"""Tests for reminders.py: what gets sent when, and never twice."""

import asyncio
from types import SimpleNamespace

import pytest

import google_calendar
import reminders
import tracker


class FakeTelegram:
    """Records messages instead of sending them (and which ones get deleted / edited)."""
    def __init__(self):
        self.sent, self.deleted, self.edited, self.next_id = [], [], [], 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append(text)
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        self.edited.append((message_id, text))


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()

    async def fake_report():  # the weekly report needs Gemini; pretend instead
        return "weekly report text"
    async def fake_gemini(prompt, config):
        return "briefing text"
    monkeypatch.setattr(reminders.bot, "write_weekly_report", fake_report)
    monkeypatch.setattr(reminders.bot, "ask_gemini_once", fake_gemini)
    monkeypatch.setattr(google_calendar, "is_connected", lambda: False)

    async def fake_weather():  # no internet in tests
        return "Shah Alam: Rain likely 3pm-6pm (up to 80%). 33°C max."
    monkeypatch.setattr(reminders.weather, "today_forecast", fake_weather)
    async def fake_air_line():
        return "AQI now 173 (Unhealthy)."
    monkeypatch.setattr(reminders.weather, "today_air", fake_air_line)

    async def no_warnings(now):
        return []
    monkeypatch.setattr(reminders.weather, "new_rain_warnings", no_warnings)

    async def clean_air(now):
        return None
    monkeypatch.setattr(reminders.weather, "air_change", clean_air)
    return fake


def tick_at(clock, telegram, *when):
    clock.set(*when)
    before = len(telegram.sent)
    asyncio.run(reminders.tick(telegram))
    return telegram.sent[before:]


def test_morning_briefing_once_and_not_too_early(clock, telegram):
    assert tick_at(clock, telegram, 2026, 10, 13, 7, 45) == []
    assert tick_at(clock, telegram, 2026, 10, 13, 8, 5) == ["briefing text"]
    assert tick_at(clock, telegram, 2026, 10, 13, 8, 20) == []  # already sent today


def test_late_reminders_are_skipped(clock, telegram):
    tick_at(clock, telegram, 2026, 10, 13, 12, 0)  # 4h after 8am: briefing skipped
    assert "briefing text" not in telegram.sent


def test_water_nudge_only_when_behind(clock, telegram):
    tracker.set_goal("water", 2500, "ml per day")
    tick_at(clock, telegram, 2026, 10, 13, 10, 0)  # skip the 8am briefing
    nudge = tick_at(clock, telegram, 2026, 10, 13, 11, 5)
    assert len(nudge) == 1 and "Water check, Alya" in nudge[0]
    tracker.log_entry("water", "water", 1500, "ml")
    assert tick_at(clock, telegram, 2026, 10, 13, 14, 5) == []  # on track: quiet


def test_switched_off_kinds_are_not_sent(clock, telegram):
    reminders._settings_doc().set({"briefing": False}, merge=True)
    assert tick_at(clock, telegram, 2026, 10, 13, 8, 5) == []
    assert reminders.enabled_kinds()["briefing"] is False
    assert reminders.enabled_kinds()["water"] is True


def test_task_reminders_once_and_daily(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False}, merge=True)
    tracker.add_task("Call mum", remind_at="2026-10-13 15:00")
    tracker.add_task("Skincare", repeat="daily", remind_at="22:00")
    assert tick_at(clock, telegram, 2026, 10, 13, 14, 50) == []
    assert tick_at(clock, telegram, 2026, 10, 13, 15, 5) == ["⏰ Reminder: Call mum 🎀"]
    assert tick_at(clock, telegram, 2026, 10, 13, 15, 20) == []
    assert tick_at(clock, telegram, 2026, 10, 13, 22, 5) == ["⏰ Reminder: Skincare 🎀"]
    assert tick_at(clock, telegram, 2026, 10, 14, 22, 5) == ["⏰ Reminder: Skincare 🎀"]  # every day


def test_no_task_reminder_once_done(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False}, merge=True)
    task_id = tracker.add_task("Skincare", repeat="daily", remind_at="22:00")["task_id"]
    tracker.complete_task(task_id)
    assert tick_at(clock, telegram, 2026, 10, 13, 22, 5) == []


def test_event_reminders(clock, telegram, monkeypatch):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False}, merge=True)
    calendar = {
        "2026-10-13": [{"event_id": "e1", "title": "Python class", "start": "2026-10-13T14:00:00+08:00",
                        "end": "2026-10-13T16:00:00+08:00", "location": "UiTM"}],
        "2026-10-14": [{"event_id": "e2", "title": "Aisyah's birthday", "start": "2026-10-14",
                        "end": "2026-10-15", "location": ""}],
    }
    monkeypatch.setattr(google_calendar, "is_connected", lambda: True)
    monkeypatch.setattr(google_calendar, "list_calendar_events", lambda start, end: calendar.get(start, []))
    assert tick_at(clock, telegram, 2026, 10, 13, 13, 15) == []  # 45 min before: too early
    soon = tick_at(clock, telegram, 2026, 10, 13, 13, 30)
    assert len(soon) == 1 and "Python class at 2:00pm (in 30 min)" in soon[0]
    assert tick_at(clock, telegram, 2026, 10, 13, 13, 45) == []  # not twice
    tomorrow = tick_at(clock, telegram, 2026, 10, 13, 20, 35)
    assert any("Aisyah's birthday (all day)" in message for message in tomorrow)


def test_weekly_report_on_sunday_and_weigh_in_on_the_first(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False,
                                   "events": False}, merge=True)
    assert tick_at(clock, telegram, 2026, 10, 17, 19, 35) == []  # Saturday
    sunday = tick_at(clock, telegram, 2026, 10, 18, 19, 35)
    assert any("weekly report text" in message for message in sunday)
    first = tick_at(clock, telegram, 2026, 11, 1, 7, 35)
    assert len(first) == 1 and "weigh-in" in first[0]


# --- Tasks on set weekdays ------------------------------------------------------------
def test_weekday_repeats(clock):
    assert tracker.normalise_repeat("weekdays") == "mon,tue,wed,thu,fri"
    assert tracker.normalise_repeat("Tuesday and Friday") == "tue,fri"
    assert tracker.normalise_repeat("every day") == "daily"
    assert tracker.normalise_repeat("") == ""


def test_weekday_task_shows_and_reminds_only_on_its_days(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False,
                                   "events": False, "period": False}, merge=True)
    tracker.add_task("📊 Weekly report to my manager", repeat="tue,fri", remind_at="16:00")
    clock.set(2026, 10, 14, 12, 0)  # a Wednesday
    assert tracker.tasks_for_today()[0] == []
    assert tick_at(clock, telegram, 2026, 10, 14, 16, 5) == []
    clock.set(2026, 10, 16, 12, 0)  # a Friday
    assert [t["title"] for t, _ in tracker.tasks_for_today()[0]] == ["📊 Weekly report to my manager"]
    assert tick_at(clock, telegram, 2026, 10, 16, 14, 50) == []
    # Fixed-day habits are reminded an hour early, to get ready
    assert tick_at(clock, telegram, 2026, 10, 16, 15, 5) == ["⏰ In 1 hour (4pm): 📊 Weekly report to my manager 🎀"]
    assert tick_at(clock, telegram, 2026, 10, 16, 16, 5) == []


def test_daily_habits_remind_on_time(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False,
                                   "events": False, "period": False}, merge=True)
    tracker.add_task("🧴 Skincare", repeat="daily", remind_at="22:00")
    assert tick_at(clock, telegram, 2026, 10, 13, 21, 5) == []
    assert tick_at(clock, telegram, 2026, 10, 13, 22, 5) == ["⏰ Reminder: 🧴 Skincare 🎀"]


# --- Keeping the chat tidy ------------------------------------------------------------
def test_a_new_panel_replaces_the_old_one(clock):
    fake = FakeTelegram()
    first = asyncio.run(reminders.bot.send_panel(fake, 42, "water"))
    asyncio.run(reminders.bot.send_panel(fake, 42, "water"))
    assert fake.deleted == [first.message_id]  # only one water panel left in the chat


def test_unanswered_reminder_is_replaced_next_time(clock, telegram):
    reminders._settings_doc().set({"briefing": False, "water": False, "evening": False,
                                   "events": False, "period": False}, merge=True)
    tracker.add_task("🧴 Skincare", repeat="daily", remind_at="22:00")
    tick_at(clock, telegram, 2026, 10, 13, 22, 5)
    first_reminder = telegram.next_id
    tick_at(clock, telegram, 2026, 10, 14, 22, 5)
    assert first_reminder in telegram.deleted  # yesterday's reminder removed


def test_done_on_a_reminder_ticks_it_removes_it_and_updates_the_panel(clock):
    bot = reminders.bot
    fake = FakeTelegram()
    task_id = tracker.add_task("📒 Log book report", repeat="weekdays", remind_at="16:00")["task_id"]
    panel = asyncio.run(bot.send_panel(fake, 42, "tasks"))
    reminder = SimpleNamespace(message_id=555, chat=SimpleNamespace(id=42))
    popups = []

    async def answer(text=None):
        popups.append(text)
    query = SimpleNamespace(message=reminder, answer=answer, get_bot=lambda: fake)
    asyncio.run(bot.on_reminder_done(query, task_id))
    assert tracker.task_is_done(tracker.list_tasks()[0])
    assert 555 in fake.deleted                       # the reminder disappeared
    assert fake.edited[-1][0] == panel.message_id    # the existing tasks panel was updated...
    assert "1/1 done" in fake.edited[-1][1] or "All 1 tasks done" in fake.edited[-1][1]
    assert popups == ["✅ 📒 Log book report done! ♡"]
