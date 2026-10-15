"""Tests for dashboard.py and its web addresses: login links, sessions, data."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import cloud_server
import dashboard
import tracker


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cloud_server, "application", SimpleNamespace(bot=None))
    return TestClient(cloud_server.web, base_url="https://testserver", follow_redirects=False)


def test_pages_need_a_login(client):
    assert client.get("/dashboard").status_code == 401
    assert "/dashboard" in client.get("/dashboard").text  # tells you to send /dashboard
    assert client.get("/dashboard/data").status_code == 401


def test_login_link_works_once(client, clock):
    token = dashboard.new_login_link_token()
    response = client.get(f"/dashboard/login?t={token}")
    assert response.status_code == 303 and response.headers["location"] == "/dashboard"
    cookie = response.cookies[dashboard.COOKIE]
    assert "httponly" in response.headers["set-cookie"].lower() and "secure" in response.headers["set-cookie"].lower()
    assert client.get("/dashboard", cookies={dashboard.COOKIE: cookie}).status_code == 200
    again = TestClient(cloud_server.web, base_url="https://testserver", follow_redirects=False)
    assert again.get(f"/dashboard/login?t={token}").status_code == 401  # already used


def test_expired_and_made_up_links_are_refused(clock):
    token = dashboard.new_login_link_token()
    clock.now += timedelta(minutes=dashboard.LINK_MINUTES + 1)
    assert dashboard.use_login_token(token) is False
    assert dashboard.use_login_token("made-up") is False
    assert dashboard.use_login_token("") is False


def test_sessions_cant_be_forged(monkeypatch):
    good = dashboard.new_session()
    assert dashboard.valid_session(good)
    expires, signature = good.split(".")
    assert not dashboard.valid_session(f"{int(expires) + 9999}.{signature}")  # extended by hand
    assert not dashboard.valid_session("garbage") and not dashboard.valid_session(None)
    monkeypatch.setattr(dashboard.time, "time", lambda: int(expires) + 1)
    assert not dashboard.valid_session(good)  # after 30 days


def test_data(client, clock):
    tracker.log_entry("food", "nasi lemak", calories=650, sugar_g=8)
    tracker.log_entry("water", "water", 500, "ml")
    tracker.log_entry("expense", "lunch", 15, "RM", tag="food")
    tracker.set_budget("food", 200)
    response = client.get("/dashboard/data?days=30", cookies={dashboard.COOKIE: dashboard.new_session()})
    d = response.json()
    assert len(d["dates"]) == 30 and d["dates"][-1] == "2026-10-13"
    assert d["today"]["kcal"] == 650 and d["today"]["water"] == 500 and d["series"]["sugar"][-1] == 8
    assert d["month"]["by_type"] == [["food", 15]]
    assert d["budgets"][0]["left_rm"] == 185
    assert response.headers["cache-control"] == "no-store"
