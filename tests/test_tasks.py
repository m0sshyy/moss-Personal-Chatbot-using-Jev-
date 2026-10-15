"""Tests for the to-do list: daily vs one-off tasks, ticking, the panel's order."""

import tracker


def titles(shown):
    return [(task["title"], done) for task, done in shown]


def test_daily_tasks_come_first_then_by_due_date(clock):
    tracker.add_task("Pay bill", due_date="2026-10-13")
    tracker.add_task("No date task")
    tracker.add_task("Skincare", repeat="daily")
    tracker.add_task("Overdue thing", due_date="2026-10-10")
    tracker.add_task("Later thing", due_date="2026-10-20")
    shown, later = tracker.tasks_for_today()
    assert titles(shown) == [("Skincare", False), ("Overdue thing", False), ("Pay bill", False),
                             ("No date task", False)]
    assert later == 1


def test_ticked_habits_leave_the_list_until_their_next_day(clock):
    task_id = tracker.add_task("Skincare", repeat="daily")["task_id"]
    tracker.complete_task(task_id)
    assert titles(tracker.tasks_for_today()[0]) == []  # done: out of the way
    assert tracker.repeating_done_today() == 1
    clock.set(2026, 10, 14, 9, 0)  # next day: back, unticked
    assert titles(tracker.tasks_for_today()[0]) == [("Skincare", False)]
    assert tracker.repeating_done_today() == 0


def test_night_habits_show_from_8pm(clock):
    tracker.add_task("🧴 Skincare", repeat="daily", remind_at="22:00")
    tracker.add_task("🍵 Peppermint tea", repeat="daily")
    tracker.add_task("🥤 Detox juice", repeat="mon,tue", remind_at="18:00")  # 13 Oct 2026 is a Tuesday
    assert titles(tracker.tasks_for_today()[0]) == [("🍵 Peppermint tea", False), ("🥤 Detox juice", False)]
    clock.set(2026, 10, 13, 20, 0)
    assert ("🧴 Skincare", False) in titles(tracker.tasks_for_today()[0])


def test_panel_counts_the_habits_that_left(clock):
    import MosshyBot
    habit = tracker.add_task("Stretching", repeat="daily")["task_id"]
    tracker.add_task("Vet appointment")
    tracker.complete_task(habit)
    text, keyboard = MosshyBot.tasks_panel()
    assert text.startswith("📝 Today's tasks · 1/2 done") and "🔁 1 habit done" in text
    assert [row[0].text for row in keyboard.inline_keyboard] == ["⬜ Vet appointment"]


def test_one_off_task_stays_ticked_today_then_disappears(clock):
    task_id = tracker.add_task("Submit assignment")["task_id"]
    tracker.complete_task(task_id)
    assert titles(tracker.tasks_for_today()[0]) == [("Submit assignment", True)]
    clock.set(2026, 10, 14, 9, 0)
    assert tracker.tasks_for_today() == ([], 0)
    assert tracker.list_tasks() == []  # finished tasks are hidden...
    assert len(tracker.list_tasks(include_finished=True)) == 1  # ...but not deleted


def test_completing_logs_a_task_entry_and_untick_removes_it(clock):
    task_id = tracker.add_task("Call mum")["task_id"]
    tracker.complete_task(task_id)
    assert [e["item"] for e in tracker.find_entries("2026-10-13", "2026-10-13", "task")] == ["Call mum"]
    tracker.untick_task(task_id)
    assert tracker.find_entries("2026-10-13", "2026-10-13", "task") == []
    assert tracker.task_is_done(tracker.list_tasks()[0]) is False


def test_update_task_moves_date_and_reminder(clock):
    task_id = tracker.add_task("Pay bill", due_date="2026-10-13", remind_at="2026-10-13 09:00")["task_id"]
    tracker.update_task(task_id, due_date="2026-10-17", remind_at="2026-10-17 10:00")
    task = tracker.list_tasks()[0]
    assert (task["due"], task["remind_at"], task["reminded"]) == ("2026-10-17", "2026-10-17 10:00", False)
    tracker.update_task(task_id, due_date="none", remind_at="none")
    task = tracker.list_tasks()[0]
    assert (task["due"], task["remind_at"]) == ("", "")


def test_delete_task(clock):
    task_id = tracker.add_task("Old habit", repeat="daily")["task_id"]
    assert tracker.delete_task(task_id)["deleted"] is True
    assert tracker.list_tasks() == []
