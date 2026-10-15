"""
Period / cycle tracker.

Everything is worked out from your "period" entries (the same log as everything
else): "start" and "end" entries, plus symptoms (cramps, bloating...).
    - cycle length  = average gap between your period starts (28 days until
                      there are at least two starts)
    - period length = average start -> end (5 days until you log an end)
    - next period   = last start + cycle length
    - ovulation     = about 14 days before the next period; the fertile window
                      is the 5 days before it plus that day
These are estimates for planning, NOT birth control and not medical advice.
"""

from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import tracker

DEFAULT_CYCLE, DEFAULT_LENGTH = 28, 5
SYMPTOMS = ["Cramps", "Bloating", "Headache", "Mood swings", "Acne", "Tired", "Cravings", "Back pain"]


def _period_entries(today: date) -> list[dict]:
    start = (today - timedelta(days=400)).isoformat()
    return [e for e in tracker.entries_between(start, today.isoformat()) if e["category"] == "period"]


def cycle_status() -> dict:
    """The owner's menstrual cycle right now: day of cycle, phase, average cycle
    and period length, predicted next period and fertile window (estimates),
    and today's/recent symptoms. Use this for "when is my next period?",
    "what day of my cycle am I on?", "am I late?"."""
    today = tracker.now().date()
    entries = _period_entries(today)
    day_of = lambda e: date.fromisoformat(e["logged_at"][:10])
    starts = sorted({day_of(e) for e in entries if "start" in e["item"].lower()})
    ends = sorted({day_of(e) for e in entries if e["item"].lower().startswith("end")})
    symptoms_recent = sorted({e["item"] for e in entries
                              if day_of(e) >= today - timedelta(days=3)
                              and "start" not in e["item"].lower() and not e["item"].lower().startswith("end")})
    if not starts:
        return {"has_data": False, "recent_symptoms": symptoms_recent}

    gaps = [(b - a).days for a, b in zip(starts, starts[1:]) if 20 <= (b - a).days <= 45]
    cycle = round(sum(gaps) / len(gaps)) if gaps else DEFAULT_CYCLE
    lengths = []
    for s in starts:  # each start paired with the first end within 10 days
        end = next((e for e in ends if 0 <= (e - s).days <= 10), None)
        if end:
            lengths.append((end - s).days + 1)
    length = round(sum(lengths) / len(lengths)) if lengths else DEFAULT_LENGTH

    last = starts[-1]
    ended_on = next((e for e in ends if 0 <= (e - last).days <= 10), None)
    day = (today - last).days + 1
    next_start = last + timedelta(days=cycle)
    ovulation = next_start - timedelta(days=14)
    # On your period: until the logged end, or (no end logged) a couple of days
    # past your usual length.
    on_period = today <= ended_on if ended_on else day <= length + 2
    ended = ended_on is not None
    if on_period:
        phase = "period (menstrual)"
    elif today < ovulation - timedelta(days=5):
        phase = "follicular"
    elif today <= ovulation + timedelta(days=1):
        phase = "fertile window (ovulation)"
    elif today < next_start:
        phase = "luteal (PMS can show up here)"
    else:
        phase = "period expected, not logged yet"
    if today > ovulation + timedelta(days=1):  # this cycle's window is over: show the next one
        ovulation += timedelta(days=cycle)
    return {
        "has_data": True, "last_start": last.isoformat(), "cycle_day": day,
        "average_cycle_days": cycle, "cycle_based_on_starts": len(starts),
        "average_period_days": length, "period_ended_logged": ended, "phase": phase,
        "next_period": next_start.isoformat(), "days_until_next": (next_start - today).days,
        "fertile_window": [(ovulation - timedelta(days=5)).isoformat(), (ovulation + timedelta(days=1)).isoformat()],
        "recent_symptoms": symptoms_recent,
    }


TOOLS = [cycle_status]


# --- The 🩸 Period panel ---------------------------------------------------------
def _short(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b}"


def panel() -> tuple[str, InlineKeyboardMarkup]:
    status = cycle_status()
    today_logged = {item for category, item in tracker.logged_today() if category == "period"}
    if not status["has_data"]:
        text = ("🩸 Period tracker\n\nNothing logged yet ♡ Tap 🩸 Started when your period "
                "begins, or tell me when the last one started (\"my last period started 20 Sep\").")
    else:
        days = status["days_until_next"]
        when = ("today" if days == 0 else f"in {days} days" if days > 0 else f"{-days} days late")
        text = (f"🩸 Cycle day {status['cycle_day']} · {status['phase']}\n"
                f"🗓️ Next period: {_short(status['next_period'])} ({when})\n"
                f"🌷 Fertile window: {_short(status['fertile_window'][0])} - "
                f"{_short(status['fertile_window'][1])}\n"
                f"🔁 Cycle ~{status['average_cycle_days']} days · period ~{status['average_period_days']} days")
        if status["cycle_based_on_starts"] < 2:
            text += "\n(Using a 28-day guess until you've logged 2 periods)"
        text += "\nEstimates only, not birth control ♡"
    if status["recent_symptoms"]:
        text += f"\n🫧 Recent: {', '.join(status['recent_symptoms'])}"
    tick = lambda name: ("✅ " if name.lower() in today_logged else "")
    keyboard = [[InlineKeyboardButton(tick("start") + "🩸 Started today", callback_data="period:start"),
                 InlineKeyboardButton(tick("end") + "🌸 Ended today", callback_data="period:end")]]
    symptom_buttons = [InlineKeyboardButton(tick(s) + s, callback_data=f"period:{s}") for s in SYMPTOMS]
    keyboard += [symptom_buttons[i:i + 2] for i in range(0, len(symptom_buttons), 2)]
    return text + "\n\nTap to log today, tap again to undo:", InlineKeyboardMarkup(keyboard)


async def on_button(query, what: str) -> None:
    """Tap = log it for today; tap again = undo (like the supplement checklist)."""
    if ("period", what.lower()) in tracker.logged_today():
        tracker.undo_latest_today("period", what)
        note = f"↩️ Removed {what}"
    else:
        tracker.log_entry("period", what.lower() if what in ("start", "end") else what,
                          tag="symptom" if what not in ("start", "end") else "")
        note = {"start": "🩸 Period started, logged. Be gentle with yourself ♡",
                "end": "🌸 Period ended, logged ♡"}.get(what, f"🫧 {what} logged ♡")
    await query.answer(note)
    text, buttons = panel()
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except BadRequest:
        pass


# --- Reminder text for reminders.py (sent at 9am when relevant) ----------------
def reminder_today() -> tuple[str, bool] | None:
    """(message, show buttons?) for today, or None. Only on the days it matters."""
    status = cycle_status()
    if not status["has_data"]:
        return None
    days, day = status["days_until_next"], status["cycle_day"]
    if days == 2:
        return (f"🩸 Heads-up: your period may start around {_short(status['next_period'])}. "
                "Maybe pack pads/tampons and a painkiller in your bag ♡", False)
    if days in (-3, -7):  # late, and no start logged
        return (f"🌷 Your period is about {-days} days later than your usual cycle. That can happen "
                "with stress, travel or sleep changes. Tap 🩸 Started when it comes. "
                "If you're worried, a doctor can help ♡", True)
    if not status["period_ended_logged"] and day == status["average_period_days"] + 1:
        return ("🌸 Has your period ended? Tap 🌸 Ended today when it does, so your cycle "
                "predictions get better ♡", True)
    return None
