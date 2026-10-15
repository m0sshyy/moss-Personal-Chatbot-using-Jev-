"""
Work write-ups: ready-made drafts of your daily log book entry and your report
to your supervisor, written from the work notes you give Moss during the day
("fixed the RAG search bug", "meeting with the team about the dashboard").

Two kinds, set on a task as its "draft" (see tracker.add_task):
    daily_log    - today's log book entry, sent with the task's reminder
    work_report  - a progress report covering the days since the last one

The drafts are written in plain professional English (no emojis or kaomoji) and
only from your own notes, so nothing is invented. If you send Moss an example
of your log book, or describe the format you need, it's saved with
set_draft_style and used from then on.
"""

from datetime import date, timedelta

from google.genai import types

import google_calendar
import tracker

DEFAULT_STYLES = {
    "daily_log": (
        "A daily log book entry:\n"
        "Date: <weekday, day month year>\n"
        "Activities:\n- 3 to 6 bullet points of what was done (past tense, specific)\n"
        "Skills / knowledge gained:\n- 1 to 3 bullet points\n"
        "Problems faced and how they were solved: (only if the notes mention any)"),
    "work_report": (
        "A short progress report to the supervisor named in the task title:\n"
        "Subject: Progress update <date range>\n"
        "Dear <supervisor>,\n"
        "Summary: 1 to 2 sentences\n"
        "Completed:\n- bullet points\n"
        "In progress:\n- bullet points (only if any)\n"
        "Issues / support needed: (only if any)\n"
        "Next steps:\n- bullet points\n"
        "Best regards,\n<owner's name>"),
}

WRITER = """
You write professional workplace documents for the owner, who works in Malaysia.
Write in clear, natural professional English: no emojis, no kaomoji, no cute or
casual wording. Use ONLY facts from the notes provided: never invent tasks,
results, numbers, names or dates. Leave out anything personal (health, meals,
family, errands, appointments that aren't work). If the notes are thin, keep the
draft short instead of padding it. Plain text: no Markdown symbols like ** or #.
Follow the given format exactly (its greeting, headings, bullet style and tone);
it always wins over these general rules. Return only the draft itself.
""".strip()


def _styles_doc():
    return tracker._collection("settings").document("draft_styles")


def get_style(kind: str) -> str:
    saved = _styles_doc().get()
    return ((saved.to_dict() or {}).get(kind) if saved.exists else None) or DEFAULT_STYLES[kind]


# --- Tools Gemini can call -----------------------------------------------------
def set_draft_style(kind: str, style: str) -> dict:
    """Save how the owner wants a work write-up formatted, e.g. when they send
    an example of their log book entry or say "my report needs a Risks section".

    Args:
        kind: "daily_log" (log book entry) or "work_report" (report to supervisor).
        style: The format to follow: their template, headings, or an example
            entry, in their own words (copy it faithfully).
    """
    kind = kind.strip().lower()
    if kind not in DEFAULT_STYLES:
        return {"error": "kind must be daily_log or work_report"}
    _styles_doc().set({kind: style.strip()}, merge=True)
    return {"saved": True, "kind": kind}


def work_notes(start_date: str, end_date: str) -> dict:
    """Everything about the owner's work and studies between two dates
    (inclusive): the work/study notes they logged, tasks they finished, and
    calendar events, plus the formats saved for log books and reports. Use it
    to write a log book entry (today), a report to their supervisor (since the
    last one, by default the past week) or resume bullet points.

    Args:
        start_date: 'YYYY-MM-DD'
        end_date: 'YYYY-MM-DD'
    """
    entries = tracker.entries_between(start_date, end_date)
    notes = [f"{e['logged_at']} [{e['category']}] {e['item']}" + (f" ({e['notes']})" if e.get("notes") else "")
             for e in entries if e["category"] in ("work", "study")]
    finished = [f"{e['logged_at']} {e['item']}" for e in entries
                if e["category"] == "task" and e.get("tag") != "daily"]  # not habits like skincare
    try:
        events = [f"{event['start'][:16].replace('T', ' ')} {event['title']}"
                  for event in google_calendar.list_calendar_events(start_date, end_date)] \
            if google_calendar.is_connected() else []
    except Exception:  # calendar login expired: write from the notes only
        events = []
    return {"work_and_study_notes": notes, "tasks_finished": finished, "calendar_events": events,
            "formats": {kind: get_style(kind) for kind in DEFAULT_STYLES}}


TOOLS = [work_notes, set_draft_style]


# --- Drafts that come with a task's reminder ---------------------------------------
def draft_dates(task: dict, today: date) -> tuple[date, date]:
    """Which days a draft covers: today for a log book entry; since the last
    report (or the past week) for a work report."""
    if task.get("draft") == "daily_log":
        return today, today
    last = task.get("last_draft")
    start = date.fromisoformat(last) if last and last < today.isoformat() else today - timedelta(days=6)
    return start, today


async def write_draft(task: dict) -> str | None:
    """The draft for this task's reminder, or None if there are no notes yet
    (then the reminder asks what you worked on instead)."""
    import MosshyBot as bot  # imported here: MosshyBot.py imports this file
    start, end = draft_dates(task, tracker.now().date())
    notes = work_notes(start.isoformat(), end.isoformat())
    if not (notes["work_and_study_notes"] or notes["tasks_finished"] or notes["calendar_events"]):
        return None
    prompt = (f"Write the draft for: {task['title']}\n"
              f"It covers: {start:%A %d %B %Y}" + (f" to {end:%A %d %B %Y}" if end != start else "") + "\n"
              f"The owner's name: {bot.OWNER_NAME or 'the owner'}\n"
              f"Format to follow:\n{get_style(task['draft'])}\n\n"
              f"Notes:\n{ {key: value for key, value in notes.items() if key != 'formats'} }")
    text = await bot.ask_gemini_once(prompt, types.GenerateContentConfig(system_instruction=WRITER))
    tracker._collection("tasks").document(task["task_id"]).update({"last_draft": end.isoformat()})
    return text.strip()
