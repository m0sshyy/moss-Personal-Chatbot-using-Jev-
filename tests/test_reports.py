"""Tests for reports.py: the numbers behind the weekly and monthly reports."""

from datetime import date

import reports
import tracker


def test_month_and_week_ranges():
    assert reports.month_range(2026, 2) == ("2026-02-01", "2026-02-28")
    assert reports.month_range(2026, 12) == ("2026-12-01", "2026-12-31")
    assert reports.previous_month(2026, 1) == (2025, 12)
    assert reports.week_range(date(2026, 10, 15)) == ("2026-10-12", "2026-10-18")  # Mon-Sun


def test_stats_averages_and_spending(clock):
    clock.set(2026, 10, 18, 20, 0)
    for day in ("2026-10-12", "2026-10-13"):
        tracker.log_entry("food", "lunch", calories=600, sugar_g=10, logged_at=f"{day} 13:00")
        tracker.log_entry("drink", "bubble tea", calories=300, sugar_g=45, logged_at=f"{day} 16:00")
        tracker.log_entry("expense", "lunch", 15, "RM", tag="food", logged_at=f"{day} 13:00")
    tracker.log_entry("expense", "Grab", 20, "RM", tag="transport", logged_at="2026-10-13 09:00")
    tracker.log_entry("water", "water", 1400, "ml", logged_at="2026-10-12 20:00")
    tracker.log_entry("workout", "walk", distance_km=3, duration_min=40, burned_kcal=180,
                      logged_at="2026-10-13 18:00")
    numbers = reports.stats("2026-10-12", "2026-10-18")
    assert numbers["avg_kcal_eaten_per_day"] == 900   # per day with food logged
    assert numbers["avg_sugar_g_per_day"] == 55
    assert numbers["avg_water_ml_per_day"] == 200     # 1400 over 7 days
    assert numbers["spent_rm"] == 50
    assert list(numbers["spent_by_type_rm"]) == ["food", "transport"]  # biggest first
    assert (numbers["workouts"], numbers["workout_km"]) == (1, 3)


def test_future_days_dont_count(clock):
    clock.set(2026, 10, 14, 12, 0)  # Tuesday: the week so far is Mon-Tue (... and today)
    assert reports.stats("2026-10-12", "2026-10-18")["dates"] == "2026-10-12 to 2026-10-14 (3 days)"


def test_weekly_prompt_includes_earlier_weeks(clock):
    clock.set(2026, 10, 18, 20, 0)
    assert reports.build_weekly_prompt(date(2026, 10, 18)) is None  # nothing logged
    tracker.log_entry("food", "lunch", calories=600, logged_at="2026-10-06 13:00")  # last week
    tracker.log_entry("food", "lunch", calories=700, logged_at="2026-10-14 13:00")  # this week
    prompt = reports.build_weekly_prompt(date(2026, 10, 18))
    assert "2026-10-12 to 2026-10-18" in prompt
    assert "2026-10-05 to 2026-10-11" in prompt  # the comparison week
    assert "Watch-outs" in prompt


def test_monthly_prompt_includes_trend_and_weight(clock):
    clock.set(2026, 11, 1, 9, 0)
    tracker.log_entry("food", "lunch", calories=600, logged_at="2026-09-10 13:00")
    tracker.log_entry("food", "lunch", calories=650, logged_at="2026-10-10 13:00")
    tracker.log_entry("weight", "weight", 56.1, "kg", logged_at="2026-10-01 07:30")
    prompt = reports.build_report_prompt(2026, 10)
    assert "September 2026" in prompt      # earlier month to compare
    assert "56.1 kg" in prompt
    assert reports.build_report_prompt(2025, 1) is None
