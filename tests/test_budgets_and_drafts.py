"""Tests for monthly budgets (tracker.py) and work write-up drafts (drafts.py)."""

import asyncio
from types import SimpleNamespace

import drafts
import MosshyBot
import reminders
import tracker


# --- Budgets ---------------------------------------------------------------------------
def test_budget_left_after_spending(clock):
    tracker.set_budget("food", 400)
    result = tracker.log_entry("expense", "lunch", 25, "RM", tag="food")
    assert result["budgets"] == [{"spending_type": "food", "monthly_rm": 400, "spent_rm": 25,
                                  "left_rm": 375, "percent_used": 6}]
    assert "budget_warnings" not in result


def test_warnings_at_80_and_100_percent_only_once(clock):
    tracker.set_budget("food", 100)
    assert "budget_warnings" not in tracker.log_entry("expense", "a", 70, "RM", tag="food")
    warned = tracker.log_entry("expense", "b", 15, "RM", tag="food")      # 85%
    assert warned["budget_warnings"] == ["food budget just passed 80%: RM85.00 of RM100 this month"]
    assert "budget_warnings" not in tracker.log_entry("expense", "c", 5, "RM", tag="food")  # 90%: already warned
    over = tracker.log_entry("expense", "d", 20, "RM", tag="food")        # 110%
    assert over["budget_warnings"] == ["food budget just passed 100%: RM110.00 of RM100 this month"]


def test_total_budget_counts_every_type(clock):
    tracker.set_budget("total", 1000)
    tracker.log_entry("expense", "Grab", 30, "RM", tag="transport")
    tracker.log_entry("expense", "lunch", 20, "RM", tag="food")
    assert tracker.get_budgets()[0]["spent_rm"] == 50


def test_budgets_reset_each_month(clock):
    tracker.set_budget("food", 100)
    tracker.log_entry("expense", "lunch", 90, "RM", tag="food", logged_at="2026-09-30 13:00")
    assert tracker.get_budgets()[0]["spent_rm"] == 0  # it's October now


def test_removing_and_unknown_budgets():
    tracker.set_budget("food", 100)
    assert tracker.set_budget("food", 0)["removed"] is True
    assert tracker.get_budgets() == []
    assert "error" in tracker.set_budget("snacks", 50)


# --- Work notes and drafts ----------------------------------------------------------------
def test_work_notes_leave_out_daily_habits(clock, monkeypatch):
    monkeypatch.setattr(drafts.google_calendar, "is_connected", lambda: False)
    tracker.log_entry("work", "fixed the RAG search bug")
    tracker.log_entry("food", "nasi lemak")
    skincare = tracker.add_task("🧴 Skincare", repeat="daily")["task_id"]
    report = tracker.add_task("📊 Send slides to my manager")["task_id"]
    tracker.complete_task(skincare)
    tracker.complete_task(report)
    notes = drafts.work_notes("2026-10-13", "2026-10-13")
    assert [n.split("] ", 1)[1] for n in notes["work_and_study_notes"]] == ["fixed the RAG search bug"]
    assert [n.split(" ", 2)[2] for n in notes["tasks_finished"]] == ["📊 Send slides to my manager"]
    assert "daily_log" in notes["formats"]


def test_report_covers_the_days_since_the_last_one(clock):
    today = clock.now.date()
    assert drafts.draft_dates({"draft": "daily_log"}, today) == (today, today)
    assert drafts.draft_dates({"draft": "work_report"}, today)[0].isoformat() == "2026-10-07"
    assert drafts.draft_dates({"draft": "work_report", "last_draft": "2026-10-09"}, today)[0].isoformat() == "2026-10-09"


def test_saved_style_is_used(clock):
    drafts.set_draft_style("daily_log", "Date / Task / Outcome")
    assert drafts.get_style("daily_log") == "Date / Task / Outcome"
    assert drafts.get_style("work_report") == drafts.DEFAULT_STYLES["work_report"]


def test_reminder_asks_when_there_are_no_notes(clock, monkeypatch):
    monkeypatch.setattr(drafts.google_calendar, "is_connected", lambda: False)
    task_id = tracker.add_task("📒 Log book report", repeat="weekdays", remind_at="16:00", draft="daily_log")["task_id"]
    task = next(t for t in tracker.list_tasks() if t["task_id"] == task_id)
    text, as_html = asyncio.run(reminders.reminder_with_draft(task))
    assert "What did you work on today?" in text


def test_reminder_comes_with_a_draft(clock, monkeypatch):
    monkeypatch.setattr(drafts.google_calendar, "is_connected", lambda: False)
    prompts = []

    async def fake_writer(prompt, config):
        prompts.append((prompt, config.system_instruction))
        return "Activities:\n- Fixed the RAG search bug <tested>"
    monkeypatch.setattr(MosshyBot, "ask_gemini_once", fake_writer)
    tracker.log_entry("work", "fixed the RAG search bug")
    task_id = tracker.add_task("📒 Log book report", repeat="weekdays", remind_at="16:00", draft="daily_log")["task_id"]
    task = next(t for t in tracker.list_tasks() if t["task_id"] == task_id)
    text, as_html = asyncio.run(reminders.reminder_with_draft(task))
    assert as_html and "<pre>Activities:\n- Fixed the RAG search bug &lt;tested&gt;</pre>" in text
    assert "fixed the RAG search bug" in prompts[0][0] and "no emojis" in prompts[0][1]
    assert tracker.list_tasks()[0]["last_draft"] == "2026-10-13"


def test_done_keeps_the_draft_but_removes_the_button(clock):
    task_id = tracker.add_task("📒 Log book report", repeat="weekdays", draft="daily_log")["task_id"]
    calls = []

    async def answer(text=None):
        calls.append(("answer", text))

    async def edit_markup(reply_markup=None):
        calls.append(("button removed", reply_markup))

    class FakeBot:
        async def delete_message(self, *args):
            calls.append(("deleted",))

        async def edit_message_text(self, *args, **kwargs):
            pass
    query = SimpleNamespace(answer=answer, edit_message_reply_markup=edit_markup, get_bot=lambda: FakeBot(),
                            message=SimpleNamespace(message_id=9, chat=SimpleNamespace(id=42)))
    asyncio.run(MosshyBot.on_reminder_done(query, task_id))
    assert ("button removed", None) in calls and ("deleted",) not in calls
