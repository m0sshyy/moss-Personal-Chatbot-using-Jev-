"""
Google Calendar: tools that let Gemini add, list, move and delete your events.

One-time setup (README, "Connecting Google Calendar"):
    1. In Google Cloud you create credentials.json - it tells Google "this is MosshyBot".
    2. You run  .venv\\Scripts\\python connect_google.py  - you log in with your
       Google account in the browser, and it saves your permission ("token") for
       MosshyBot to manage your calendar events.
The token is stored in your private Firestore (settings/google_calendar_token),
so both your PC and the cloud copy of the bot can use it. It's like a key to
your calendar: never share it, or credentials.json.

Like tracker.py, the docstrings and type hints below are written for Gemini to read.
"""

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import tracker

FOLDER = Path(__file__).parent
CREDENTIALS_FILE = FOLDER / "credentials.json"
# "calendar.events" = may manage events, but not delete or share whole calendars.
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = "Asia/Kuala_Lumpur"


def _token_doc():
    return tracker._collection("settings").document("google_calendar_token")


def save_token(credentials: Credentials) -> None:
    _token_doc().set({"token": credentials.to_json(), "updated": tracker.now().isoformat()})


def is_connected() -> bool:
    return _token_doc().get().exists


def _calendar():
    """A connection to Google Calendar, renewing the login when it expires (hourly)."""
    stored = _token_doc().get()
    if not stored.exists:
        raise RuntimeError("Google Calendar isn't connected. On the PC, run: "
                           ".venv\\Scripts\\python connect_google.py")
    credentials = Credentials.from_authorized_user_info(json.loads(stored.to_dict()["token"]), SCOPES)
    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except RefreshError:
            raise RuntimeError("Google Calendar access has expired. On the PC, run: "
                               ".venv\\Scripts\\python connect_google.py") from None
        save_token(credentials)
    # A new connection per call: Gemini's tools run on helper threads, and this
    # library's connections must not be shared between threads.
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _parse(text: str) -> datetime:
    """'2026-10-16 14:00' or '2026-10-16T14:00' -> a datetime."""
    return datetime.fromisoformat(text.strip().replace(" ", "T"))


def _time_fields(start: str, end: str) -> dict:
    """Google wants all-day events as {"date"} and timed ones as {"dateTime", "timeZone"}."""
    if len(start.strip()) == 10:  # just a date -> all-day event
        first = _parse(start).date()
        last = _parse(end).date() if end else first
        # Google's all-day end date is exclusive: a 1-day event on the 16th ends on the 17th.
        return {"start": {"date": first.isoformat()},
                "end": {"date": (last + timedelta(days=1)).isoformat()}}
    begins = _parse(start)
    ends = _parse(end) if end else begins + timedelta(hours=1)
    return {"start": {"dateTime": begins.isoformat(timespec="seconds"), "timeZone": TIMEZONE},
            "end": {"dateTime": ends.isoformat(timespec="seconds"), "timeZone": TIMEZONE}}


REPEATS = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}


def _repeat_rule(repeat: str, repeat_until: str, all_day: bool) -> list[str]:
    """Google describes repeats with an "RRULE" line, e.g. RRULE:FREQ=YEARLY."""
    frequency = REPEATS.get(repeat.strip().lower())
    if not frequency:
        return []
    rule = f"RRULE:FREQ={frequency}"
    if repeat_until:
        last = _parse(repeat_until[:10]).date()
        # For timed events Google wants the end in UTC: the end of that day in
        # Malaysia (midnight, UTC+8) is 16:00 UTC, so stop at 15:59:59 UTC.
        rule += f";UNTIL={last:%Y%m%d}" if all_day else f";UNTIL={last:%Y%m%d}T155959Z"
    return [rule]


def _simplify(event: dict) -> dict:
    """Keep only what Gemini needs from Google's very detailed event data."""
    return {
        "event_id": event["id"],
        "title": event.get("summary", "(no title)"),
        "start": event["start"].get("dateTime", event["start"].get("date")),
        "end": event["end"].get("dateTime", event["end"].get("date")),
        "location": event.get("location", ""),
        "description": event.get("description", "")[:200],
        # Set for one occurrence of a repeating event: the id of the whole series.
        "series_id": event.get("recurringEventId", ""),
        "repeats": event.get("recurrence", []),
    }


# --- Tools Gemini can call -----------------------------------------------------
def add_calendar_event(
    title: str,
    start: str,
    end: str = "",
    location: str = "",
    description: str = "",
    reminder_minutes: int = 30,
    repeat: str = "",
    repeat_until: str = "",
) -> dict:
    """Add an event (class, appointment, meeting, deadline, event) to the owner's Google Calendar.

    Args:
        title: Short event name, e.g. "Python class", "Dentist", "INNOFEST 2026".
        start: Malaysia time, 'YYYY-MM-DD HH:MM'. Use just 'YYYY-MM-DD' for an
            all-day event (deadlines, birthdays, anniversaries, multi-day festivals).
        end: Same format as start. Leave empty for 1 hour (or one whole day).
        location: Place or meeting link, if known.
        description: Extra details: what to bring, the event's web link...
        reminder_minutes: Phone notification this many minutes before. 0 = none.
            For all-day events it counts back from midnight at the start of the
            day: 900 = 9am the day before (good for birthdays), 0 = none.
        repeat: Empty for a one-off event, or "daily", "weekly", "monthly" or
            "yearly" (birthdays and anniversaries are yearly all-day events).
        repeat_until: Optional last date 'YYYY-MM-DD' for repeats (e.g. the last
            week of a semester). Empty = repeats forever.
    """
    body = {"summary": title, "location": location, "description": description,
            **_time_fields(start, end)}
    body["reminders"] = {"useDefault": False, "overrides":
                         [{"method": "popup", "minutes": reminder_minutes}] if reminder_minutes else []}
    recurrence = _repeat_rule(repeat, repeat_until, all_day="date" in body["start"])
    if recurrence:
        body["recurrence"] = recurrence
    event = _calendar().events().insert(calendarId="primary", body=body).execute()
    return {"added": True, **_simplify(event)}


def list_calendar_events(start_date: str, end_date: str) -> list[dict]:
    """List the owner's calendar events between two dates (inclusive), earliest first.
    Use for "what's on tomorrow?", "am I free Friday?", and to find an event_id.

    Args:
        start_date: 'YYYY-MM-DD'
        end_date: 'YYYY-MM-DD'
    """
    day_after_end = _parse(end_date).date() + timedelta(days=1)
    result = _calendar().events().list(
        calendarId="primary",
        timeMin=f"{start_date.strip()[:10]}T00:00:00+08:00",
        timeMax=f"{day_after_end.isoformat()}T00:00:00+08:00",
        singleEvents=True,   # show each repeat of a weekly class separately
        orderBy="startTime",
        maxResults=100,
    ).execute()
    return [_simplify(event) for event in result.get("items", [])]


def update_calendar_event(
    event_id: str,
    title: str = "",
    start: str = "",
    end: str = "",
    location: str = "",
    description: str = "",
) -> dict:
    """Change an existing event (find its event_id with list_calendar_events first).
    Only the fields you fill in are changed. When moving an event, give both
    start and end, keeping its original length unless told otherwise.

    Args:
        event_id: The event's id. For a repeating event, event_id changes only
            that one occurrence; use its series_id to change every occurrence.
        title: New name, or empty to keep.
        start: New start, 'YYYY-MM-DD HH:MM' (or 'YYYY-MM-DD' for all-day), or empty to keep.
        end: New end, same format, or empty.
        location: New location, or empty to keep.
        description: New description, or empty to keep.
    """
    changes = {key: value for key, value in
               (("summary", title), ("location", location), ("description", description)) if value}
    if start:
        changes.update(_time_fields(start, end))
    event = _calendar().events().patch(calendarId="primary", eventId=event_id, body=changes).execute()
    return {"updated": True, **_simplify(event)}


# Deleting can't be undone, so Gemini can't do it alone: this tool only *asks*,
# and MosshyBot.py shows you Yes/No buttons. The delete happens when you tap Yes.
# Requests wait here until the buttons are shown (within the same message)...
new_delete_requests: dict[str, dict] = {}


# ...then in Firestore ("pending_deletes") until you tap, which may be after a restart.
def wait_for_tap(code: str, event: dict) -> None:
    tracker._collection("pending_deletes").document(code).set(event)


def stop_waiting(code: str) -> dict | None:
    """Forget a delete request (❌ No tapped). Returns the event, or None if unknown."""
    doc = tracker._collection("pending_deletes").document(code)
    snapshot = doc.get()
    if not snapshot.exists:
        return None
    doc.delete()
    return snapshot.to_dict()


def delete_calendar_event(event_id: str) -> dict:
    """Ask the owner to confirm deleting an event (find its event_id with
    list_calendar_events first). This does NOT delete straight away: the owner
    gets Yes/No buttons in Telegram and the event is deleted only if they tap Yes.

    Args:
        event_id: The event's id. For a repeating event, event_id is only that
            one occurrence; use its series_id to delete every occurrence.
    """
    event = _simplify(_calendar().events().get(calendarId="primary", eventId=event_id).execute())
    new_delete_requests[secrets.token_hex(4)] = event  # a random code like "9f3a0c1e"
    return {"status": "Yes/No buttons are being shown to the owner. Nothing is deleted "
                      "until they tap Yes. Tell them to tap a button.", "event": event}


def describe(event: dict) -> str:
    """Event details in words, for the confirmation message."""
    start = event["start"]
    if len(start) == 10:  # all-day
        day = datetime.fromisoformat(start)
        when = f"{day:%a} {day.day} {day:%b %Y} (all day)"
    else:
        begins, ends = datetime.fromisoformat(start), datetime.fromisoformat(event["end"])
        clock = lambda t: f"{t:%I:%M%p}".lstrip("0").lower()  # "2:00pm"
        when = f"{begins:%a} {begins.day} {begins:%b %Y}, {clock(begins)} - {clock(ends)}"
    text = f"{event['title']}\n{when}"
    if event["repeats"]:
        text += "\n🔁 Repeating event: EVERY repeat will be deleted"
    elif event["series_id"]:
        text += "\n🔁 Only this one date (the other repeats stay)"
    return text


def really_delete(code: str) -> dict | None:
    """Called when the owner taps Yes. Returns the deleted event, or None if
    the button was already used."""
    event = stop_waiting(code)
    if event:
        _calendar().events().delete(calendarId="primary", eventId=event["event_id"]).execute()
    return event


TOOLS = [add_calendar_event, list_calendar_events, update_calendar_event, delete_calendar_event]
