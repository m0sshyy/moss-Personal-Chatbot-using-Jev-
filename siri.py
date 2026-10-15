"""
Siri commands: your iPhone's Shortcuts app talks to the cloud bot directly.

Each Shortcut sends a small message to the bot's web address (POST /shortcut,
see cloud_server.py) with a secret code in the "X-Shortcut-Secret" header, and
Siri speaks the reply. The commands (README, "Siri commands"):

    "Hey Siri, log water"           {"action": "water"}          instant, no AI
    "Hey Siri, took my supplements" {"action": "supplements"}    instant, no AI
    "Hey Siri, ask Moss"            {"action": "ask", "text": "..."}
                                    -> Jev + Gemini, like a typed message
    "Spend" (Control Centre)        {"action": "spend", "amount": "12.50", "item": "lunch"}
                                    -> saved as an expense; Gemini only picks the type
    Apple Pay automation            {"action": "spend", "amount": "RM12.50", "item": "KFC",
                                     "notes": "Apple Pay"}   every tap of your phone
    Gmail receipts script (hourly)  {"action": "receipt", "id", "from", "subject", "date", "text"}
                                    -> receipts.py
    nightly Apple Health automation {"action": "health", "steps": 8214,
                                     "distance_km": 5.6, "sleep_hours": 7.2}

Replies are short and free of emojis/kaomoji, because Siri reads them aloud.
Send /siri in Telegram to get the address and secret for setting up the Shortcuts.
"""

import os
import re
from datetime import timedelta

import MosshyBot as bot
import jev_router
import receipts
import tracker

FOR_SIRI = ("\n\n(This came through Siri and your answer will be read aloud: reply in one or two "
            "short spoken sentences, with no emojis, kaomoji, symbols, lists or line breaks.)")


def secret() -> str:
    return os.environ.get("SHORTCUT_SECRET", "").strip()


def speakable(text: str) -> str:
    """Make a reply nice to hear: drop emojis, kaomoji and list symbols."""
    plain = "".join(ch if ch.isascii() else " " for ch in text)  # emojis, ᵔ ᵕ ♡ etc.
    plain = re.sub(r"\(\s*[^\w\s]*\s*\)", " ", plain)              # kaomoji leftovers like "( )"
    plain = re.sub(r"^\s*[-*•]\s*", "", plain, flags=re.M)          # list dashes
    sentences = []
    for line in plain.splitlines():  # each line becomes a sentence
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line:
            sentences.append(line if line[-1] in ".!?" else line + ".")
    return " ".join(sentences) or "Done."


async def handle(telegram_bot, data: dict) -> str:
    """Do what the Shortcut asked, and return the sentence for Siri to say."""
    chat_id = int(bot.ALLOWED_USER_ID)
    action = str(data.get("action") or "ask").strip().lower()

    if action == "water":
        ml = float(data.get("ml") or 250)
        tracker.log_entry("water", "water", ml, "ml")
        await bot.refresh_panel(telegram_bot, chat_id, "water")  # keep the 💧 panel in Telegram up to date
        total = tracker.water_today()
        goal = next((g["target"] for g in tracker.get_goals() if g["name"] == "water"), None)
        reply = f"Logged {ml:g} millilitres. That's {total / 1000:.2f} litres today"
        if goal:
            reply += " and you've reached your goal, well done" if total >= goal else \
                     f", {max(0, goal - total) / 1000:.2f} litres to go"
        return reply + "."

    if action == "supplements":
        done = tracker.logged_today()
        names = [item["item"] for item in tracker.list_quick_buttons()
                 if (item["category"], item["item"].lower()) not in done and tracker.quick_log(item["id"])]
        await bot.refresh_panel(telegram_bot, chat_id, "supps")
        return f"Logged {', '.join(names)}. All supplements done today." if names else \
            "You've already taken all your supplements today."

    if action == "health":
        return save_health(data)

    if action in ("spend", "expense"):
        return await save_spending(data)

    if action == "receipt":  # the Gmail receipts script (receipts.py)
        return await receipts.handle(telegram_bot, chat_id, data)

    text = str(data.get("text") or "").strip()
    if not text:
        return "Sorry, I didn't catch that."
    tools = await jev_router.pick_tools(text, bot.previous_reply(chat_id))
    response = await bot.send_to_gemini(chat_id, bot.with_timestamp(text + FOR_SIRI), tools)
    reply = speakable(response.text or "Done.")
    if await bot.ask_to_confirm_deletes(telegram_bot, chat_id):
        reply += " Please confirm in Telegram."
    return reply


def _number(value) -> float:
    """Shortcuts may send 8214, "8214", "8,214" or "5.6 km": take the number."""
    match = re.search(r"-?\d[\d,]*\.?\d*", str(value or ""))
    return float(match.group().replace(",", "")) if match else 0.0


def save_health(data: dict) -> str:
    """The nightly Apple Health sync: steps, walking distance and (optional) sleep.
    Each day is saved once; syncing again just replaces that day's numbers."""
    now = tracker.now()
    # Synced just after midnight? Those numbers still belong to yesterday.
    day = (now - timedelta(hours=3)).date().isoformat()
    steps, km, sleep = _number(data.get("steps")), _number(data.get("distance_km")), _number(data.get("sleep_hours"))
    saved = []
    if steps:
        tracker.save_daily("steps", "steps", steps, "steps", day, distance_km=round(km, 2) if km else 0)
        saved.append(f"{steps:,.0f} steps" + (f", {km:.1f} kilometres" if km else ""))
    if sleep:
        tracker.save_daily("sleep", "sleep", round(sleep, 1), "hours", day)
        saved.append(f"{sleep:.1f} hours of sleep")
    return f"Saved {' and '.join(saved)}." if saved else "No health numbers came through."


SPENDING_TYPES = ("food", "transport", "bills", "shopping", "health", "fun", "education", "other")


async def guess_spending_type(item: str) -> str:
    """'lunch' -> food, 'Grab' -> transport... One tiny Gemini call; "other" if it fails."""
    prompt = (f"Which spending type is this purchase in Malaysia: \"{item}\"? "
              f"Answer with exactly one word from: {', '.join(SPENDING_TYPES)}.")
    try:
        response = await bot.gemini.aio.models.generate_content(model=bot.FAST_MODEL, contents=prompt)
        word = re.sub(r"[^a-z]", "", (response.text or "").lower())
    except Exception as error:
        bot.log.warning("Couldn't guess the spending type for %r: %s", item, error)
        return "other"
    return word if word in SPENDING_TYPES else "other"


async def save_spending(data: dict) -> str:
    """The "Spend" Shortcut (a price and what it was, typed in from Control Centre)
    and the Apple Pay automation (the amount and shop of each tap)."""
    amount = round(_number(data.get("amount")), 2)
    item = str(data.get("item") or data.get("text") or "").strip() or "something"
    if amount <= 0:
        return "I didn't get a price, nothing saved."
    tag = str(data.get("tag") or "").strip().lower()
    tag = tag if tag in SPENDING_TYPES else await guess_spending_type(item)
    result = tracker.log_entry("expense", item, amount, "RM", tag=tag, notes=str(data.get("notes") or ""))
    reply = f"Saved RM{amount:.2f} for {item}, under {tag}."
    for budget in result.get("budgets", []):
        left = budget["left_rm"]
        name = "Total" if budget["spending_type"] == "total" else budget["spending_type"].capitalize()
        reply += (f" {name} budget: RM{left:.2f} left this month." if left >= 0 else
                  f" {name} budget is over by RM{-left:.2f} this month.")
    return reply


async def show_setup(update, context) -> None:
    """/siri: the web address and secret to paste into the iPhone Shortcuts."""
    if await bot.reject_if_not_owner(update):
        return
    if not secret():
        await update.message.reply_text("Siri commands aren't switched on yet (no SHORTCUT_SECRET).")
        return
    webhook = (await context.bot.get_webhook_info()).url
    if not webhook:
        await update.message.reply_text("Siri commands only work with the cloud bot (Cloud Run) 🎀")
        return
    address = webhook.rsplit("/telegram", 1)[0] + "/shortcut"
    await update.message.reply_text(
        "🗣️ Siri setup (tap to copy):\n\n"
        f"Address:\n<code>{address}</code>\n\n"
        f"Header name:\n<code>X-Shortcut-Secret</code>\n\n"
        f"Secret:\n<code>{secret()}</code>\n\n"
        "Steps are in README.md, \"Siri commands\". Delete this message once you're done ♡",
        parse_mode="HTML")
