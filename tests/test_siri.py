"""Tests for siri.py and the /shortcut address: Siri commands and Apple Health sync."""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import cloud_server
import siri
import tracker


class FakeTelegram:
    async def edit_message_text(self, *args, **kwargs):
        pass

    async def send_message(self, *args, **kwargs):
        return SimpleNamespace(message_id=1)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cloud_server, "application", SimpleNamespace(bot=FakeTelegram()))
    return TestClient(cloud_server.web)


SECRET = {"X-Shortcut-Secret": "siri-secret"}


def test_the_siri_address_needs_the_secret(client):
    assert client.post("/shortcut", json={"action": "water"}).status_code == 403
    assert client.post("/shortcut", json={"action": "water"}, headers={"X-Shortcut-Secret": "nope"}).status_code == 403
    assert tracker.water_today() == 0


def test_log_water(client, clock):
    tracker.set_goal("water", 2500, "ml per day")
    reply = client.post("/shortcut", json={"action": "water"}, headers=SECRET).text
    assert reply == "Logged 250 millilitres. That's 0.25 litres today, 2.25 litres to go."
    assert tracker.water_today() == 250


def test_took_my_supplements(client, clock):
    for name in ("Fish Oil", "Vit C"):
        tracker.add_quick_button(name)
    assert client.post("/shortcut", json={"action": "supplements"}, headers=SECRET).text == \
        "Logged Fish Oil, Vit C. All supplements done today."
    assert client.post("/shortcut", json={"action": "supplements"}, headers=SECRET).text == \
        "You've already taken all your supplements today."


def test_health_sync_saves_each_day_once(client, clock):
    clock.set(2026, 10, 14, 23, 50)
    data = {"action": "health", "steps": "8,214", "distance_km": "5.6 km", "sleep_hours": 7.25}
    assert client.post("/shortcut", json=data, headers=SECRET).text == \
        "Saved 8,214 steps, 5.6 kilometres and 7.2 hours of sleep."
    client.post("/shortcut", json={**data, "steps": 9000}, headers=SECRET)  # synced again
    steps = tracker.find_entries("2026-10-14", "2026-10-14", "steps")
    assert [(e["amount"], e["distance_km"]) for e in steps] == [(9000, 5.6)]  # replaced, not doubled


def test_health_sync_after_midnight_counts_for_yesterday(client, clock):
    clock.set(2026, 10, 15, 0, 20)
    client.post("/shortcut", json={"action": "health", "steps": 7000}, headers=SECRET)
    assert tracker.find_entries("2026-10-14", "2026-10-14", "steps")[0]["amount"] == 7000


def test_ask_mosshy_gets_a_speakable_answer(client, monkeypatch):
    async def fake_pick(text, previous=""):
        return {"summarise"}

    async def fake_gemini(chat_id, message, tools, owner_text=""):
        assert "read aloud" in message and tools == {"summarise"}
        return SimpleNamespace(text="You spent RM 86.50 this week 🌸\n- mostly food! (˶ᵔ ᵕ ᵔ˶)")
    monkeypatch.setattr(siri.jev_router, "pick_tools", fake_pick)
    monkeypatch.setattr(siri.bot, "send_to_gemini", fake_gemini)
    reply = client.post("/shortcut", json={"action": "ask", "text": "how much did I spend?"}, headers=SECRET).text
    assert reply == "You spent RM 86.50 this week. mostly food!"


def test_spend_from_control_centre(client, clock, monkeypatch):
    async def guess(item):
        return "food"
    monkeypatch.setattr(siri, "guess_spending_type", guess)
    tracker.set_budget("food", 200)
    tracker.set_budget("total", 1000)
    reply = client.post("/shortcut", json={"action": "spend", "amount": "RM12.50", "item": "lunch"}, headers=SECRET).text
    assert reply == ("Saved RM12.50 for lunch, under food. Food budget: RM187.50 left this month. "
                     "Total budget: RM987.50 left this month.")
    [entry] = tracker.find_entries("2026-10-13", "2026-10-13", "expense")
    assert (entry["item"], entry["amount"], entry["tag"]) == ("lunch", 12.5, "food")
    assert client.post("/shortcut", json={"action": "spend", "item": "lunch"}, headers=SECRET).text == \
        "I didn't get a price, nothing saved."


def test_spending_type_falls_back_to_other(monkeypatch):
    async def answer(**kwargs):
        return SimpleNamespace(text=" Transport.\n")
    async def broken(**kwargs):
        raise RuntimeError("offline")
    models = SimpleNamespace(generate_content=answer)
    monkeypatch.setattr(siri.bot, "gemini", SimpleNamespace(aio=SimpleNamespace(models=models)))
    assert asyncio.run(siri.guess_spending_type("Grab")) == "transport"
    models.generate_content = broken
    assert asyncio.run(siri.guess_spending_type("Grab")) == "other"


def test_apple_pay_tap(client, clock, monkeypatch):
    async def guess(item):
        return "food"
    monkeypatch.setattr(siri, "guess_spending_type", guess)
    data = {"action": "spend", "amount": "RM 1,012.50", "item": "KFC SS15", "notes": "Apple Pay"}
    assert client.post("/shortcut", json=data, headers=SECRET).text == "Saved RM1012.50 for KFC SS15, under food."
    [entry] = tracker.find_entries("2026-10-13", "2026-10-13", "expense")
    assert (entry["amount"], entry["notes"]) == (1012.5, "Apple Pay")
