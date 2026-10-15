"""
Gmail receipts: purchase emails (Shopee, Grab, foodpanda, bank transfers...) logged as expenses.

A small Google Apps Script inside the owner's own Gmail (gmail_receipts.gs, see README
"Gmail receipts") checks for new receipt-like emails every hour and posts each one to
POST /shortcut as {"action": "receipt", "id", "from", "subject", "date", "text"}.

Gemini reads the email and lists what was bought. Skipped on purpose:
    - promos and newsletters, refunds, failed or cancelled payments
    - money moved between the owner's own accounts, and e-wallet top-ups
      (Touch 'n Go, ShopeePay, GrabPay...): the spending happens later, not here
    - subscriptions already tracked as bills (bills.py)
    - a purchase with the same price already logged that day (e.g. by the Spend button)
Each email is handled once (Firestore "receipts"), and Moss tells the owner in Telegram.
"""

import os

from pydantic import BaseModel
from google.genai import types

import bills
import tracker

SPENDING_TYPES = ("food", "transport", "bills", "shopping", "health", "fun", "education", "other")


class Purchase(BaseModel):
    item: str
    amount_rm: float
    spending_type: str


class Receipt(BaseModel):
    shop: str
    purchases: list[Purchase]


def _prompt(email: dict) -> str:
    owner = os.environ.get("OWNER_NAME", "").strip()
    named = (f" Any transfer to an account in the owner's name ({owner}, often written in capitals "
             f"with more names) is to themselves." if owner else "")
    subscriptions = "; ".join(f"{b['name']} RM{b['amount_rm']:g}" for b in bills.all_bills()) or "none"
    return f"""This email came to the owner, who lives in Malaysia. List what the owner paid for in it.

Rules:
- Only real, completed payments by the owner: order confirmations, e-receipts, invoices,
  payment or transfer confirmations. Promotions, newsletters, delivery updates without
  a price, refunds, failed or cancelled payments: return no purchases.
- Money the owner moved to their OWN accounts (transfer to themselves, between their banks) and
  e-wallet top-ups/reloads (Touch 'n Go, ShopeePay, GrabPay, Boost...): no purchases.{named}
- A transfer to ANOTHER person counts: item = the transfer reference or the person's name.
- These subscriptions are already tracked, so skip their charges: {subscriptions}.
- item: short and plain, what was bought ("Laneige lip mask", "GrabFood KFC", "Grab ride").
  One entry per order (not per line), unless the items clearly belong to different types.
- amount_rm: the total paid in RM, after discounts and shipping. Other currency: convert to RM.
- spending_type: one of {", ".join(SPENDING_TYPES)}.
- shop: the shop or app, e.g. Shopee, Grab, foodpanda, CIMB.

From: {email['from']}
Subject: {email['subject']}

{email['text'][:8000]}"""


async def read_receipt(email: dict) -> Receipt:
    import MosshyBot as bot  # imported here: MosshyBot.py imports siri, which imports this file
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Receipt)
    response = await bot.gemini.aio.models.generate_content(
        model=bot.FAST_MODEL, contents=_prompt(email), config=config)
    return response.parsed or Receipt(shop="", purchases=[])


async def handle(telegram_bot, chat_id: int, data: dict) -> str:
    """One email from the Apps Script. Returns a short line (the script only logs it)."""
    email_id = str(data.get("id") or "").strip()
    if not email_id:
        return "No email id."
    seen = tracker._collection("receipts").document(email_id)
    if seen.get().exists:
        return "Already done."
    email = {key: str(data.get(key) or "") for key in ("from", "subject", "text")}
    when = str(data.get("date") or "").strip()[:16] or tracker.now().strftime("%Y-%m-%d %H:%M")

    receipt = await read_receipt(email)
    day = when[:10]
    logged_amounts = [e["amount"] for e in tracker.find_entries(day, day, "expense")]
    saved, skipped = [], []
    for p in receipt.purchases:
        amount = round(p.amount_rm, 2)
        if amount <= 0:
            continue
        if any(abs(amount - other) < 0.01 for other in logged_amounts):
            skipped.append(f"{p.item} RM{amount:.2f}")
            continue
        tag = p.spending_type if p.spending_type in SPENDING_TYPES else "other"
        tracker.log_entry("expense", p.item, amount, "RM", tag=tag, logged_at=when,
                          notes=f"from email: {receipt.shop}".strip())
        logged_amounts.append(amount)
        saved.append(f"• {p.item} · RM{amount:.2f} ({tag})")
    seen.set({"subject": email["subject"][:200], "saved": len(saved), "at": tracker.now().isoformat()})

    if saved or skipped:
        lines = [f"🧾 From your email{' · ' + receipt.shop if receipt.shop else ''} ({day})", *saved]
        if skipped:
            lines.append("already logged, skipped: " + ", ".join(skipped))
        await telegram_bot.send_message(chat_id, "\n".join(lines))
    return f"Saved {len(saved)}, skipped {len(skipped)}."
