"""
Bills & subscriptions.

Two kinds:
    bill          you pay it yourself (TNB, water, phone, PTPTN, rent): reminders a
                  few days before and on the day, with a ✅ Paid button that logs
                  the expense (so it counts towards your budgets)
    subscription  charged automatically (Spotify, Netflix, iCloud): a heads-up a
                  few days before it renews, and the expense is logged by itself
                  on the renewal day

Each one repeats monthly (on a day of the month) or yearly (on a date). Whether
this period's one is paid is stored as "paid_for": "2026-10" (monthly) or "2026"
(yearly). The reminders themselves are sent by reminders.py.
"""

import calendar
from datetime import date

import tracker

KINDS = ("bill", "subscription")


def _bills():
    return tracker._collection("bills")


def _on(year: int, month: int, day: int) -> date:
    """That day, or the month's last day if it's shorter (a bill on the 31st, in February)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _period(bill: dict, due: date) -> str:
    return due.strftime("%Y") if bill["frequency"] == "yearly" else due.strftime("%Y-%m")


def _current(bill: dict, today: date) -> date:
    """This period's due date: this month's (monthly) or this year's (yearly)."""
    if bill["frequency"] == "yearly":
        return _on(today.year, bill["due_month"], bill["due_day"])
    return _on(today.year, today.month, bill["due_day"])


def _following(bill: dict, due: date) -> date:
    """The due date one period after `due`."""
    if bill["frequency"] == "yearly":
        return _on(due.year + 1, bill["due_month"], bill["due_day"])
    year, month = (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)
    return _on(year, month, bill["due_day"])


def due_date(bill: dict, today: date) -> date:
    """The due date that matters now: this period's, unless it's paid, long
    past, or from before the bill was added; then the next one."""
    due = _current(bill, today)
    if is_paid(bill, due) or (due - today).days < -3 or due.isoformat() < bill.get("starts", ""):
        due = _following(bill, due)
    return due


def shown_due(bill: dict, today: date) -> date:
    """The date shown in the 🧾 Bills panel: like due_date, but a monthly bill
    paid this month stays on this month's date (shown ✅) until the month ends."""
    due = _current(bill, today)
    if is_paid(bill, due) and bill["frequency"] == "monthly":
        return due
    return due_date(bill, today)


def is_paid(bill: dict, due: date) -> bool:
    return bill.get("paid_for") == _period(bill, due)


def all_bills() -> list[dict]:
    return [{"bill_id": doc.id, **doc.to_dict()} for doc in _bills().order_by("due_day").stream()]


# --- Tools Gemini can call -----------------------------------------------------
def add_bill(name: str, amount_rm: float, due_day: int, frequency: str = "monthly", due_month: int = 0,
             kind: str = "bill", spending_type: str = "bills", remind_days_before: int = 3) -> dict:
    """Add a recurring bill or subscription, e.g. "TNB bill around RM80 due on
    the 20th", "Spotify RM16.90 every 15th", "car insurance RM900 every 5 March".

    Args:
        name: Short name, e.g. "TNB", "Spotify", "PTPTN", "Phone bill".
        amount_rm: The usual amount (an estimate is fine for changing bills).
        due_day: Day of the month it's due or charged (1-31).
        frequency: "monthly" or "yearly".
        due_month: For yearly ones, the month (1-12). 0 for monthly.
        kind: "bill" if the owner pays it themselves, "subscription" if it's
            charged automatically (streaming, apps, cloud storage...).
        spending_type: The expense type it's logged as: bills, fun (streaming,
            games), education, health, transport, shopping or other.
        remind_days_before: How many days before to remind (default 3; 0 = only on the day).
    """
    kind = kind.strip().lower() if kind.strip().lower() in KINDS else "bill"
    frequency = "yearly" if frequency.strip().lower().startswith("y") else "monthly"
    if frequency == "yearly" and not 1 <= due_month <= 12:
        return {"error": "For a yearly bill, give due_month (1-12)."}
    _, doc = _bills().add({
        "name": name.strip(), "amount_rm": amount_rm, "due_day": max(1, min(31, int(due_day))),
        "frequency": frequency, "due_month": int(due_month) if frequency == "yearly" else 0, "kind": kind,
        "spending_type": spending_type.strip().lower() or "bills", "remind_days_before": max(0, int(remind_days_before)),
        "paid_for": "", "paid_entry": "",
        "starts": tracker.now().date().isoformat(),  # earlier due dates don't count
    })
    bill = {"bill_id": doc.id, **doc.get().to_dict()}
    return {"added": True, "bill_id": doc.id, "next_due": due_date(bill, tracker.now().date()).isoformat(),
            "tip": "The owner sees all bills with /bills."}


def list_bills() -> list[dict]:
    """The owner's bills and subscriptions: amount, next due date, days until
    due, and whether this period's one is paid. Use for "what bills are due?",
    "how much are my subscriptions?", or to find a bill_id."""
    today = tracker.now().date()
    result = []
    for bill in all_bills():
        due = due_date(bill, today)
        result.append({"bill_id": bill["bill_id"], "name": bill["name"], "kind": bill["kind"],
                       "amount_rm": bill["amount_rm"], "frequency": bill["frequency"], "next_due": due.isoformat(),
                       "days_until_due": (due - today).days, "paid": is_paid(bill, due)})
    return result


def mark_bill_paid(bill_id: str, amount_rm: float = 0) -> dict:
    """Mark the current bill as paid and log it as an expense ("paid my TNB, RM92").

    Args:
        bill_id: From list_bills.
        amount_rm: What was actually paid; 0 = the usual amount.
    """
    doc = _bills().document(str(bill_id))
    snapshot = doc.get()
    if not snapshot.exists:
        return {"error": f"No bill with id {bill_id}. Use list_bills to look it up."}
    bill = {"bill_id": doc.id, **snapshot.to_dict()}
    # Pay this period's bill (even if it's late); only move on to the next
    # period if this one is from before the bill was added. Never pay ahead
    # by accident when this period's is already paid.
    due = _current(bill, tracker.now().date())
    if is_paid(bill, due):
        return {"already_paid": True, "name": bill["name"], "period": _period(bill, due)}
    if due.isoformat() < bill.get("starts", ""):
        due = _following(bill, due)
    paid = amount_rm or bill["amount_rm"]
    logged = tracker.log_entry("expense", bill["name"], paid, "RM", tag=bill["spending_type"],
                               notes=f"{bill['kind']} for {_period(bill, due)}")
    doc.update({"paid_for": _period(bill, due), "paid_entry": logged["entry_id"]})
    return {"paid": True, "name": bill["name"], "amount_rm": paid, "period": _period(bill, due),
            **{key: value for key, value in logged.items() if key.startswith("budget")}}


def update_bill(bill_id: str, name: str = "", amount_rm: float = -1, due_day: int = 0, due_month: int = 0,
                frequency: str = "", kind: str = "", remind_days_before: int = -1) -> dict:
    """Change a bill or subscription ("Spotify went up to RM18.90", "TNB is due
    on the 25th now"). Only the fields you fill in change.

    Args:
        bill_id: From list_bills.
        name: New name, or empty to keep.
        amount_rm: New usual amount, or -1 to keep.
        due_day: New day of the month, or 0 to keep.
        due_month: New month for yearly ones, or 0 to keep.
        frequency: "monthly" / "yearly", or empty to keep.
        kind: "bill" / "subscription", or empty to keep.
        remind_days_before: New number of days, or -1 to keep.
    """
    doc = _bills().document(str(bill_id))
    if not doc.get().exists:
        return {"error": f"No bill with id {bill_id}. Use list_bills to look it up."}
    changes = {}
    if name.strip():
        changes["name"] = name.strip()
    if amount_rm >= 0:
        changes["amount_rm"] = amount_rm
    if due_day:
        changes["due_day"] = max(1, min(31, int(due_day)))
    if due_month:
        changes["due_month"] = int(due_month)
    if frequency.strip():
        changes["frequency"] = "yearly" if frequency.strip().lower().startswith("y") else "monthly"
    if kind.strip().lower() in KINDS:
        changes["kind"] = kind.strip().lower()
    if remind_days_before >= 0:
        changes["remind_days_before"] = int(remind_days_before)
    doc.update(changes)
    return {"updated": True, "bill": doc.get().to_dict()}


def delete_bill(bill_id: str) -> dict:
    """Remove a bill or subscription for good ("I cancelled Netflix").
    Past payments stay in the spending log."""
    doc = _bills().document(str(bill_id))
    existed = doc.get().exists
    doc.delete()
    return {"deleted": existed, "bill_id": bill_id}


TOOLS = [add_bill, list_bills, mark_bill_paid, update_bill, delete_bill]


# --- Used by the 🧾 Bills panel and reminders.py ----------------------------------
def unpay(bill_id: str) -> dict | None:
    """Undo "paid" (mis-tap): removes the logged expense too."""
    doc = _bills().document(str(bill_id))
    snapshot = doc.get()
    if not snapshot.exists:
        return None
    bill = snapshot.to_dict()
    if bill.get("paid_entry"):
        tracker.delete_entry(bill["paid_entry"])
    doc.update({"paid_for": "", "paid_entry": ""})
    return bill


def reminders_due(today: date) -> list[tuple[dict, str, date]]:
    """(bill, what, due date) for every bill that needs a message today:
    "soon" (its days-before day), "today", "overdue" (up to 3 days late) for
    bills; "soon" and "renewed" for subscriptions."""
    due_now = []
    for bill in all_bills():
        due = due_date(bill, today)
        if is_paid(bill, due):
            continue
        days = (due - today).days
        if bill["kind"] == "subscription":
            if days <= 0:
                due_now.append((bill, "renewed", due))
            elif days == bill["remind_days_before"]:
                due_now.append((bill, "soon", due))
        elif days == 0:
            due_now.append((bill, "today", due))
        elif days < 0:
            due_now.append((bill, "overdue", due))
        elif days == bill["remind_days_before"]:
            due_now.append((bill, "soon", due))
    return due_now
