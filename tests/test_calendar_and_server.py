"""Tests for the Google Calendar helpers and the Cloud Run web server's security."""

from types import SimpleNamespace

from starlette.testclient import TestClient

import cloud_server
import google_calendar


# --- Calendar helpers (no Google needed) ----------------------------------------
def test_timed_event_defaults_to_one_hour():
    fields = google_calendar._time_fields("2026-10-16 14:00", "")
    assert fields["start"] == {"dateTime": "2026-10-16T14:00:00", "timeZone": "Asia/Kuala_Lumpur"}
    assert fields["end"]["dateTime"] == "2026-10-16T15:00:00"


def test_all_day_event_end_is_the_next_day():
    # Google counts an all-day event's end date as "not included".
    assert google_calendar._time_fields("2026-11-02", "") == {
        "start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}}


def test_repeat_rules():
    assert google_calendar._repeat_rule("yearly", "", True) == ["RRULE:FREQ=YEARLY"]
    assert google_calendar._repeat_rule("weekly", "2026-12-18", False) == ["RRULE:FREQ=WEEKLY;UNTIL=20261218T155959Z"]
    assert google_calendar._repeat_rule("", "", False) == []


def test_delete_confirmation_text():
    event = {"title": "Python class", "start": "2026-10-15T14:00:00+08:00",
             "end": "2026-10-15T16:00:00+08:00", "repeats": [], "series_id": "abc"}
    assert google_calendar.describe(event) == ("Python class\nThu 15 Oct 2026, 2:00pm - 4:00pm\n"
                                               "🔁 Only this one date (the other repeats stay)")


# --- Web server security ------------------------------------------------------------
def make_client(monkeypatch):
    handled = []

    async def fake_process_update(update):
        handled.append(update.update_id)
    # A pretend Telegram bot that just records which messages it was given.
    monkeypatch.setattr(cloud_server, "application",
                        SimpleNamespace(bot=None, process_update=fake_process_update))
    return TestClient(cloud_server.web), handled  # no "with": skips the Telegram start-up


def update(update_id):
    return {"update_id": update_id, "message": {"message_id": 1, "date": 0, "text": "hi",
                                                "chat": {"id": 42, "type": "private"},
                                                "from": {"id": 42, "is_bot": False, "first_name": "A"}}}


def test_telegram_messages_need_the_secret(monkeypatch):
    client, handled = make_client(monkeypatch)
    assert client.post("/telegram", json=update(1)).status_code == 403
    assert client.post("/telegram", json=update(1),
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
    assert handled == []


def test_repeated_telegram_messages_are_handled_once(monkeypatch):
    client, handled = make_client(monkeypatch)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}
    assert client.post("/telegram", json=update(7), headers=headers).status_code == 200
    assert client.post("/telegram", json=update(7), headers=headers).status_code == 200
    assert handled == [7]


def test_scheduled_jobs_need_the_secret(monkeypatch):
    client, _ = make_client(monkeypatch)
    assert client.post("/jobs/tick").status_code == 403
    assert client.post("/jobs/nonsense", headers={"X-Job-Secret": "job-secret"}).status_code == 404
    assert client.get("/").text == "MosshyBot is running"
