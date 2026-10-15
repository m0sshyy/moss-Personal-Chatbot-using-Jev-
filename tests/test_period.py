"""Tests for period.py: cycle maths and when period reminders are sent."""

import period
import tracker


def log_cycles(*starts_and_ends):
    for start, end in starts_and_ends:
        tracker.log_entry("period", "start", logged_at=f"{start} 08:00")
        if end:
            tracker.log_entry("period", "end", logged_at=f"{end} 08:00")


def test_no_data(clock):
    assert period.cycle_status()["has_data"] is False
    assert period.reminder_today() is None


def test_average_cycle_and_prediction(clock):
    log_cycles(("2026-08-20", "2026-08-24"), ("2026-09-17", "2026-09-21"))
    status = period.cycle_status()
    assert status["average_cycle_days"] == 28
    assert status["average_period_days"] == 5
    assert status["cycle_day"] == 27
    assert status["next_period"] == "2026-10-15"
    assert status["phase"].startswith("luteal")


def test_fertile_window_shown_is_the_upcoming_one(clock):
    log_cycles(("2026-08-20", "2026-08-24"), ("2026-09-17", "2026-09-21"))
    start, end = period.cycle_status()["fertile_window"]
    assert start >= "2026-10-13"  # not a window that's already over
    assert (start, end) == ("2026-10-24", "2026-10-30")


def test_one_start_uses_a_28_day_guess(clock):
    log_cycles(("2026-09-20", None))
    status = period.cycle_status()
    assert status["average_cycle_days"] == 28
    assert status["cycle_based_on_starts"] == 1


def test_on_period_until_the_logged_end(clock):
    log_cycles(("2026-10-11", None))
    assert period.cycle_status()["phase"].startswith("period")
    tracker.log_entry("period", "end", logged_at="2026-10-12 20:00")
    assert not period.cycle_status()["phase"].startswith("period")


def test_reminders_two_days_before_late_and_ended(clock):
    log_cycles(("2026-08-20", "2026-08-24"), ("2026-09-17", "2026-09-21"))
    assert "Heads-up" in period.reminder_today()[0]          # 13 Oct: 2 days before
    clock.set(2026, 10, 14, 9, 0)
    assert period.reminder_today() is None                   # 1 day before: nothing
    clock.set(2026, 10, 18, 9, 0)
    assert "later than your usual" in period.reminder_today()[0]  # 3 days late
    tracker.log_entry("period", "start", logged_at="2026-10-18 08:00")
    clock.set(2026, 10, 23, 9, 0)                            # day 6, no end logged
    assert "ended" in period.reminder_today()[0]
