"""Tests for tracker.py: logging, totals, corrections, goals, quick buttons."""

import tracker


def test_log_entry_saves_and_returns_todays_totals(clock):
    result = tracker.log_entry("food", "nasi lemak", 1, "plate", 650, sugar_g=8, protein_g=20)
    assert result["saved"] is True
    assert result["logged_at"] == "2026-10-13 09:00"
    food = next(t for t in result["totals_for_2026-10-13"] if t["category"] == "food")
    assert food["total_calories"] == 650
    assert food["total_sugar_g"] == 8


def test_unknown_category_is_refused(clock):
    assert "error" in tracker.log_entry("snacks", "chips")
    assert tracker.find_entries("2026-10-13", "2026-10-13") == []


def test_zero_extra_numbers_are_not_stored(clock):
    tracker.log_entry("water", "water", 500, "ml")
    entry = tracker.find_entries("2026-10-13", "2026-10-13")[0]
    assert "sugar_g" not in entry and "distance_km" not in entry


def test_workout_numbers_are_kept_apart_from_calories_eaten(clock):
    tracker.log_entry("workout", "brisk walk", tag="walk", distance_km=3.2, duration_min=40, burned_kcal=180)
    workout = tracker.summarise("2026-10-13", "2026-10-13")[0]
    assert workout["total_calories"] == 0
    assert workout["total_distance_km"] == 3.2
    assert workout["total_burned_kcal"] == 180


def test_find_entries_is_newest_first_and_filters_by_category(clock):
    tracker.log_entry("food", "breakfast", logged_at="2026-10-13 08:00")
    tracker.log_entry("food", "lunch", logged_at="2026-10-13 13:00")
    tracker.log_entry("water", "water", 250, "ml", logged_at="2026-10-13 10:00")
    assert [e["item"] for e in tracker.find_entries("2026-10-13", "2026-10-13", "food")] == ["lunch", "breakfast"]


def test_date_ranges_include_the_whole_last_day(clock):
    tracker.log_entry("note", "late night", logged_at="2026-10-13 23:59")
    tracker.log_entry("note", "next day", logged_at="2026-10-14 00:00")
    assert [e["item"] for e in tracker.entries_between("2026-10-13", "2026-10-13")] == ["late night"]


def test_update_entry_changes_only_given_fields(clock):
    entry_id = tracker.log_entry("food", "waffle", 1, "serving", 650, sugar_g=40)["entry_id"]
    result = tracker.update_entry(entry_id, calories=325, sugar_g=20)
    assert result["now"]["calories"] == 325
    assert result["now"]["sugar_g"] == 20
    assert result["now"]["item"] == "waffle"  # unchanged
    assert len(tracker.find_entries("2026-10-13", "2026-10-13")) == 1  # corrected, not duplicated


def test_update_and_delete_unknown_entry(clock):
    assert "error" in tracker.update_entry("nope", calories=1)
    assert tracker.delete_entry("nope")["deleted"] is False


def test_goals_are_stored_by_lowercase_name():
    tracker.set_goal("Water", 2500, "ml per day")
    tracker.set_goal("water", 3000, "ml per day")  # replaces, doesn't duplicate
    assert tracker.get_goals() == [{"name": "water", "target": 3000, "unit": "ml per day"}]


def test_quick_buttons_keep_their_order_and_log(clock):
    for name in ("Fish Oil", "Vit C", "Magnesium"):
        tracker.add_quick_button(name)
    buttons = tracker.list_quick_buttons()
    assert [b["item"] for b in buttons] == ["Fish Oil", "Vit C", "Magnesium"]
    assert tracker.quick_log(buttons[1]["id"])["item"] == "Vit C"
    assert ("supplement", "vit c") in tracker.logged_today()
    assert tracker.quick_log("gone") is None


def test_undo_latest_today_removes_only_the_newest_match(clock):
    clock.set(2026, 10, 13, 10, 0)
    tracker.log_entry("water", "water", 250, "ml")
    clock.set(2026, 10, 13, 11, 0)
    tracker.log_entry("water", "water", 500, "ml")
    assert tracker.water_today() == 750
    assert tracker.undo_latest_today("water")["amount"] == 500
    assert tracker.water_today() == 250


def test_first_entry_date(clock):
    assert tracker.first_entry_date() is None
    tracker.log_entry("note", "hello", logged_at="2026-09-01 10:00")
    tracker.log_entry("note", "later")
    assert tracker.first_entry_date() == "2026-09-01"
