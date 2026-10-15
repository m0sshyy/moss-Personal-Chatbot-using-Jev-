"""Tests for bills.py and the bill reminders."""

import asyncio
from datetime import date
from types import SimpleNamespace

import bills
import MosshyBot
import reminders
import tracker


class FakeTelegram:
    def __init__(self):
        self.sent, self.next_id = [], 0

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append(text)
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def delete_message(self, *args):
        pass

    async def edit_message_text(self, *args, **kwargs):
        pass


def remind(day):
    fake = FakeTelegram()
    asyncio.run(reminders.send_bill_reminders(fake, day))
    return fake.sent


def test_due_dates_start_from_when_the_bill_was_added(clock):  # today: Tue 13 Oct 2026
    tracker_id = bills.add_bill("TNB", 80, due_day=10)["bill_id"]
    assert bills.list_bills()[0]["next_due"] == "2026-11-10"  # 10 Oct was before it was added
    bills.add_bill("Shopee VIP", 6.99, due_day=20, kind="subscription")
    assert [b["next_due"] for b in bills.list_bills()] == ["2026-11-10", "2026-10-20"]
    assert tracker_id


def test_short_months_use_their_last_day(clock):
    clock.set(2027, 2, 1, 9, 0)
    bills.add_bill("Rent", 900, due_day=31)
    assert bills.list_bills()[0]["next_due"] == "2027-02-28"


def test_bill_reminders_before_on_and_after_the_day(clock):
    bills.add_bill("TNB", 80, due_day=20, remind_days_before=3)
    assert remind(date(2026, 10, 16)) == []
    assert remind(date(2026, 10, 17)) == ["🧾 TNB is due on Tue 20 Oct (about RM80) 🎀"]
    assert remind(date(2026, 10, 20)) == ["🧾 TNB is due today (about RM80) 🎀"]
    assert remind(date(2026, 10, 22)) == ["🧾 TNB was due on Tue 20 Oct. Paid it already? ♡"]
    assert remind(date(2026, 10, 25)) == []  # more than 3 days late: stops


def test_paying_logs_the_expense_and_stops_reminders(clock):
    bill_id = bills.add_bill("TNB", 80, due_day=20)["bill_id"]
    clock.set(2026, 10, 20, 10, 0)
    result = bills.mark_bill_paid(bill_id, 92.40)
    assert result["paid"] and result["amount_rm"] == 92.40
    assert [(e["item"], e["amount"], e["tag"]) for e in tracker.find_entries("2026-10-20", "2026-10-20", "expense")] \
        == [("TNB", 92.40, "bills")]
    assert remind(date(2026, 10, 20)) == []
    assert bills.mark_bill_paid(bill_id)["already_paid"] is True
    assert bills.list_bills()[0]["next_due"] == "2026-11-20"


def test_subscriptions_log_themselves_once(clock):
    bills.add_bill("Shopee VIP", 6.99, due_day=20, kind="subscription")
    assert remind(date(2026, 10, 17)) == ["🔁 Heads-up: Shopee VIP renews on Tue 20 Oct (RM6.99). Still using it? ♡"]
    clock.set(2026, 10, 20, 9, 30)
    assert remind(date(2026, 10, 20)) == ["🔁 Shopee VIP renewed today: RM6.99 logged ♡"]
    assert remind(date(2026, 10, 20)) == []  # not twice
    assert len(tracker.find_entries("2026-10-20", "2026-10-20", "expense")) == 1


def test_untick_in_the_panel_removes_the_expense(clock):
    bill_id = bills.add_bill("Phone", 45, due_day=15)["bill_id"]
    clock.set(2026, 10, 15, 12, 0)
    bills.mark_bill_paid(bill_id)
    text, buttons = MosshyBot.bills_panel()
    assert "RM45.00 of RM45.00 paid" in text and buttons.inline_keyboard[0][0].text.startswith("✅")
    bills.unpay(bill_id)
    assert tracker.find_entries("2026-10-15", "2026-10-15", "expense") == []
    assert MosshyBot.bills_panel()[1].inline_keyboard[0][0].text.startswith("⬜")


def test_yearly_bills(clock):
    bills.add_bill("Car insurance", 900, due_day=5, frequency="yearly", due_month=3)
    assert bills.list_bills()[0]["next_due"] == "2027-03-05"
    assert "error" in bills.add_bill("Road tax", 90, due_day=5, frequency="yearly")


def test_late_payment_counts_for_the_bill_that_was_due(clock):
    bill_id = bills.add_bill("TNB", 80, due_day=20)["bill_id"]
    clock.set(2026, 10, 27, 12, 0)  # a week late
    assert bills.mark_bill_paid(bill_id)["period"] == "2026-10"  # October's, not November's
