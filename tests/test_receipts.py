"""Tests for receipts.py: purchase emails from the Gmail script become expenses."""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import bills
import cloud_server
import receipts
import tracker

SECRET = {"X-Shortcut-Secret": "siri-secret"}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(cloud_server, "application", SimpleNamespace(bot=fake))
    return fake


def gemini_reads(monkeypatch, shop, *purchases):
    """Pretend Gemini read the email and found these (item, RM, type) purchases."""
    prompts = []

    async def read(email):
        prompts.append(receipts._prompt(email))
        return receipts.Receipt(shop=shop, purchases=[
            receipts.Purchase(item=i, amount_rm=a, spending_type=t) for i, a, t in purchases])
    monkeypatch.setattr(receipts, "read_receipt", read)
    return prompts


def email(id_="m1", **extra):
    return {"action": "receipt", "id": id_, "from": "Shopee <info@mail.shopee.com.my>",
            "subject": "Your order has been confirmed", "date": "2026-10-12 21:15", "text": "Total RM89.00", **extra}


def test_a_receipt_is_saved_once_and_told_in_telegram(telegram, clock, monkeypatch):
    gemini_reads(monkeypatch, "Shopee", ("Laneige lip mask", 89, "shopping"))
    client = TestClient(cloud_server.web)
    assert client.post("/shortcut", json=email(), headers=SECRET).text == "Saved 1, skipped 0."
    assert client.post("/shortcut", json=email(), headers=SECRET).text == "Already done."
    [entry] = tracker.find_entries("2026-10-12", "2026-10-12", "expense")
    assert (entry["item"], entry["amount"], entry["tag"], entry["logged_at"]) == \
        ("Laneige lip mask", 89, "shopping", "2026-10-12 21:15")
    assert telegram.sent == ["🧾 From your email · Shopee (2026-10-12)\n• Laneige lip mask · RM89.00 (shopping)"]


def test_already_logged_by_the_spend_button(telegram, clock, monkeypatch):
    tracker.log_entry("expense", "lip mask", 89, "RM", tag="shopping", logged_at="2026-10-12 20:00")
    gemini_reads(monkeypatch, "Shopee", ("Laneige lip mask", 89, "shopping"))
    assert TestClient(cloud_server.web).post("/shortcut", json=email(), headers=SECRET).text == "Saved 0, skipped 1."
    assert len(tracker.find_entries("2026-10-12", "2026-10-12", "expense")) == 1
    assert "already logged, skipped: Laneige lip mask RM89.00" in telegram.sent[0]


def test_top_ups_and_promos_are_quiet(telegram, clock, monkeypatch):
    gemini_reads(monkeypatch, "Touch 'n Go")  # Gemini found nothing paid for
    assert TestClient(cloud_server.web).post("/shortcut", json=email("m2"), headers=SECRET).text == "Saved 0, skipped 0."
    assert telegram.sent == [] and tracker.find_entries("2026-10-12", "2026-10-12", "expense") == []


def test_the_prompt_knows_the_rules_and_the_subscriptions(telegram, clock, monkeypatch):
    bills.add_bill("Apple", 44.90, due_day=1, kind="subscription")
    prompts = gemini_reads(monkeypatch, "")
    TestClient(cloud_server.web).post("/shortcut", json=email("m3"), headers=SECRET)
    assert "Apple RM44.9" in prompts[0] and "Touch 'n Go" in prompts[0] and "OWN accounts" in prompts[0]
