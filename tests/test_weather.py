"""Tests for weather.py (rain forecast in the morning briefing) and /reset clearing the chat."""

import asyncio
from types import SimpleNamespace

import httpx

import MosshyBot
import reminders
import weather


def forecast(chances, codes=None):
    """Open-Meteo-style hourly numbers for one day, from midnight."""
    hours = [f"2026-10-14T{h:02d}:00" for h in range(24)]
    return ({"time": hours, "precipitation_probability": chances, "weather_code": codes or [3] * 24},
            {"temperature_2m_max": [33.4]})


def test_dry_day():
    assert weather.describe(*forecast([10] * 24)) == "No rain expected (at most 10% chance). 33°C max."


def test_rain_spells_and_storms():
    chances = [0] * 24
    for h in (15, 16, 17):
        chances[h] = 80
    chances[21] = 55
    chances[3] = 90  # 3am: asleep, not mentioned
    codes = [3] * 24
    codes[16] = 95
    assert weather.describe(*forecast(chances, codes)) == \
        "Rain likely 3pm-6pm and 9pm (up to 80%), with thunderstorms. 33°C max."


def test_place_defaults_to_kl_and_can_be_changed(monkeypatch):
    assert weather.place()["name"] == "Kuala Lumpur"
    found = {"results": [{"name": "Shah Alam", "admin1": "Selangor", "latitude": 3.08, "longitude": 101.53}]}
    monkeypatch.setattr(weather.httpx, "get", lambda *a, **k: SimpleNamespace(json=lambda: found))
    assert weather.set_weather_place("shah alam") == {"saved": True, "weather_place": "Shah Alam, Selangor"}
    assert weather.place()["latitude"] == 3.08
    monkeypatch.setattr(weather.httpx, "get", lambda *a, **k: SimpleNamespace(json=lambda: {}))
    assert "Couldn't find" in weather.set_weather_place("Narnia")["error"]


def test_no_internet_doesnt_break_the_briefing(monkeypatch):
    class Offline:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, *a, **k):
            raise httpx.ConnectError("offline")
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **k: Offline())
    assert asyncio.run(weather.today_forecast()) == "Weather unavailable (ConnectError)."


def test_briefing_includes_the_weather(clock, monkeypatch):
    prompts = []

    async def fake_gemini(prompt, config):
        prompts.append(prompt)
        return "briefing"
    async def fake_weather():
        return "Shah Alam: Rain likely 3pm-6pm (up to 80%). 33°C max."
    async def fake_send(*args):
        pass
    monkeypatch.setattr(reminders.bot, "ask_gemini_once", fake_gemini)
    monkeypatch.setattr(reminders.bot, "send_long", fake_send)
    monkeypatch.setattr(reminders.weather, "today_forecast", fake_weather)
    async def fake_air_line():
        return "AQI now 173 (Unhealthy)."
    monkeypatch.setattr(reminders.weather, "today_air", fake_air_line)
    monkeypatch.setattr(reminders.google_calendar, "is_connected", lambda: False)
    asyncio.run(reminders.send_briefing(None))
    assert "Rain likely 3pm-6pm" in prompts[0] and "umbrella" in prompts[0] and "AQI now 173" in prompts[0]


def test_reset_clears_the_chat(fake_db):
    deleted = []

    class FakeBot:
        async def delete_messages(self, chat_id, ids):
            deleted.append(ids)
    MosshyBot.remember_message("water", 480)
    asyncio.run(MosshyBot.clear_chat(FakeBot(), 42, 250))
    assert deleted[0][0] == 250 and deleted[-1][-1] == 1 and sum(map(len, deleted)) == 250
    assert all(len(batch) <= 100 for batch in deleted)
    assert MosshyBot.remembered_message("water") is None


# --- Live heavy-rain alerts ---------------------------------------------------------
def warning(title, text, valid_to="2026-10-13T19:00:00", issued="2026-10-13T16:15:00"):
    return {"warning_issue": {"issued": issued, "title_en": title}, "valid_to": valid_to, "text_en": text}


STORM = warning("Thunderstorms Warning", "Thunderstorms, heavy rain and strong winds are expected over the states of "
                "Selangor (Petaling, Hulu Langat and Petaling) • W.P. Kuala Lumpur • W.P. Kuala Lumpur")
SEA = warning("Strong Winds and Rough Seas Warning", "Thunderstorms over the waters of Selangor • Kuala Lumpur")


def test_only_land_warnings_for_my_areas(clock):
    found = weather.matching_warnings([STORM, SEA, warning("No Advisory", "")], ["Kuala Lumpur", "Petaling"], clock.now)
    assert [(w["title"], w["areas"]) for w in found] == [("Thunderstorms Warning", ["Kuala Lumpur", "Petaling"])]
    assert weather.matching_warnings([STORM], ["Gombak"], clock.now) == []
    clock.set(2026, 10, 13, 19, 30)  # the warning has ended
    assert weather.matching_warnings([STORM], ["Petaling"], clock.now) == []


def test_alert_text(clock):
    [found] = weather.matching_warnings([STORM], ["Kuala Lumpur", "Petaling"], clock.now)
    assert weather.alert_text(found).startswith(
        "⛈️ Heavy rain alert: Kuala Lumpur & Petaling\nMET Malaysia: Thunderstorms Warning until 7pm.\n")


def test_areas_default_to_the_weather_place_and_can_be_set():
    assert weather.alert_areas() == ["Kuala Lumpur"]
    assert weather.set_rain_alert_areas(["Kuala Lumpur", " Petaling "]) == {"saved": True, "rain_alert_areas": ["Kuala Lumpur", "Petaling"]}
    assert weather.alert_areas() == ["Kuala Lumpur", "Petaling"]


def test_each_warning_is_sent_once_and_replaces_the_last(clock, monkeypatch):
    weather.set_rain_alert_areas(["Kuala Lumpur", "Petaling"])
    live = [STORM]

    class Offline:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, *a, **k):
            return SimpleNamespace(json=lambda: list(live), raise_for_status=lambda: None)
    monkeypatch.setattr(weather.httpx, "AsyncClient", lambda **k: Offline())

    sent, deleted = [], []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append(text)
            return SimpleNamespace(message_id=len(sent))
        async def delete_message(self, chat_id, message_id):
            deleted.append(message_id)
    telegram = FakeBot()
    asyncio.run(reminders.send_rain_alerts(telegram, clock.now))
    asyncio.run(reminders.send_rain_alerts(telegram, clock.now))  # 15 minutes later: same warning
    assert len(sent) == 1
    live.append(warning("Thunderstorms Warning", STORM["text_en"], "2026-10-13T21:00:00", "2026-10-13T18:45:00"))
    asyncio.run(reminders.send_rain_alerts(telegram, clock.now))  # extended: a new warning
    assert len(sent) == 2 and "until 9pm" in sent[1] and deleted == [1]


def test_a_broken_weather_service_doesnt_break_reminders(clock, monkeypatch):
    async def broken(now):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(reminders.weather, "new_rain_warnings", broken)
    asyncio.run(reminders.send_rain_alerts(None, clock.now))  # no error


# --- Air quality (haze) alerts ---------------------------------------------------------
def fake_air(monkeypatch, aqi, pm=60, today_max=None):
    async def air_now():
        return {"place": "Kuala Lumpur", "aqi": aqi, "pm2_5": pm, "today_max": today_max or aqi}
    monkeypatch.setattr(weather, "air_now", air_now)


def test_air_alerts_when_it_gets_worse_and_when_it_clears(clock, monkeypatch):
    check = lambda: asyncio.run(weather.air_change(clock.now))
    fake_air(monkeypatch, 60)
    assert check() is None  # fine
    fake_air(monkeypatch, 173, 70)
    assert check().startswith("😷 Air quality alert: Kuala Lumpur\nAQI 173 · Unhealthy (PM2.5 70 µg/m³)\nWear a mask")
    assert check() is None  # still unhealthy: not again
    fake_air(monkeypatch, 140)
    assert check() is None  # a bit better, but not clear
    fake_air(monkeypatch, 230)
    assert check().startswith("🚨 Air quality alert: Kuala Lumpur\nAQI 230 · Very unhealthy")
    fake_air(monkeypatch, 95)
    assert check() is None  # under 100, but not under 90 yet
    fake_air(monkeypatch, 70)
    assert check() == "😮‍💨 Air's better now in Kuala Lumpur: AQI 70. Safe to open the windows ♡"
    assert check() is None


def test_air_alert_resets_each_day(clock, monkeypatch):
    fake_air(monkeypatch, 173)
    assert asyncio.run(weather.air_change(clock.now))
    clock.set(2026, 10, 14, 8, 0)  # next morning, still hazy: tell again
    assert asyncio.run(weather.air_change(clock.now))


def test_briefing_air_line(monkeypatch):
    fake_air(monkeypatch, 120, today_max=210)
    assert asyncio.run(weather.today_air()) == (
        "AQI now 120 (Unhealthy for sensitive groups), up to 210 today (Very unhealthy). "
        "Sinus tip: Stay indoors if you can, mask on outside, air purifier on if you have one.")
