"""
Automatic reminders (Phase 4): the morning briefing, water nudges, the evening
check-in, calendar event reminders (30 min before + the night before), and your
own "remind me to ..." reminders.

How it works: every 15 minutes something calls tick():
    - in the cloud: Google Cloud Scheduler (job "mosshybot-tick" -> /jobs/tick)
    - on your PC:   the bot's built-in job queue (MosshyBot.py, main())
tick() looks at the clock and sends whatever is due and hasn't been sent today.
What was sent today is remembered in Firestore (settings/reminders_state), so
nothing is sent twice, even if a tick is repeated.

Change the times below. Turn each type on/off in Telegram with /reminders.
"""

import html
import re
from datetime import date, datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import MosshyBot as bot
import bills
import drafts
import google_calendar
import period
import tracker
import weather

EVERY_DAY = lambda day: True
SUNDAYS = lambda day: day.weekday() == 6
FIRST_OF_MONTH = lambda day: day.day == 1

# (name, kind, time, which days). "kind" is what /reminders switches on and off.
SCHEDULE = [
    ("weigh-in", "weighin", time(7, 30), FIRST_OF_MONTH),
    ("briefing", "briefing", time(8, 0), EVERY_DAY),
    ("period", "period", time(9, 0), EVERY_DAY),
    ("bills", "bills", time(9, 30), EVERY_DAY),
    ("water-11", "water", time(11, 0), EVERY_DAY),
    ("water-14", "water", time(14, 0), EVERY_DAY),
    ("water-17", "water", time(17, 0), EVERY_DAY),
    ("weekly-report", "weekly", time(19, 30), SUNDAYS),
    ("water-20", "water", time(20, 0), EVERY_DAY),
    ("tomorrow", "events", time(20, 30), EVERY_DAY),
    ("evening", "evening", time(21, 0), EVERY_DAY),
]
KINDS = {
    "briefing": "🌅 Morning briefing (8am)",
    "water": "💧 Water nudges (if behind)",
    "evening": "🌙 Evening check-in (9pm)",
    "events": "📅 Events: 30 min before + night before",
    "period": "🩸 Period heads-ups",
    "bills": "🧾 Bills & subscriptions (9:30am)",
    "weekly": "📊 Weekly report (Sun 7:30pm)",
    "weighin": "⚖️ Monthly weigh-in (1st, 7:30am)",
    "tasks": "⏰ My task reminders",
    "rain": "⛈️ Heavy rain alerts (live)",
    "air": "😷 Air quality / haze alerts (live)",
}
LATE_LIMIT = timedelta(hours=2)  # skip a reminder that's more than 2 hours late
WATER_DAY = (time(8, 0), time(22, 0))  # spread the water goal over these hours
DEFAULT_WATER_GOAL = 2000  # ml, if no water goal is set
RAIN_ALERT_HOURS = range(12, 24)  # noon to midnight: the drive home and evenings out
AIR_ALERT_HOURS = range(6, 23)  # no haze alerts in the middle of the night
HABIT_HEADS_UP = timedelta(hours=1)  # fixed-day habits are reminded an hour early
EVENT_HEADS_UP = timedelta(minutes=35)  # "starting soon" window (ticks are 15 min apart)


# --- Settings and "already sent today" -----------------------------------------
def _settings_doc():
    return tracker._collection("settings").document("reminders")


def _state_doc():
    return tracker._collection("settings").document("reminders_state")


def enabled_kinds() -> dict[str, bool]:
    saved = _settings_doc().get()
    settings = saved.to_dict() if saved.exists else {}
    return {kind: settings.get(kind, True) for kind in KINDS}  # everything on by default


def _sent_today() -> set[str]:
    saved = _state_doc().get()
    state = saved.to_dict() if saved.exists else {}
    return set(state.get("sent", [])) if state.get("date") == tracker.today() else set()


def _mark_sent(sent: set[str]) -> None:
    _state_doc().set({"date": tracker.today(), "sent": sorted(sent)})


# --- The tick ---------------------------------------------------------------------
async def tick(telegram_bot) -> None:
    """Send whatever is due. Safe to call as often as you like."""
    if not bot.ALLOWED_USER_ID:
        return
    now = tracker.now()
    enabled, sent = enabled_kinds(), _sent_today()
    due = []
    for name, kind, at, on_day in SCHEDULE:
        scheduled = datetime.combine(now.date(), at, tzinfo=tracker.MALAYSIA)
        if name in sent or now < scheduled or not on_day(now.date()):
            continue
        sent.add(name)  # marked even when skipped, so it isn't retried all day
        if enabled[kind] and now - scheduled <= LATE_LIMIT:
            due.append(name)
    task_reminders = due_task_reminders(now) if enabled["tasks"] else []
    sent.update(f"task-{task['task_id']}" for task in task_reminders)
    events_soon = [e for e in events_starting_soon(now) if f"event-{e['event_id']}" not in sent] \
        if enabled["events"] else []
    sent.update(f"event-{e['event_id']}" for e in events_soon)
    # Save before sending: if sending fails halfway, nothing gets sent twice.
    _mark_sent(sent)

    for name in due:
        if name == "briefing":
            await send_briefing(telegram_bot)
        elif name.startswith("water"):
            await send_water_nudge(telegram_bot, now)
        elif name == "evening":
            await send_evening_check(telegram_bot)
        elif name == "tomorrow":
            await send_tomorrow_events(telegram_bot, now)
        elif name == "period":
            await send_period_reminder(telegram_bot)
        elif name == "bills":
            await send_bill_reminders(telegram_bot, now.date())
        elif name == "weekly-report":
            await send_weekly_report(telegram_bot)
        elif name == "weigh-in":
            await telegram_bot.send_message(
                bot.ALLOWED_USER_ID,
                "⚖️ It's monthly weigh-in day 🎀\nIf you'd like, step on the scale (mornings, before "
                "breakfast, are most consistent) and just send me the number, e.g. 55.2 kg ♡\n"
                "No pressure at all, skipping is totally fine 🤍", reply_markup=bot.DISMISS)
    for task in task_reminders:
        await send_task_reminder(telegram_bot, task)
    for event in events_soon:
        await send_event_soon(telegram_bot, event, now)
    if enabled["rain"] and now.hour in RAIN_ALERT_HOURS:
        await send_rain_alerts(telegram_bot, now)
    if enabled["air"] and now.hour in AIR_ALERT_HOURS:
        await send_air_alert(telegram_bot, now)


# --- Morning briefing --------------------------------------------------------------
def period_outlook() -> str:
    """A line for the briefing when the next period is within 3 days (or late)."""
    status = period.cycle_status()
    if status["has_data"] and -7 <= status["days_until_next"] <= 3:
        return (f"Cycle day {status['cycle_day']}, {status['phase']}; next period expected "
                f"{status['next_period']} (in {status['days_until_next']} days).")
    return ""


async def send_briefing(telegram_bot) -> None:
    today = tracker.now().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    shown, later = tracker.tasks_for_today()
    open_tasks = [t["title"] + (" (overdue)" if t["due"] and t["due"] < today.isoformat() else "")
                  for t, done in shown if not done]
    tomorrow = [t["title"] for t in tracker.list_tasks() if t["due"] == (today + timedelta(days=1)).isoformat()]
    try:
        events = (google_calendar.list_calendar_events(today.isoformat(), today.isoformat())
                  if google_calendar.is_connected() else "Google Calendar not connected")
    except Exception as error:  # e.g. the weekly Calendar login expired
        events = f"Couldn't read the calendar: {error}"
    facts = {
        "today": f"{today:%A %d %B %Y}",
        "calendar_today": events,
        "tasks_today": open_tasks,
        "tasks_due_tomorrow": tomorrow,
        "supplements_to_take": [b["item"] for b in tracker.list_quick_buttons()],
        "goals": tracker.get_goals(),
        "yesterday_totals": tracker.summarise(yesterday, yesterday),
        "budgets_80_percent_used_or_more": [b for b in tracker.get_budgets() if b["percent_used"] >= 80],
        "bills_due_in_the_next_7_days": [b for b in bills.list_bills()
                                         if not b["paid"] and b["days_until_due"] <= 7],
        "period": period_outlook(),
        "weather_today": await weather.today_forecast(),
        "air_quality_today": await weather.today_air(),
    }
    prompt = (
        "Write my morning briefing for Telegram from these facts. Keep it short "
        "(about 10-14 lines): a sweet good-morning line, today's schedule with times, "
        "the weather (rain times, and an umbrella reminder if rain is likely) and air quality (the sinus tip if it's unhealthy), the tasks (overdue first), any budget that's nearly used up, a one-line recap of yesterday (food kcal, water, "
        "exercise, spending, only what's there), the supplements to take, the period "
        "heads-up if there is one, and one small encouraging focus for the day. "
        f"Only use these facts:\n{facts}")
    text = await bot.ask_gemini_once(prompt, bot.types.GenerateContentConfig(
        system_instruction=bot.SYSTEM_INSTRUCTION))
    await bot.send_long(telegram_bot, bot.ALLOWED_USER_ID, text)
    # So you can answer it: "move the TNB task to tomorrow", "add gym at 7pm".
    bot.remember(int(bot.ALLOWED_USER_ID), "Give me my morning briefing.", text)


# --- Heavy rain alerts -----------------------------------------------------------
async def send_rain_alerts(telegram_bot, now: datetime) -> None:
    """MET Malaysia warnings for the owner's areas, each sent once. A new one
    replaces the previous alert, so the chat shows only the latest."""
    try:
        warnings = await weather.new_rain_warnings(now)
    except Exception as error:  # the weather service is down: try again next tick
        bot.log.warning("Couldn't check rain warnings: %s", error)
        return
    for warning in warnings:
        await bot.delete_remembered(telegram_bot, bot.ALLOWED_USER_ID, "rain_alert")
        message = await telegram_bot.send_message(bot.ALLOWED_USER_ID, weather.alert_text(warning),
                                                  reply_markup=bot.DISMISS)
        bot.remember_message("rain_alert", message.message_id)


async def send_air_alert(telegram_bot, now: datetime) -> None:
    """Haze: tell the owner when the air gets worse (or clears up), replacing the last alert."""
    try:
        text = await weather.air_change(now)
    except Exception as error:
        bot.log.warning("Couldn't check air quality: %s", error)
        return
    if text:
        await bot.delete_remembered(telegram_bot, bot.ALLOWED_USER_ID, "air_alert")
        message = await telegram_bot.send_message(bot.ALLOWED_USER_ID, text, reply_markup=bot.DISMISS)
        bot.remember_message("air_alert", message.message_id)


# --- Water nudge -----------------------------------------------------------------
async def send_water_nudge(telegram_bot, now: datetime) -> None:
    goal = next((g["target"] for g in tracker.get_goals() if g["name"] == "water"), DEFAULT_WATER_GOAL)
    start, end = (datetime.combine(now.date(), t, tzinfo=tracker.MALAYSIA) for t in WATER_DAY)
    share = min(1.0, max(0.0, (now - start) / (end - start)))
    expected, drank = goal * share, tracker.water_today()
    if drank >= expected - 250:  # on track (within one glass): no nudge
        return
    name = f", {bot.OWNER_NAME}" if bot.OWNER_NAME else ""
    header = f"🫧 Water check{name}! You're at {drank:,.0f} ml, aim for about {expected:,.0f} by now ♡\n\n"
    # Replaces the previous 💧 Water panel, so there's only ever one in the chat.
    await bot.send_panel(telegram_bot, bot.ALLOWED_USER_ID, "water", header)


# --- Evening check-in --------------------------------------------------------------
async def send_evening_check(telegram_bot) -> None:
    done = tracker.logged_today()
    supps_left = [b for b in tracker.list_quick_buttons()
                  if (b["category"], b["item"].lower()) not in done]
    shown, _ = tracker.tasks_for_today()
    tasks_left = [t for t, is_done in shown if not is_done]
    if not supps_left and not tasks_left:
        return  # all done: no need to bother you
    name = f", {bot.OWNER_NAME}" if bot.OWNER_NAME else ""
    header = f"🌙 Evening check-in{name}! Anything left for today? ₍ᐢ. .ᐢ₎\n\n"
    if supps_left:
        await bot.send_panel(telegram_bot, bot.ALLOWED_USER_ID, "supps", header)
        header = ""
    if tasks_left:
        await bot.send_panel(telegram_bot, bot.ALLOWED_USER_ID, "tasks", header)


# --- Calendar events -----------------------------------------------------------------
def _events_on(day: date) -> list[dict]:
    """The day's calendar events, or [] if the calendar can't be read right now
    (not connected, or the weekly login expired - the briefing mentions that)."""
    if not google_calendar.is_connected():
        return []
    try:
        return google_calendar.list_calendar_events(day.isoformat(), day.isoformat())
    except Exception:
        return []


def _clock(when: datetime) -> str:
    return f"{when:%I:%M%p}".lstrip("0").lower()  # "3:00pm"


def events_starting_soon(now: datetime) -> list[dict]:
    """Timed events starting within the next ~35 minutes."""
    if not time(6, 0) <= now.time() <= time(23, 30):  # no calendar checks overnight
        return []
    soon = []
    for event in _events_on(now.date()):
        if len(event["start"]) == 10:  # all-day events (birthdays...) have no start time
            continue
        starts = datetime.fromisoformat(event["start"])
        if timedelta(0) < starts - now <= EVENT_HEADS_UP:
            soon.append(event)
    return soon


async def send_event_soon(telegram_bot, event: dict, now: datetime) -> None:
    starts = datetime.fromisoformat(event["start"])
    minutes = round((starts - now).total_seconds() / 60)
    text = f"📅 Starting soon: {event['title']} at {_clock(starts)} (in {minutes} min) 🎀"
    if event.get("location"):
        text += f"\n📍 {event['location']}"
    await telegram_bot.send_message(bot.ALLOWED_USER_ID, text, reply_markup=bot.DISMISS)


async def send_tomorrow_events(telegram_bot, now: datetime) -> None:
    tomorrow = now.date() + timedelta(days=1)
    events = _events_on(tomorrow)
    if not events:
        return
    lines = [f"🌙 Tomorrow ({tomorrow:%a %d %b}) you have:"]
    for event in events:
        if len(event["start"]) == 10:
            lines.append(f"- {event['title']} (all day)")
        else:
            lines.append(f"- {_clock(datetime.fromisoformat(event['start']))} {event['title']}"
                         + (f" @ {event['location']}" if event.get("location") else ""))
    lines.append("Sleep well and get ready, okay? (˶ᵔ ᵕ ᵔ˶)")
    await telegram_bot.send_message(bot.ALLOWED_USER_ID, "\n".join(lines), reply_markup=bot.DISMISS)


# --- Weekly report -----------------------------------------------------------------
async def send_weekly_report(telegram_bot) -> None:
    text = await bot.write_weekly_report()
    await bot.send_long(telegram_bot, bot.ALLOWED_USER_ID, "🗓️ Your week, wrapped up 🎀\n\n" + text)
    bot.remember(int(bot.ALLOWED_USER_ID), "Show me my weekly report.", text)


# --- Bills & subscriptions -----------------------------------------------------------
async def send_bill_reminders(telegram_bot, today: date) -> None:
    """Bills: before, on and after the due day, with a ✅ Paid button.
    Subscriptions: a heads-up before, and logged automatically on the day."""
    for bill, what, due in bills.reminders_due(today):
        when = f"{due:%a} {due.day} {due:%b}"
        if what == "renewed":
            bills.mark_bill_paid(bill["bill_id"])
            await telegram_bot.send_message(
                bot.ALLOWED_USER_ID, f"🔁 {bill['name']} renewed today: RM{bill['amount_rm']:g} logged ♡",
                reply_markup=bot.DISMISS)
            continue
        if bill["kind"] == "subscription":  # "soon"
            await telegram_bot.send_message(
                bot.ALLOWED_USER_ID, f"🔁 Heads-up: {bill['name']} renews on {when} (RM{bill['amount_rm']:g}). "
                                     "Still using it? ♡", reply_markup=bot.DISMISS)
            continue
        text = {"soon": f"🧾 {bill['name']} is due on {when} (about RM{bill['amount_rm']:g}) 🎀",
                "today": f"🧾 {bill['name']} is due today (about RM{bill['amount_rm']:g}) 🎀",
                "overdue": f"🧾 {bill['name']} was due on {when}. Paid it already? ♡"}[what]
        key = f"bill_{bill['bill_id']}"
        await bot.delete_remembered(telegram_bot, bot.ALLOWED_USER_ID, key)  # yesterday's, if unanswered
        button = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Paid", callback_data=f"billpaid:{bill['bill_id']}")]])
        message = await telegram_bot.send_message(bot.ALLOWED_USER_ID, text, reply_markup=button)
        bot.remember_message(key, message.message_id)


# --- Period ------------------------------------------------------------------------
async def send_period_reminder(telegram_bot) -> None:
    reminder = period.reminder_today()
    if reminder is None:
        return
    text, with_buttons = reminder
    if with_buttons:
        await bot.send_panel(telegram_bot, bot.ALLOWED_USER_ID, "period", text + "\n\n")
    else:
        await telegram_bot.send_message(bot.ALLOWED_USER_ID, text, reply_markup=bot.DISMISS)


# --- Your own task reminders -------------------------------------------------------
def reminds_early(task: dict) -> bool:
    """Habits on fixed days with a time (log book Mon-Fri 4pm, detox juice Mon/Fri 6pm)
    get their reminder an hour before, to get ready. Daily ones remind on time."""
    return task["repeat"] not in ("", "daily") and len(task.get("remind_at", "")) == 5


def due_task_reminders(now: datetime) -> list[dict]:
    """Tasks whose reminder time has come and that haven't been reminded yet."""
    sent, done_today = _sent_today(), tracker.logged_today()
    due = []
    for task in tracker.list_tasks():
        remind_at = task.get("remind_at", "")
        if not remind_at or tracker.task_is_done(task, done_today):
            continue
        if len(remind_at) == 5:  # 'HH:MM': on each of the task's days
            if task["repeat"] and not tracker.repeats_on(task, now.date()):
                continue  # e.g. a Tue/Fri task on a Wednesday
            when = datetime.combine(now.date(), time.fromisoformat(remind_at), tzinfo=tracker.MALAYSIA)
            if reminds_early(task):
                when -= HABIT_HEADS_UP
            if now >= when and f"task-{task['task_id']}" not in sent:
                due.append(task)
        elif not task.get("reminded"):  # 'YYYY-MM-DD HH:MM': once
            when = datetime.fromisoformat(remind_at).replace(tzinfo=tracker.MALAYSIA)
            if now >= when:
                due.append(task)
    return due


async def send_task_reminder(telegram_bot, task: dict) -> None:
    if len(task["remind_at"]) > 5:
        tracker._collection("tasks").document(task["task_id"]).update({"reminded": True})
    key = f"reminder_{task['task_id']}"
    await bot.delete_remembered(telegram_bot, bot.ALLOWED_USER_ID, key)  # an older one, if never answered
    button = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"taskdone:{task['task_id']}")]])
    if task.get("draft"):
        text, as_html = await reminder_with_draft(task)
    else:
        text, as_html = f"⏰ Reminder: {task['title']} 🎀", False
        if reminds_early(task):
            at = weather._clock(datetime.combine(tracker.now().date(), time.fromisoformat(task["remind_at"])))
            text = f"⏰ In 1 hour ({at}): {task['title']} 🎀"
    message = await telegram_bot.send_message(bot.ALLOWED_USER_ID, text, reply_markup=button,
                                              parse_mode="HTML" if as_html else None)
    bot.remember_message(key, message.message_id)
    if task.get("draft"):  # so a reply like "also add the training session" makes sense
        bot.remember(int(bot.ALLOWED_USER_ID), f"(Reminder: {task['title']})", html.unescape(re.sub("<[^>]+>", "", text)))


async def reminder_with_draft(task: dict) -> tuple[str, bool]:
    """A work write-up reminder (drafts.py): the draft ready to copy, or, if
    there are no work notes yet, a question asking what you worked on."""
    try:
        draft = await drafts.write_draft(task)
    except Exception as error:  # Gemini hiccup: still remind, just without a draft
        bot.log.warning("Couldn't write the draft for %s: %s", task["title"], error)
        draft = None
    if draft:
        return (f"⏰ {html.escape(task['title'])} time! Here's a draft from your notes 🎀\n\n"
                f"<pre>{html.escape(draft)}</pre>\n\n"
                "Tap the box to copy it. Reply to change anything ♡"), True
    period_ = "today" if task["draft"] == "daily_log" else "since your last report"
    return (f"⏰ {html.escape(task['title'])} time! 🎀\n"
            f"What did you work on {period_}? Tell me (a voice note is fine) and I'll write the draft ♡"), True


# --- /reminders: switch each type on or off ---------------------------------------
def settings_panel() -> tuple[str, InlineKeyboardMarkup]:
    enabled = enabled_kinds()
    keyboard = [[InlineKeyboardButton(("✅ " if enabled[kind] else "⬜ ") + label,
                                      callback_data=f"remind:{kind}")] for kind, label in KINDS.items()]
    return "🔔 Reminders · tap to switch on/off ♡", InlineKeyboardMarkup(keyboard)


async def show_settings(update, context) -> None:
    if await bot.reject_if_not_owner(update):
        return
    text, buttons = settings_panel()
    await update.message.reply_text(text, reply_markup=buttons)


async def on_toggle(query, kind: str) -> None:
    if kind in KINDS:
        now_on = not enabled_kinds()[kind]
        _settings_doc().set({kind: now_on}, merge=True)
        await query.answer(f"{KINDS[kind]}: {'on' if now_on else 'off'}")
    else:
        await query.answer()
    text, buttons = settings_panel()
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except BadRequest:
        pass
