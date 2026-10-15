"""
Shared set-up for every test (pytest loads this file automatically).

- Fake settings, so the bot's code can be imported without your real keys.
- A fresh pretend Firestore for each test (tests/fake_firestore.py).
- A pretend clock, so tests can say "it's Sunday 7:35pm" and get the same
  result every time.

Run all tests with:   .venv\\Scripts\\python -m pytest
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Fake settings, set BEFORE any bot code is imported.
for key, value in {"TELEGRAM_BOT_TOKEN": "123:test", "GEMINI_API_KEY": "test-key", "ALLOWED_USER_ID": "42",
                   "OWNER_NAME": "Alya", "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
                   "JOB_SECRET": "job-secret", "SHORTCUT_SECRET": "siri-secret",
                   "FIRESTORE_PREFIX": ""}.items():
    os.environ[key] = value
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "import tracker" works

import tracker  # noqa: E402
from fake_firestore import FakeClient  # noqa: E402

tracker._client = FakeClient()  # used while the bot's files are first imported


@pytest.fixture(autouse=True)
def fake_db():
    """Every test starts with an empty pretend Firestore."""
    tracker._client = FakeClient()
    yield tracker._client


@pytest.fixture
def clock(monkeypatch):
    """clock.set(2026, 10, 13, 9, 0) makes tracker.now() return that moment."""
    class Clock:
        now = datetime(2026, 10, 13, 9, 0, tzinfo=tracker.MALAYSIA)

        def set(self, *parts):
            self.now = datetime(*parts, tzinfo=tracker.MALAYSIA)

    c = Clock()
    monkeypatch.setattr(tracker, "now", lambda: c.now)
    return c
