"""
Personal assistant bot — Telegram + Google Gemini.

How it works, in one picture:

    You (iPhone, Telegram)  ->  Telegram servers  ->  this script on your PC
                                                          |
                                                          v
                                    Google Gemini (the "brain")  <->  tracker.py (memory)
                                                                  <->  google_calendar.py

This script keeps asking Telegram "any new messages for my bot?" (called *polling*).
When one arrives, it sends the text (or photo) to Gemini. Gemini can call the
tools in tracker.py to save or look things up, then writes the answer back to you.

Files:
    MosshyBot.py  - this file: talking to Telegram and Gemini
    tracker.py    - the database and the tools Gemini can use
    reports.py    - the monthly personal growth report
    exports.py    - the Excel file sent by /export
    google_calendar.py + connect_google.py - Google Calendar

Run it with:   .venv\\Scripts\\python MosshyBot.py
Stop it with:  Ctrl + C
"""

import asyncio
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import time

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import bills
import brain
import chat_memory
import dashboard
import drafts
import exports
import google_calendar
import jev_router
import period
import weather
import report_card
import reports
import tracker

# --- 1. Settings -------------------------------------------------------------
# Secrets live in the .env file (never in the code), so you can share this
# file safely. load_dotenv() reads .env and puts each line into os.environ.
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Two models: a fast one for everyday chat and logging (replies in ~2-3 s),
# and a smarter, slower one for monthly reports and news. Each one is also
# the backup for the other when Google is busy or a quota runs out.
FAST_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
SMART_MODEL = os.environ.get("GEMINI_SMART_MODEL", "gemini-flash-latest")
# Only this Telegram user may talk to the bot. Anyone can find a bot by name,
# so without this, strangers could use up your Gemini quota.
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "").strip()
# What the bot calls you (set OWNER_NAME in .env / Cloud Run). Empty = no name.
OWNER_NAME = os.environ.get("OWNER_NAME", "").strip()

missing = [name for name, value in [("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                                    ("GEMINI_API_KEY", GEMINI_API_KEY)] if not value]
if missing:
    raise SystemExit(f"Missing in .env: {', '.join(missing)} - see README.md, step 2.")

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # hide noisy per-request logs
# When the internet blips, Firestore's login helper prints a huge error for every
# retry, even though it then reconnects fine. Hide those; if Firestore really
# can't connect, the request itself still fails with a clear error.
logging.getLogger("grpc._plugin_wrapping").setLevel(logging.CRITICAL)
log = logging.getLogger("bot")

# --- 2. The assistant's personality ------------------------------------------
# The "system instruction" is a standing brief Gemini follows in every reply.
# Edit this freely — it's the easiest way to change how your bot behaves.
SYSTEM_INSTRUCTION = """
You are Moss, a sweet, coquette-style personal assistant and life tracker
chatting with your owner on Telegram. The owner lives in Malaysia (timezone
Asia/Kuala_Lumpur, currency RM) and may mix English and Malay. Each message starts
with the current date and time in [brackets]; use it to work out "today",
"yesterday", "last week".

Golden rule: saving, adding, changing or deleting ANYTHING happens only by calling
the tool for it. Call the tool first, then confirm from its result. Never say "I've
added / saved / logged / set / updated it" without a tool call that succeeded in
this reply, and never write a tool call out as text.

Your main jobs:
1. Tracking — whenever the owner mentions something they ate, drank, took,
   trained, spent, studied, worked on, how they slept/felt, or their period,
   save it with log_entry (one call per item). Don't ask permission, just log it.
   - Period: log "start" / "end", and symptoms (cramps, bloating, headache,
     mood, flow) as period entries with details in notes, so cycles and
     symptom patterns show up in the monthly report. Past dates count too:
     "my last period started 20 Sep" -> a "start" entry with logged_at that day.
     For "when's my next period?", "what cycle day am I?", "am I late?" use
     cycle_status. Its dates are estimates: never present the fertile window
     as birth control. The owner can also tap the "🩸 Period" button or /period.
   - Food and drinks: estimate calories using typical Malaysian portions
     (e.g. nasi lemak ayam ~650 kcal, teh tarik ~150 kcal), and ALWAYS fill in
     sugar_g, protein_g, carbs_g and fat_g too (rough estimates are fine), so
     sugar and protein can be tracked over weeks. Mention the sugar in the
     confirmation when it's notable (sweet drinks, desserts).
   - Weight: "55.2kg" or "weighed 55.2 this morning" -> category weight, amount
     55.2, unit kg. Be neutral and kind about weight, never judgmental.
   - Photos of food: identify every component, estimate the portion and calories
     of each, and log the dish. Count the calorie-dense extras people forget
     (sauces, syrup, butter, oil, sugar coating, cream). If you can't tell what
     something is, say what it might be instead of guessing silently.
   - Photos of receipts: log an expense with the total, and the shop in notes.
   - If something important is unclear (e.g. "spent 20" on what?), ask briefly.
   Confirming food and drinks: ALWAYS show what you identified, so the owner can
   spot mistakes. Name the dish, then each component with its kcal, then the
   total and today's running total (log_entry returns today's totals). E.g.:
     "🍛 Nasi lemak ayam goreng · ~800 kcal
      - coconut rice ~300 · fried chicken ~300
      - sambal ~80 · egg, peanuts, anchovies ~120
      ✅ Logged · today: 1,430 kcal, 1.25 L water
      Wrong? Just reply to correct me."
   (That's only an example of the format: estimate each photo on its own.)
   - Exercise ("brisk walk 3km 40 min", "ran 5k", "gym legs 1 hour", "pilates"):
     category workout, with distance_km, duration_min and an estimated
     burned_kcal (calories stays 0: it's only for food eaten). Sets, reps,
     weights and pace go in notes. Confirm with the numbers and today's or this
     week's totals, e.g. "🏃‍♀️ Brisk walk · 3 km · 40 min · ~180 kcal burned".
     If they tell you their regular routine (e.g. "gym Mon/Wed/Fri 7pm"), offer
     to add it to their calendar as weekly repeating events.
   For everything else (water, supplements, spending...) one short line is enough,
   e.g. "✅ Logged RM15 Grab (transport) · today: RM47 spent".
   - Budgets: "my food budget is RM400 a month" -> set_budget. When an expense
     is logged and log_entry returns "budgets", add one short line with what's
     left (e.g. "RM120 left for food this month"). If it returns
     "budget_warnings", warn gently and kindly with one practical tip, never
     scolding. "How much budget is left?" -> get_budgets.
   - Bills & subscriptions: "TNB around RM80 due on the 20th" -> add_bill
     (kind "bill": the owner pays it); "Spotify RM16.90 every 15th" ->
     add_bill (kind "subscription": charged automatically). "Paid my TNB,
     RM92" -> mark_bill_paid (don't log it separately). "Cancelled Netflix" ->
     delete_bill. They see them all with /bills.
   - Work: log what the owner did at work as category work (one concrete entry
     per piece of work), and classes/courses as study. These notes become their
     log book entries and supervisor reports (see 4b).
2. Answering questions about their history (use find_entries / summarise) and
   their goals (get_goals / set_goal).
   Corrections ("it was half", "no walnuts", "that's a crepe", "that was
   yesterday"): fix the existing entry with update_entry (its entry_id is in the
   earlier log_entry result, or use find_entries), then show the new numbers.
   Don't log it a second time. "Undo" / "remove that": delete_entry.
3. Reply suggestions — when the owner shares a message someone sent them, offer
   2-3 reply options in different tones (friendly, professional, short).
4. Tasks — the owner has a to-do list (add_task, list_tasks, complete_task,
   update_task, delete_task). When they mention something they need to do, add
   it (with a due date if one is mentioned). Habits done every day (skincare,
   make bed, read, stretch) are daily tasks: repeat="daily". Things on set
   weekdays use day codes: "log book every weekday" -> repeat="mon,tue,wed,thu,fri",
   "report every Tuesday and Friday" -> repeat="tue,fri". "Done with X" ticks
   it off with complete_task. For a messy brain-dump, add each task, then show
   a short prioritised list with the single most important next step on top.
   They tick tasks off with the "📝 Tasks" button under the chat or /tasks.
4b. Work write-ups — some tasks have a draft: "daily_log" (log book entry) or
   "work_report" (report to their supervisor); their reminder comes with a
   ready-made draft. When the owner asks for a log book entry or report, or
   answers a write-up reminder with what they did: log the work notes first,
   call work_notes for the right days (log book: that day; report: since the
   last one, by default the past week), then write the draft in the saved
   format, in plain professional English with NO emojis, kaomoji or cute words
   inside the draft, using only facts from the notes. If they send an example
   of their log book or describe the format they need, save it with
   set_draft_style. For a new write-up task ("remind me to do my log book every
   weekday at 4pm"), set draft on add_task.
   Reminders: "remind me to call mum at 3pm" / "remind me tomorrow 9am to pay
   the bill" -> add_task with remind_at 'YYYY-MM-DD HH:MM'. "Skincare every day
   at 10pm" -> a daily task with remind_at '22:00'; repeating tasks on set days
   remind only on those days. Reminders arrive within
   about 15 minutes of the time. The automatic morning briefing (8am), water
   nudges, evening check-in (9pm) and calendar event reminders (in Telegram,
   ~30 min before each event and a list of tomorrow's events at 8:30pm) are
   switched on/off with /reminders.
5. Quick log buttons — for things the owner logs often (supplements especially),
   offer to add one-tap buttons with add_quick_button. They open them with the
   "💊 Supplements" button under the chat or /supps. (Water has its own
   "💧 Water" button with +250/+500/+1000 ml.)
6. General help — answer questions, summarise text, give advice. For health
   topics be supportive and practical, and suggest a doctor for anything worrying.
6b. Second brain — the owner saves notes, links, PDFs and screenshots to ask
   about later. "Remember that...", "save this note" -> save_note in their own
   words. A link to keep ("save this", or a link sent with nothing else) ->
   save_link. A photo of notes, slides or a screenshot they ask you to save:
   write out ALL its text and save_note it. PDFs and text files sent as files
   are saved automatically. When they ask about something they saved, or ask
   something their notes could answer ("what did that article say about...",
   "explain SHAP from my notes", "what's my locker code?"): search_brain first,
   answer ONLY from what it returns, cite like [1], and end with
   "📚 Sources:" and the titles (and links). If nothing matches, say so before
   answering from general knowledge. General knowledge questions ("what's the
   difference between AI and ML?") are answered directly, without searching,
   unless they mention their notes, lectures or something they saved.
   "What have I saved?" -> list_brain (or
   /brain). "Forget X" -> forget_brain_item.

{calendar}
{owner}
Personality: coquette and soft. Sweet, girly and encouraging, like a doting best
friend: bows, ribbons, pastel pink, florals, pearls and lace. Use these emojis
for decoration: 🎀 🌸 💗 🩷 🤍 🍓 🫧 🌷 ✨ 💌 🩰 and end most messages with ONE
cute kaomoji, choosing from: (˶ᵔ ᵕ ᵔ˶)  (｡•ᴗ•｡)♡  ₍ᐢ. .ᐢ₎  ʚ♡ɞ  ♡⸜(˶˃ ᵕ ˂˶)⸝♡
(っ˘ω˘ς)  ૮ ˶ᵔ ᵕ ᵔ˶ ა  (ᵕ—ᴗ—)  ( ˶ˆᗜˆ˵ )  (๑>◡<๑)  (ˊᗜˋ*)  ( ´ ▽ ` )ﾉ  (,,>﹏<,,)
Celebrate wins (goal reached, workout done, all supplements taken) a little extra.
Keep the cuteness as decoration: the facts and numbers must stay clear and easy
to read, and replies stay short. Anything you save (task titles, quick
buttons, logged items, calendar events) uses the owner's own plain words, never
fancy names. The one exception: start each task title with ONE fitting emoji,
e.g. "🧴 Skincare", "🪥 Tooth brush", "🐱 Vet appointment for cat". Be gentle and caring rather than bubbly when the
owner is unwell, stressed, sad, in pain (e.g. period cramps) or worried about
money, and drop the kaomoji for anything serious.

Style: concise and phone-friendly. Telegram shows plain text, so do NOT use
Markdown symbols like ** or #. Use short lines, dashes and emojis for structure.
""".strip()

CALENDAR_JOB = """7. Calendar — the owner's Google Calendar is connected. When they mention a
   class, appointment, meeting, deadline or event with a date, add it with
   add_calendar_event. Birthdays and anniversaries: yearly all-day events with a
   reminder the day before. Weekly classes: repeat weekly (ask until when if
   unknown). Use list_calendar_events for "what's on...", "am I free...".
   To move an event, find it first. Confirm with the weekday, date and time, e.g.
   "📅 Added: Python class, Thu 16 Oct, 2:00-3:00pm".
   Deleting: for a repeating event, first ask whether they mean just that date
   or every repeat. delete_calendar_event then shows the owner Yes/No buttons;
   tell them to tap one. Never say an event is deleted - only the button does that.
"""
NO_CALENDAR = """7. Calendar — not connected yet. If asked to add events, say the owner can
   connect Google Calendar by following README.md, "Connecting Google Calendar".
"""
# The calendar tools are only switched on once you've connected Google (token.json exists).
CALENDAR_ON = google_calendar.is_connected()
OWNER_LINE = (f"The owner's name is {OWNER_NAME}. Call them {OWNER_NAME} regularly (greetings, "
              f"celebrations, check-ins), mixed with sweet nicknames.\n" if OWNER_NAME else "")
SYSTEM_INSTRUCTION = SYSTEM_INSTRUCTION.format(calendar=CALENDAR_JOB if CALENDAR_ON else NO_CALENDAR,
                                               owner=OWNER_LINE)

# Give up on a Gemini request after 45 seconds instead of retrying quietly for
# minutes, so we can switch to the backup model (see send_to_gemini below).
gemini = genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(
    timeout=45_000, retry_options=types.HttpRetryOptions(attempts=1)))
# tools=... hands Gemini the functions from tracker.py. The library runs them
# automatically when Gemini asks, and feeds the results back to Gemini.
# thinking_level="low": Gemini "thinks" less before answering, which makes
# everyday logging much faster. Reports still use full thinking.
chat_config = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    tools=(tracker.TOOLS + period.TOOLS + drafts.TOOLS + bills.TOOLS + weather.TOOLS + brain.TOOLS
           + (google_calendar.TOOLS if CALENDAR_ON else [])),
    thinking_config=types.ThinkingConfig(thinking_level="low"))

# One conversation per Telegram chat, so Gemini remembers what you said earlier.
# (Kept in memory only: restarting the script starts the chat fresh, but
# everything you logged is safe in the database.)
conversations: dict[int, genai.chats.AsyncChat] = {}

# Every message re-sends the whole conversation to Gemini, and you pay for all
# of it. So only the most recent part is kept (~15 exchanges). Your logs are
# safe either way: they live in the database, not in the chat.
MAX_HISTORY = 40


def get_conversation(chat_id: int):
    if chat_id not in conversations:
        # Pick up where we left off: the recent conversation is saved in Firestore.
        conversations[chat_id] = gemini.aio.chats.create(model=FAST_MODEL, config=chat_config,
                                                         history=chat_memory.load(chat_id))
    history = conversations[chat_id].get_history(curated=True)
    if len(history) > MAX_HISTORY:
        # Cut at one of your own text messages, so we never keep a tool
        # result whose tool request was cut off (Gemini would reject that).
        start = next((i for i in range(len(history) - MAX_HISTORY, len(history))
                      if history[i].role == "user"
                      and any(part.text for part in history[i].parts or [])), len(history))
        conversations[chat_id] = gemini.aio.chats.create(model=FAST_MODEL, config=chat_config,
                                                         history=history[start:])
    return conversations[chat_id]


def remember(chat_id: int, asked: str, answer: str) -> None:
    """Add a news digest or report to the chat, so you can follow up on it
    ("add the INNOFEST one to my calendar", "why did my spending go up?")."""
    history = get_conversation(chat_id).get_history(curated=True) + [
        types.Content(role="user", parts=[types.Part(text=asked)]),
        types.Content(role="model", parts=[types.Part(text=answer)]),
    ]
    conversations[chat_id] = gemini.aio.chats.create(model=FAST_MODEL, config=chat_config,
                                                     history=history)
    chat_memory.save(chat_id, history)


# --- 3. Helpers ---------------------------------------------------------------
def is_allowed(update: Update) -> bool:
    return bool(ALLOWED_USER_ID) and str(update.effective_user.id) == ALLOWED_USER_ID


async def reject_if_not_owner(update: Update) -> bool:
    """Returns True (and explains why) when the sender isn't the owner."""
    if is_allowed(update):
        return False
    if not ALLOWED_USER_ID:
        # First-time setup: tell the owner their ID so they can put it in .env
        await update.message.reply_text(
            f"👋 Setup step: your Telegram user ID is {update.effective_user.id}\n\n"
            "Put it in the .env file as ALLOWED_USER_ID, then restart the bot."
        )
    else:
        await update.message.reply_text("Sorry, this is a private bot 🎀")
    return True


async def send_long(bot, chat_id: int | str, text: str) -> None:
    """Telegram caps messages at 4096 characters, so split long answers."""
    for start in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[start : start + 4000])


@asynccontextmanager
async def typing_indicator(chat):
    """Show "typing..." the whole time we wait. Telegram hides it after
    5 seconds, so a small background task repeats it every 4 seconds."""
    async def keep_typing():
        while True:
            await chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()


def with_timestamp(text: str) -> str:
    """Gemini doesn't know today's date, so every message tells it."""
    return f"[{tracker.now():%A %d %B %Y, %H:%M}]\n{text}"


def config_for(tool_names: set[str] | None) -> types.GenerateContentConfig:
    """Gemini's settings for one message: every tool (None), only the named
    tools (picked by Jev, see jev_router.py), or no tools at all (empty set)."""
    if tool_names is None:
        return chat_config
    tools = [tool for tool in chat_config.tools if tool.__name__ in tool_names]
    return chat_config.model_copy(update={"tools": tools or None})


def previous_reply(chat_id: int) -> str:
    """My last message in this chat (gives Jev context for follow-ups like "yes")."""
    for content in reversed(get_conversation(chat_id).get_history(curated=True)):
        if content.role == "model":
            text = "".join(part.text or "" for part in content.parts or [])
            if text:
                return text
    return ""


# Safety net: sometimes Gemini SAYS it did something ("📅 Added: Aina's birthday")
# without calling the tool, so nothing was saved. The report card caught this
# (report_card.py). Such a reply gets sent back once, with every tool, to do it for real.
CLAIMS_DONE = re.compile(
    r"\b(i'?ve|i have|i just|just)\b[^.!?\n]{0,30}\b(added|saved|logged|set|updated|changed|moved|"
    r"deleted|removed|ticked|marked)\b"
    r"|\b(i'?ll|i will|let me)\b[^.!?\n]{0,20}\b(add|save|log|set|update|change|move|delete|remove|tick|mark)\b"
    r"|\b(adding|saving|logging|setting|updating|deleting|removing)\b[^.!?\n]{0,25}\b(right away|now|for you)\b"
    r"|📅 added|✅ logged|✅ saved|✅ added", re.IGNORECASE)
DO_IT_NOW = ("(System note: your reply says you did something, but no tool was called, so nothing was "
             "saved or changed. Call the right tool now, then confirm. If it was already done earlier "
             "in our chat, just answer again without calling anything.)")


def called_a_tool(contents) -> bool:
    return any(part.function_call for content in contents if content.role == "model"
               for part in content.parts or [])


def claims_without_doing(text: str | None, called: bool) -> bool:
    return not called and bool(CLAIMS_DONE.search(text or ""))


async def send_to_gemini(chat_id: int, message, tool_names: set[str] | None = None):
    """Send to the fast model; if it's busy or down, retry once with the smart model.
    tool_names limits which tools Gemini gets for this message (None = all)."""
    chat = get_conversation(chat_id)
    config = config_for(tool_names)
    before = len(chat.get_history(curated=True))
    try:
        response = await chat.send_message(message, config=config)
    except Exception as error:
        log.warning("%s failed (%s) - trying %s", FAST_MODEL, error, SMART_MODEL)
        history = chat.get_history(curated=True)  # the conversation so far
        chat = gemini.aio.chats.create(model=SMART_MODEL, config=chat_config, history=history)
        before = len(history)
        # The failed attempt may have saved something before it broke, so warn
        # Gemini not to log the same thing twice.
        note = "(System note: a previous attempt at this message failed partway. Before logging, " \
               "check today's entries with find_entries and don't log anything twice.)"
        message = [*message, note] if isinstance(message, list) else [message, note]
        response = await chat.send_message(message, config=config)
    if claims_without_doing(response.text, called_a_tool(chat.get_history(curated=True)[before:])):
        log.info("Reply claimed an action without a tool call - asking again")
        response = await chat.send_message(DO_IT_NOW, config=chat_config)
    # Next message goes on with the fast model, carrying the whole conversation.
    conversations[chat_id] = chat
    # Carry on from the saved, photo-free copy, so photos aren't re-sent with every message.
    history = chat_memory.save(chat_id, conversations[chat_id].get_history(curated=True))
    conversations[chat_id] = gemini.aio.chats.create(model=FAST_MODEL, config=chat_config, history=history)
    return response


async def ask_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE, message,
                     route_text: str | None = None, tool_names: set[str] | None = None,
                     header: str = "") -> None:
    """Send a message (text, or a list of text + images) to Gemini and reply with the answer.
    With route_text, Jev first decides which tools Gemini needs (jev_router.py).
    header goes above the reply (e.g. what I heard in a voice note)."""
    try:
        async with typing_indicator(update.message.chat):
            chat_id = update.effective_chat.id
            if route_text is not None:
                tool_names = await jev_router.pick_tools(route_text, previous_reply(chat_id))
            response = await send_to_gemini(chat_id, message, tool_names)
        await send_long(context.bot, update.effective_chat.id,
                        header + (response.text or "(Gemini returned an empty answer)"))
        await ask_to_confirm_deletes(context.bot, update.effective_chat.id)
    except Exception as error:  # e.g. bad API key, free-tier limit reached
        log.exception("Gemini request failed")
        await update.message.reply_text(f"(,,>﹏<,,) oops, my brain hiccuped: {error}\nTry again in a moment? 🎀")


async def ask_to_confirm_deletes(bot, chat_id) -> int:
    """If Gemini asked to delete calendar events, show Yes/No buttons for each.
    Returns how many are waiting for a tap."""
    count = 0
    while google_calendar.new_delete_requests:
        code, event = google_calendar.new_delete_requests.popitem()
        google_calendar.wait_for_tap(code, event)
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete:{code}"),
            InlineKeyboardButton("❌ No, keep it", callback_data=f"keep:{code}"),
        ]])
        await bot.send_message(chat_id, "🗑️ Delete this event?\n\n" + google_calendar.describe(event),
                               reply_markup=buttons)
        count += 1
    return count


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs when you tap any button under a message. Each button carries a short
    code like "quick:3" or "delete:1" saying what it's for."""
    query = update.callback_query
    if not is_allowed(update):
        await query.answer()
        return
    action, code = query.data.split(":", 1)
    if action in ("quick", "quickall", "water"):
        await on_quick_button(query, action, code)
    elif action == "task":
        await on_task_button(query, code)
    elif action == "taskdone":
        await on_reminder_done(query, code)
    elif action in ("bill", "billpaid"):
        await on_bill_button(query, action, code)
    elif action == "dismiss":
        await query.answer()
        await delete_quietly(query.get_bot(), query.message.chat.id, query.message.message_id)
    elif action == "period":
        await period.on_button(query, code)
    elif action == "remind":
        import reminders  # imported here: reminders.py itself imports this file
        await reminders.on_toggle(query, code)
    else:
        await query.answer()  # stops the button's loading spinner
        await on_delete_button(query, action, code)


async def on_delete_button(query, action: str, code: str) -> None:
    """✅ Yes / ❌ No under a "Delete this event?" message."""
    if action == "keep":
        event = google_calendar.stop_waiting(code)
        await query.edit_message_text("🤍 Kept: " + (event["title"] if event else "nothing changed."))
        return
    try:
        # The calendar library isn't async, so run it on a helper thread.
        event = await asyncio.to_thread(google_calendar.really_delete, code)
    except Exception as error:
        log.exception("Calendar delete failed")
        await query.edit_message_text(f"⚠️ Couldn't delete: {error}")
        return
    await query.edit_message_text(("🗑️ Deleted: " + google_calendar.describe(event)) if event else
                                  "⌛ This button has expired. Ask me to delete it again.")


# --- Quick log panels: one tap logs, no Gemini needed (instant and free) -------
WATER_BUTTONS = (250, 500, 1000)  # ml


def supplements_panel() -> tuple[str, InlineKeyboardMarkup]:
    """A daily checklist: one button per supplement, ✓ once taken today."""
    done = tracker.logged_today()
    buttons, left = [], 0
    for item in tracker.list_quick_buttons():
        taken = (item["category"], item["item"].lower()) in done
        dose = f" {item['amount']:g}{item['unit'] or ''}" if item["amount"] else ""
        buttons.append(InlineKeyboardButton(("✅ " if taken else "⬜ ") + item["item"] + dose,
                                            callback_data=f"quick:{item['id']}"))
        left += not taken
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]  # 2 buttons per row
    if left > 1:
        keyboard.append([InlineKeyboardButton(f"🎀 Took all the rest ({left})", callback_data="quickall:")])

    if not buttons:
        text = ('💊 Supplements\n\nNo buttons yet. Tell me "add fish oil and vitamin C '
                'to my quick buttons" to make some.')
    elif left:
        text = (f"💊 Supplements · {len(buttons) - left}/{len(buttons)} taken today 🌸\n"
                "Tap to tick, tap again to untick ♡")
    else:
        text = "💊 All supplements taken today! ʚ♡ɞ\nTap one to untick it."
    return text, InlineKeyboardMarkup(keyboard)


def water_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Today's water with a progress bar, plus +ml buttons and an undo."""
    total = tracker.water_today()
    goal = next((g["target"] for g in tracker.get_goals() if g["name"] == "water"), None)
    text = f"💧 Water today: {total:,.0f} ml"
    if goal:
        filled = min(10, int(total / goal * 10))
        text += f" of {goal:,.0f} ml\n{'🩷' * filled}{'🤍' * (10 - filled)} {total / goal:.0%}"
        if total >= goal:
            text += "\n🎉 Goal reached! So proud of you ♡⸜(˶˃ ᵕ ˂˶)⸝♡"
    keyboard = [[InlineKeyboardButton(f"+{ml} ml", callback_data=f"water:{ml}") for ml in WATER_BUTTONS]]
    if total:
        keyboard.append([InlineKeyboardButton("↩️ Undo last", callback_data="water:undo")])
    return text, InlineKeyboardMarkup(keyboard)


async def show_supplements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_panel(update, context, "supps")


async def show_water(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_panel(update, context, "water")


async def show_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_panel(update, context, "period")


async def on_quick_button(query, action: str, code: str) -> None:
    if action == "water":
        if code == "undo":
            undone = tracker.undo_latest_today("water")
            note = f"↩️ Removed {undone['amount']:g} ml" if undone else "Nothing to undo"
        else:
            tracker.log_entry("water", "water", float(code), "ml")
            note = f"💧 +{code} ml ♡"
        text, buttons = water_panel()
    else:
        done = tracker.logged_today()
        items = tracker.list_quick_buttons()
        if action == "quickall":
            names = [item["item"] for item in items
                     if (item["category"], item["item"].lower()) not in done
                     and tracker.quick_log(item["id"])]
            note = "🎀 Logged " + ", ".join(names) + " ♡" if names else "Nothing left to log"
        else:
            item = next((item for item in items if item["id"] == code), None)
            if item is None:
                note = "That button was removed"
            elif (item["category"], item["item"].lower()) in done:
                tracker.undo_latest_today(item["category"], item["item"])  # tapped a ✅: untick
                note = f"↩️ Unticked {item['item']}"
            else:
                tracker.quick_log(item["id"])
                note = f"🎀 Logged {item['item']} ♡"
        text, buttons = supplements_panel()
    await query.answer(note)  # a small pop-up on your screen
    try:
        await query.edit_message_text(text, reply_markup=buttons)  # refresh ticks / progress
    except BadRequest:
        pass  # Telegram refuses an "edit" that changes nothing - that's fine


def tasks_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Today's to-do list as tick boxes: daily habits first, then one-off tasks."""
    shown, later = tracker.tasks_for_today()
    today_ = tracker.today()
    keyboard = []
    for task, done in shown:
        label = ("✅ " if done else "⬜ ") + ("🔁 " if task["repeat"] else "") + task["title"]
        if not done and task["due"] and task["due"] < today_:
            label += " (overdue)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"task:{task['task_id']}")])
    habits_done = tracker.repeating_done_today()  # ticked 🔁 tasks have left the list
    done_count, total = sum(done for _, done in shown) + habits_done, len(shown) + habits_done
    if not total:
        text = ('📝 No tasks for today 🌷\nTell me things like "pay TNB bill by Friday" '
                'or "skincare every day" and I\'ll add them ♡')
    elif done_count == total:
        text = f"📝 All {total} tasks done today! You're amazing (๑>◡<๑) 🎉"
    else:
        text = f"📝 Today's tasks · {done_count}/{total} done 🌸\nTap to tick ♡"
    if habits_done and done_count != total:
        text += f"\n🔁 {habits_done} habit{'s' if habits_done > 1 else ''} done ✨"
    if later:
        text += f"\n🗓️ +{later} planned for later days"
    return text, InlineKeyboardMarkup(keyboard)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_panel(update, context, "tasks")


def bills_panel() -> tuple[str, InlineKeyboardMarkup]:
    """🧾 Bills & subscriptions: tick a bill when it's paid (subscriptions tick themselves)."""
    today = tracker.now().date()
    keyboard, total, paid_total = [], 0.0, 0.0
    for bill in bills.all_bills():
        due = bills.shown_due(bill, today)
        paid = bills.is_paid(bill, due)
        if due.strftime("%Y-%m") == today.strftime("%Y-%m"):
            total += bill["amount_rm"]
            paid_total += bill["amount_rm"] if paid else 0
        label = (("✅ " if paid else "⬜ ") + ("🔁 " if bill["kind"] == "subscription" else "")
                 + f"{bill['name']} · RM{bill['amount_rm']:g} · {due.day} {due:%b}")
        if not paid and due < today:
            label += " (overdue)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"bill:{bill['bill_id']}")])
    if not keyboard:
        return ('🧾 No bills yet 🌷\nTell me things like "TNB around RM80 due on the 20th" or '
                '"Spotify RM16.90 every 15th" ♡'), InlineKeyboardMarkup([])
    return (f"🧾 Bills & subscriptions\nThis month: RM{paid_total:.2f} of RM{total:.2f} paid 🌸\n"
            "🔁 = charged automatically. Tap a bill when you've paid it ♡"), InlineKeyboardMarkup(keyboard)


async def open_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dashboard: a one-time login link to the web dashboard (dashboard.py)."""
    if await reject_if_not_owner(update):
        return
    webhook = (await context.bot.get_webhook_info()).url
    if not webhook:
        await update.message.reply_text("The dashboard only works with the cloud bot (Cloud Run) 🎀")
        return
    # DASHBOARD_URL: the friendlier web address (Firebase Hosting), else the Cloud Run one.
    site = os.environ.get("DASHBOARD_URL", "").strip().rstrip("/") or webhook.rsplit("/telegram", 1)[0]
    link = site + "/dashboard/login?t=" + dashboard.new_login_link_token()
    await update.message.reply_text(
        f"📊 Your dashboard 🎀\nThe button works once, for {dashboard.LINK_MINUTES} minutes, and keeps you "
        f"logged in on that browser for {dashboard.SESSION_DAYS} days ♡\n"
        "Tip: long-press it → Open in Safari, then Share → Add to Home Screen.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎀 Open dashboard", url=link)]]))


async def show_bills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_panel(update, context, "bills")


async def on_bill_button(query, action: str, bill_id: str) -> None:
    """A bill in the 🧾 panel (tap = paid, tap again = undo), or ✅ Paid under a reminder."""
    bill = next((b for b in bills.all_bills() if b["bill_id"] == bill_id), None)
    bot, chat_id = query.get_bot(), query.message.chat.id
    if bill is None:
        await query.answer("That bill is gone")
        return
    today = tracker.now().date()
    if action == "bill" and bills.is_paid(bill, bills.shown_due(bill, today)):
        bills.unpay(bill_id)
        await query.answer(f"↩️ {bill['name']} unmarked")
    else:
        result = bills.mark_bill_paid(bill_id)
        await query.answer(f"✅ {bill['name']} paid, RM{result.get('amount_rm', bill['amount_rm']):g} logged ♡"
                           if result.get("paid") else f"{bill['name']} is already paid ♡")
    await delete_remembered(bot, chat_id, f"bill_{bill_id}")  # its reminder isn't needed any more
    if action == "bill":
        text, buttons = bills_panel()
        try:
            await query.edit_message_text(text, reply_markup=buttons)
        except BadRequest:
            pass
    else:
        await delete_quietly(bot, chat_id, query.message.message_id)
        await refresh_panel(bot, chat_id, "bills")


def _find_task(task_id: str) -> dict | None:
    return next((t for t in tracker.list_tasks(include_finished=True) if t["task_id"] == task_id), None)


async def on_task_button(query, task_id: str) -> None:
    """A tick box in the 📝 Tasks panel: tick, or untick after a mis-tap."""
    task = _find_task(task_id)
    if task is None:
        note = "That task is gone"
    elif tracker.task_is_done(task):  # already ticked: untick (mis-tap)
        tracker.untick_task(task_id)
        note = f"↩️ Unticked {task['title']}"
    else:
        tracker.complete_task(task_id)
        note = f"✅ {task['title']} done! ♡"
        # Its reminder (if one is still showing) isn't needed any more.
        await delete_remembered(query.get_bot(), query.message.chat.id, f"reminder_{task_id}")
    await query.answer(note)
    text, buttons = tasks_panel()
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except BadRequest:
        pass  # nothing changed on screen


async def on_reminder_done(query, task_id: str) -> None:
    """✅ Done under a reminder: tick the task, make the reminder disappear, and
    update the 📝 Tasks panel that's already in the chat."""
    task = _find_task(task_id)
    if task is not None and not tracker.task_is_done(task):
        tracker.complete_task(task_id)
    await query.answer(f"✅ {task['title']} done! ♡" if task else "That task is gone")
    bot, chat_id = query.get_bot(), query.message.chat.id
    # Write-up reminders too: Done means the draft has been copied and sent.
    await delete_quietly(bot, chat_id, query.message.message_id)
    forget_message(f"reminder_{task_id}")
    await refresh_panel(bot, chat_id, "tasks")


# --- Keeping the chat tidy ------------------------------------------------------
# Telegram lets a bot delete messages in a private chat (its own and yours) for
# up to 48 hours. The bot remembers the message number of each panel and each
# reminder in Firestore (settings/chat_messages), so it can replace, update or
# remove them instead of piling up new ones.
PANELS = {"supps": supplements_panel, "water": water_panel, "tasks": tasks_panel, "period": period.panel,
          "bills": bills_panel}
DISMISS = InlineKeyboardMarkup([[InlineKeyboardButton("👌 Got it", callback_data="dismiss:")]])


def _chat_messages():
    return tracker._collection("settings").document("chat_messages")


def remembered_message(key: str) -> int | None:
    saved = _chat_messages().get()
    return (saved.to_dict() or {}).get(key) if saved.exists else None


def remember_message(key: str, message_id: int) -> None:
    _chat_messages().set({key: message_id}, merge=True)


def forget_message(key: str) -> None:
    _chat_messages().set({key: None}, merge=True)


async def delete_quietly(bot, chat_id, message_id) -> None:
    """Delete a message; if it's already gone (or older than 48 hours), never mind."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass


async def delete_remembered(bot, chat_id, key: str) -> None:
    await delete_quietly(bot, chat_id, remembered_message(key))
    forget_message(key)


async def send_panel(bot, chat_id, kind: str, header: str = ""):
    """Send a fresh panel ("supps", "water", "tasks", "period") and remove the
    previous one of the same kind, so the chat only ever shows one of each."""
    old = remembered_message(kind)
    text, buttons = PANELS[kind]()
    message = await bot.send_message(chat_id, header + text, reply_markup=buttons)
    remember_message(kind, message.message_id)
    if old != message.message_id:
        await delete_quietly(bot, chat_id, old)
    return message


async def refresh_panel(bot, chat_id, kind: str) -> None:
    """Update the panel that's already in the chat, in place."""
    message_id = remembered_message(kind)
    if not message_id:
        return
    text, buttons = PANELS[kind]()
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=buttons)
    except TelegramError:
        pass  # unchanged, or the message is gone


async def show_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    """Open a panel from a button under the chat or a /command, tidily: the old
    panel and your button tap are removed, so only the fresh panel stays."""
    if await reject_if_not_owner(update):
        return
    chat_id = update.effective_chat.id
    await send_panel(context.bot, chat_id, kind)
    await delete_quietly(context.bot, chat_id, update.message.message_id)


async def ask_gemini_once(prompt: str, config: types.GenerateContentConfig) -> str:
    """A one-off question outside the chat (reports and news) for the smart model."""
    try:
        response = await gemini.aio.models.generate_content(model=SMART_MODEL, contents=prompt,
                                                            config=config)
    except Exception as error:
        log.warning("%s failed (%s) - trying %s", SMART_MODEL, error, FAST_MODEL)
        response = await gemini.aio.models.generate_content(model=FAST_MODEL, contents=prompt,
                                                            config=config)
    return response.text or "(Gemini returned an empty answer)"


# --- 4. Reports and news ------------------------------------------------------
async def write_report(year: int, month: int) -> str:
    prompt = reports.build_report_prompt(year, month)
    if prompt is None:
        return f"📭 Nothing logged for {reports.MONTH_NAMES[month - 1]} {year} yet."
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
    # The system instruction promises a [date] at the start of every message;
    # without one, Gemini invents a timestamp and prints it in the report.
    return await ask_gemini_once(with_timestamp(prompt), config)


async def write_weekly_report(day=None) -> str:
    """The report for the week (Monday-Sunday) containing `day` (default: today)."""
    day = day or tracker.now().date()
    prompt = reports.build_weekly_prompt(day)
    if prompt is None:
        return "📭 Nothing logged this week yet. Tell me what you eat, drink and do, and I'll report on it ♡"
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
    return await ask_gemini_once(with_timestamp(prompt), config)


NEWS_PROMPT = """
Today is {today}. Search the web and give me a short digest:
1. The 5-7 most important AI and tech news stories from the last 7 days,
   especially new AI models, tools and features I could try. One or two lines each.
2. Upcoming AI/tech events in Malaysia (KL, Selangor, Cyberjaya, Penang, online)
   in the next 30 days: name, date, place, whether free, and the link.
   The link must be the event's own page, never just a homepage like
   eventbrite.com or lu.ma. If you don't have the exact page, leave the link out.
{focus}
Plain text, emojis as bullets, no Markdown. Only include items you found in search results.
"""


async def write_news(topic: str = "") -> str:
    focus = f"Focus especially on: {topic}" if topic else ""
    # google_search lets Gemini look things up on the web before answering.
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    try:
        return await ask_gemini_once(NEWS_PROMPT.format(today=f"{tracker.now():%d %B %Y}",
                                                        focus=focus), config)
    except genai.errors.ClientError as error:
        if error.code != 429:
            raise
        # 429 = "quota used up". Web search usually needs billing turned on for
        # your API key (your Google AI Pro cloud credits can pay for it).
        return ("⚠️ Gemini's web search quota is used up for this API key.\n"
                "It usually needs billing enabled — see README, 'Turning on web search'.")


# --- 5. Command handlers (messages starting with /) ---------------------------
HELP_TEXT = f"""Hi {OWNER_NAME or "there"}! I'm Moss, your little life organiser 🎀
Just text me what you ate, drank, did or spent, send a photo, or a voice note 🎙️ ♡

/today - today's summary vs your goals
/tasks - your to-do list (tap to tick)
/supps - supplement checklist
/water - log water in one tap
/bills - bills & subscriptions
/period - cycle tracker
/report - monthly report (/report week for this week)
/dashboard - charts of everything, in your browser
/export - Excel file of your habits & spending
/news - AI & tech news + Malaysian events
/brain - your saved notes, links & PDFs (just ask me about them)
/reportcard - how well I understand you, from my latest exam
/reminders - switch reminders on/off
/reset - clear chat memory (logs stay saved)"""

# The menu Telegram shows when you type "/" (set by telegram_webhook.py).
COMMANDS = [
    ("today", "today's summary vs your goals 🌸"),
    ("tasks", "your to-do list, tap to tick 📝"),
    ("supps", "supplement checklist 💊"),
    ("water", "log water in one tap 💧"),
    ("bills", "bills & subscriptions 🧾"),
    ("period", "cycle tracker 🩸"),
    ("report", "monthly report · /report week for this week 📊"),
    ("dashboard", "charts of everything, in your browser 🌸"),
    ("export", "Excel file of habits & spending 🎀"),
    ("news", "AI & tech news + Malaysian events 🫧"),
    ("brain", "your saved notes, links & PDFs 📚"),
    ("reportcard", "how well Moss understands you 📊"),
    ("reminders", "switch reminders on/off 🔔"),
    ("reset", "clear chat memory (logs stay) 🤍"),
    ("help", "what I can do ♡"),
]


# Buttons that stay under the typing box. Tapping one sends its text as a message.
SUPPS, WATER, PERIOD, TASKS, TODAY = "💊 Supps", "💧 Water", "🩸 Period", "📝 Tasks", "🌸 Today"
MAIN_KEYBOARD = ReplyKeyboardMarkup([[SUPPS, WATER, PERIOD], [TASKS, TODAY]], resize_keyboard=True,
                                    is_persistent=True)
# Older versions of the buttons, still on your phone until /start refreshes them.
OLD_BUTTONS = {"💊 Supplements": SUPPS, "⚡ Quick log": SUPPS, "📊 Today": TODAY}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    await update.message.reply_text(HELP_TEXT, reply_markup=MAIN_KEYBOARD)


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    dump = " ".join(context.args)  # everything typed after "/tasks"
    if not dump:
        await show_tasks(update, context)
        return
    await ask_gemini(update, context, with_timestamp(
        f"Add these to my task list, then show them as a prioritised to-do list:\n{dump}"))


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    await ask_gemini(update, context, with_timestamp(
        "Give me today's summary: everything I logged, totals, progress vs my goals, and "
        "my monthly budgets (what's left) if I have any. "
        "End with one small, encouraging suggestion for the rest of the day."))


def parse_month(args: list[str]) -> tuple[int, int] | None:
    """No argument -> this month. "2026-09" -> (2026, 9). Anything invalid -> None."""
    if not args:
        return tracker.now().year, tracker.now().month
    try:
        year, month = (int(part) for part in args[0].split("-"))
        reports.month_range(year, month)  # raises an error if the month is invalid
        return year, month
    except ValueError:
        return None


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    weekly = bool(context.args) and context.args[0].lower() in ("week", "weekly")
    month = None if weekly else parse_month(context.args)
    if not weekly and month is None:
        await update.message.reply_text("Usage: /report (this month), /report week, or /report 2026-09")
        return
    await update.message.reply_text("📊 Writing your report... give me a moment (˶ᵔ ᵕ ᵔ˶)")
    try:
        async with typing_indicator(update.message.chat):
            text = await write_weekly_report() if weekly else await write_report(*month)
        await send_long(context.bot, update.effective_chat.id, text)
        remember(update.effective_chat.id, "Show me my growth report.", text)
    except Exception as error:
        log.exception("Report failed")
        await update.message.reply_text(f"⚠️ Report error: {error}")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    await update.message.reply_text("🔎 Looking up the latest for you... 🫧")
    try:
        async with typing_indicator(update.message.chat):
            text = await write_news(" ".join(context.args))
        await send_long(context.bot, update.effective_chat.id, text)
        remember(update.effective_chat.id, "Show me the latest AI & tech news and events.", text)
    except Exception as error:
        log.exception("News failed")
        await update.message.reply_text(f"⚠️ News error: {error}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    if context.args and context.args[0].lower() == "all":
        start, end, name = tracker.first_entry_date(), tracker.now().date().isoformat(), "all"
    else:
        month = parse_month(context.args)
        if month is None:
            await update.message.reply_text("Usage: /export, /export 2026-09 or /export all")
            return
        (start, end), name = reports.month_range(*month), f"{month[0]}-{month[1]:02d}"
    file = exports.build_excel(start, end) if start else None
    if file is None:
        await update.message.reply_text("📭 Nothing logged for that period yet.")
        return
    await update.message.reply_document(file, filename=f"MosshyBot-{name}.xlsx",
                                        caption="📊 Your logs, all wrapped up 🎀 Tap to open ♡")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    chat_id = update.effective_chat.id
    conversations.pop(chat_id, None)
    chat_memory.forget(chat_id)
    await clear_chat(context.bot, chat_id, update.message.message_id)
    await context.bot.send_message(chat_id, "🫧 Fresh start! Chat and memory cleared, your logs are all still saved 🤍",
                                   reply_markup=MAIN_KEYBOARD)


async def clear_chat(bot, chat_id: int, last_id: int, how_far_back: int = 3000) -> None:
    """Delete the chat's messages (yours and the bot's), newest first. Message
    numbers in a private chat count up, so this tries every number below the
    /reset message; Telegram skips ones that are gone or older than 48 hours."""
    for top in range(last_id, max(0, last_id - how_far_back), -100):
        try:
            await bot.delete_messages(chat_id, list(range(top, max(0, top - 100), -1)))
        except TelegramError as error:
            log.info("Couldn't clear messages below %s: %s", top, error)
    _chat_messages().set({})  # the remembered panels and reminders are gone too


# --- 6. Normal messages -------------------------------------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    text = OLD_BUTTONS.get(update.message.text, update.message.text)
    if text == SUPPS:
        await show_supplements(update, context)
    elif text == WATER:
        await show_water(update, context)
    elif text == TASKS:
        await show_tasks(update, context)
    elif text == PERIOD:
        await show_period(update, context)
    elif text == TODAY:
        await today(update, context)
    elif update.message.forward_origin:
        # A forwarded message is someone else's words -> suggest replies (no tools needed).
        await ask_gemini(update, context,
                         with_timestamp(f"Someone sent me this message. Suggest replies:\n\n{text}"),
                         tool_names=set())
    else:
        # Jev decides which tools Gemini needs for this message (jev_router.py).
        text = replying_to(update) + text
        await ask_gemini(update, context, with_timestamp(text), route_text=text)


def replying_to(update: Update) -> str:
    """When you swipe-reply to one of the bot's messages (e.g. to correct a
    calorie estimate), tell Gemini which message you mean."""
    original = update.message.reply_to_message
    if original is None or not (original.text or original.caption):
        return ""
    return f'(Replying to: "{(original.text or original.caption)[:600]}")\n'


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_owner(update):
        return
    # Telegram keeps each photo in several sizes; the last one is the largest.
    photo_file = await update.message.photo[-1].get_file()
    image = types.Part.from_bytes(data=bytes(await photo_file.download_as_bytearray()),
                                  mime_type="image/jpeg")
    caption = update.message.caption or (
        "If this is food or a drink, identify it, estimate calories and log it. "
        "If it's a receipt, log the expense. Otherwise just tell me what you see.")
    # Photos are food, receipts, or notes to save: no need to ask Jev.
    await ask_gemini(update, context, [image, with_timestamp(caption)],
                     tool_names=jev_router.GROUPS["log"] | jev_router.GROUPS["settings"] | {"save_note"})


# --- Files: saved to the second brain (brain.py) ------------------------------------
MAX_FILE_MB = 20  # the most Telegram lets a bot download


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A PDF or text file: its text goes into the second brain, then Moss says what it's about."""
    if await reject_if_not_owner(update):
        return
    document = update.message.document
    if (document.file_size or 0) > MAX_FILE_MB * 1024 * 1024:
        await update.message.reply_text(f"📄 That file is too big for me (max {MAX_FILE_MB} MB) (,,>﹏<,,)")
        return
    name = document.file_name or "document"
    async with typing_indicator(update.message.chat):
        try:
            file = await document.get_file()
            data = bytes(await file.download_as_bytearray())
            text, pages = await brain.read_document(data, name, document.mime_type or "")
            kind = "pdf" if name.lower().endswith(".pdf") else "file"
            title = name.rsplit(".", 1)[0].replace("_", " ")
            # Saving waits on Gemini and Firestore: run it beside the bot so it stays responsive.
            saved = await asyncio.to_thread(brain.save, kind, title, text, f"file:{document.file_unique_id}", pages)
        except ValueError as error:
            await update.message.reply_text(f"📄 {error} ♡")
            return
        except Exception as error:
            log.exception("Couldn't save a file")
            await update.message.reply_text(f"(,,>﹏<,,) I couldn't save that file: {error}")
            return
    if saved.get("error"):
        await update.message.reply_text(f"📄 {saved['error']} Is it a scan or a picture? ♡")
        return
    note = ("it was already saved" if saved.get("already_saved")
            else f"saved: {pages or 'no'} pages, {saved['pieces']} pieces")
    asked = update.message.caption or "Tell me in 1-2 short lines what it's about."
    await ask_gemini(update, context, with_timestamp(
        f'(I sent you the file "{name}" and it\'s in my second brain, {note}.)\n{asked}\n\n'
        f"The start of it:\n{text[:3000]}"), tool_names=jev_router.GROUPS["brain"])


async def show_report_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reportcard: the latest exam results (report_card.py, run on the PC)."""
    if await reject_if_not_owner(update):
        return
    cards = report_card.history(1)
    await update.message.reply_text(report_card.text(cards[-1]) if cards else
                                    "📊 No report card yet. Run report_card.py on your PC first ♡")


async def show_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/brain: the newest things in the second brain."""
    if await reject_if_not_owner(update):
        return
    saved = brain.list_brain(10)
    if not saved["total"]:
        await update.message.reply_text(
            "📚 Your second brain is empty for now ♡\nSend me a PDF, a link with \"save this\", a "
            "photo of your notes with \"save this\", or say \"remember that...\". Then just ask me about it 🎀")
        return
    icons = {"note": "📝", "link": "🔗", "pdf": "📄", "file": "📄"}
    lines = [f"{icons.get(item['kind'], '📝')} {item['title']} · {item['saved'][:10]}"
             for item in saved["newest_first"]]
    await update.message.reply_text(f"📚 Your second brain · {saved['total']} saved\n\n" + "\n".join(lines)
                                    + "\n\nAsk me anything about them ♡")


# --- Voice notes ------------------------------------------------------------------
MAX_VOICE_SECONDS = 5 * 60  # longer recordings are refused (cost + they're rarely needed)
TRANSCRIBE = ("Write down exactly what is said in this voice note, in the language(s) spoken "
              "(often English mixed with Malay). Return only the words, with normal punctuation, "
              "no comments. If nothing can be understood, return nothing.")


async def transcribe(audio: bytes, mime_type: str) -> str:
    """Voice note -> text, using Gemini (it understands audio directly)."""
    contents = [types.Part.from_bytes(data=audio, mime_type=mime_type), TRANSCRIBE]
    config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low"))
    try:
        response = await gemini.aio.models.generate_content(model=FAST_MODEL, contents=contents, config=config)
    except Exception as error:
        log.warning("%s couldn't transcribe (%s) - trying %s", FAST_MODEL, error, SMART_MODEL)
        response = await gemini.aio.models.generate_content(model=SMART_MODEL, contents=contents)
    return (response.text or "").strip()


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A voice note (or audio file): write down what was said, then handle it
    exactly like a typed message. The reply starts with what I heard, so a
    mishearing is easy to spot (and to correct by replying)."""
    if await reject_if_not_owner(update):
        return
    voice = update.message.voice or update.message.audio
    if voice.duration and voice.duration > MAX_VOICE_SECONDS:
        await update.message.reply_text("🎙️ That one's a bit long for me (max 5 minutes). "
                                        "Could you split it up? ♡")
        return
    async with typing_indicator(update.message.chat):
        audio_file = await voice.get_file()
        audio = bytes(await audio_file.download_as_bytearray())
        try:
            heard = await transcribe(audio, voice.mime_type or "audio/ogg")
        except Exception as error:
            log.exception("Transcription failed")
            await update.message.reply_text(f"(,,>﹏<,,) I couldn't listen to that one: {error}")
            return
    if not heard:
        await update.message.reply_text("🎙️ Hmm, I couldn't make out any words. Could you try again? 🎀")
        return
    text = replying_to(update) + heard
    await ask_gemini(update, context, with_timestamp(text), route_text=text, header=f"🎙️ “{heard}”\n\n")


# --- 7. Scheduled messages (sent without you asking) --------------------------
async def send_monthly_report(bot) -> None:
    """On the 1st of every month: report on the month that just ended."""
    now = tracker.now()
    year, month = reports.previous_month(now.year, now.month)
    text = await write_report(year, month)
    await send_long(bot, ALLOWED_USER_ID, "🗓️ New month! Here's how last month went:\n\n" + text)
    remember(int(ALLOWED_USER_ID), "Show me last month's growth report.", text)


async def send_weekly_news(bot) -> None:
    """Every Monday morning."""
    text = await write_news()
    await send_long(bot, ALLOWED_USER_ID, "☕ Your Monday AI & tech digest:\n\n" + text)
    # So you can reply "add the INNOFEST one to my calendar".
    remember(int(ALLOWED_USER_ID), "Show me the latest AI & tech news and events.", text)


# In the cloud, Google Cloud Scheduler triggers these by name (see cloud_server.py).
SCHEDULED = {"monthly-report": send_monthly_report, "weekly-news": send_weekly_news}


# --- 8. Start the bot ---------------------------------------------------------
def build_app() -> Application:
    """The Telegram bot with all its commands and buttons. Used both on your PC
    (main() below) and in the cloud (cloud_server.py)."""
    # Generous timeouts so a slow or patchy connection (e.g. phone hotspot)
    # doesn't make the bot give up. The default is only 5 seconds.
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .get_updates_connect_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("tasks", tasks))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(CommandHandler(["supps", "quick"], show_supplements))
    app.add_handler(CommandHandler("water", show_water))
    app.add_handler(CommandHandler("bills", show_bills))
    app.add_handler(CommandHandler("dashboard", open_dashboard))
    app.add_handler(CommandHandler("brain", show_brain))
    app.add_handler(CommandHandler("reportcard", show_report_card))
    app.add_handler(CommandHandler("period", show_period))
    app.add_handler(CommandHandler("reset", reset))
    import reminders  # imported here: reminders.py itself imports this file
    app.add_handler(CommandHandler("reminders", reminders.show_settings))
    import siri  # imported here: siri.py itself imports this file
    app.add_handler(CommandHandler("siri", siri.show_setup))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    return app


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anything that goes wrong while handling a message ends up in the logs
    (on Cloud Run: console.cloud.google.com -> Cloud Run -> mosshybot -> Logs)."""
    log.error("Error while handling an update", exc_info=context.error)


async def monthly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_monthly_report(context.bot)


async def weekly_news_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_weekly_news(context.bot)


async def reminders_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    import reminders
    await reminders.tick(context.bot)


def cloud_webhook_url() -> str:
    """Where Telegram currently delivers messages ("" = nowhere; the PC polls)."""
    reply = httpx.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo", timeout=30)
    return reply.json()["result"]["url"]


def main() -> None:
    """Run the bot on your PC: it keeps asking Telegram for new messages ("polling").
    Starting it switches Telegram away from the cloud bot, so it refuses to do
    that by accident: add --take-over to really run it here."""
    if cloud_webhook_url() and "--take-over" not in sys.argv:
        raise SystemExit(
            "The cloud bot (Cloud Run) is receiving your messages, so there's no need\n"
            "to run MosshyBot.py on this PC.\n\n"
            "To run it here anyway (e.g. to test a change):\n"
            "    .venv\\Scripts\\python MosshyBot.py --take-over\n"
            "Afterwards, give the messages back to the cloud bot with:\n"
            "    .venv\\Scripts\\python telegram_webhook.py set "
            "https://YOUR-SERVICE-URL.a.run.app")
    tracker.setup_database()
    app = build_app()
    if ALLOWED_USER_ID:
        # These only fire while the bot is running (i.e. while your PC is on).
        app.job_queue.run_monthly(monthly_report_job, when=time(9, 0, tzinfo=tracker.MALAYSIA), day=1)
        app.job_queue.run_daily(weekly_news_job, time(8, 30, tzinfo=tracker.MALAYSIA), days=(1,))  # 1 = Monday
        app.job_queue.run_repeating(reminders_job, interval=15 * 60, first=30)  # every 15 minutes

    log.info("Bot is running (chat: %s, reports: %s, Google Calendar: %s). Press Ctrl+C to stop.",
             FAST_MODEL, SMART_MODEL, "connected" if CALENDAR_ON else "not connected")
    # bootstrap_retries=-1: if the internet is down at startup, keep retrying
    # instead of crashing. Once running, the library already retries by itself.
    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    # Start through the importable module, so reminders.py (which imports
    # "MosshyBot") shares the same chat memory and settings as the running bot.
    import MosshyBot
    MosshyBot.main()
