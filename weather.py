"""
Today's rain forecast for the morning briefing (reminders.py), from Open-Meteo:
free, no account or key needed (open-meteo.com).

The place is saved in Firestore (settings/weather); until the owner sets one it's
Kuala Lumpur. "my area is Shah Alam" -> Gemini calls set_weather_place.
"""

import re
from datetime import datetime

import httpx

import tracker

DEFAULT = {"name": "Kuala Lumpur", "latitude": 3.1390, "longitude": 101.6869}
RAIN_FROM = 50        # % chance of rain that counts as "rain likely"
DAYTIME = range(7, 23)  # only mention rain between 7am and 10pm
STORM_CODES = {95, 96, 99}  # thunderstorm weather codes


def _setting():
    return tracker._collection("settings").document("weather")


def place() -> dict:
    saved = _setting().get()
    return (saved.to_dict() if saved.exists else None) or DEFAULT


def set_weather_place(place_name: str) -> dict:
    """Set where the owner is, for the rain forecast in the morning briefing.

    Args:
        place_name: A town or city, e.g. "Shah Alam", "Petaling Jaya", "Kota Kinabalu".
    """
    try:
        found = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=15,
                          params={"name": place_name.strip(), "count": 1, "countryCode": "MY"}).json()
    except httpx.HTTPError as error:
        return {"error": f"Couldn't look the place up right now ({error}). Try again later."}
    if not found.get("results"):
        return {"error": f"Couldn't find '{place_name}' in Malaysia. Try the nearest bigger town."}
    top = found["results"][0]
    state = top.get("admin1") if top.get("admin1") != top["name"] else None  # not "Putrajaya, Putrajaya"
    chosen = {"name": ", ".join(p for p in (top["name"], state) if p),
              "latitude": top["latitude"], "longitude": top["longitude"]}
    _setting().set(chosen)
    return {"saved": True, "weather_place": chosen["name"]}


def _hour(h: int) -> str:
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def describe(hourly: dict, daily: dict) -> str:
    """Open-Meteo's numbers -> one plain line, e.g.
    "Rain likely 3pm-6pm (up to 80%), with thunderstorms. 33°C max."""
    hours = [(int(t[11:13]), chance or 0, code) for t, chance, code in
             zip(hourly["time"], hourly["precipitation_probability"], hourly["weather_code"])]
    wet = [(h, chance, code) for h, chance, code in hours if h in DAYTIME and chance >= RAIN_FROM]
    hottest = f"{round(daily['temperature_2m_max'][0])}°C max."
    if not wet:
        top = max((chance for h, chance, _ in hours if h in DAYTIME), default=0)
        return f"No rain expected (at most {top}% chance). {hottest}"
    spells, start, last = [], wet[0][0], wet[0][0]  # group the rainy hours into spells
    for h, _, _ in wet[1:]:
        if h != last + 1:
            spells.append((start, last))
            start = h
        last = h
    spells.append((start, last))
    when = " and ".join(_hour(a) if a == b else f"{_hour(a)}-{_hour(b + 1)}" for a, b in spells)
    storm = ", with thunderstorms" if any(code in STORM_CODES for _, _, code in wet) else ""
    return f"Rain likely {when} (up to {max(c for _, c, _ in wet)}%){storm}. {hottest}"


async def today_forecast() -> str:
    """The line for the morning briefing, or why there isn't one."""
    where = place()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = (await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": where["latitude"], "longitude": where["longitude"],
                "hourly": "precipitation_probability,weather_code", "daily": "temperature_2m_max",
                "timezone": "Asia/Kuala_Lumpur", "forecast_days": 1})).json()
        return f"{where['name']}: {describe(data['hourly'], data['daily'])}"
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return f"Weather unavailable ({error.__class__.__name__})."



# --- Live heavy-rain alerts ---------------------------------------------------------
# MET Malaysia's official warnings (thunderstorms, continuous rain), from the
# government's open data API: free, no key. They name districts, not towns, and are
# issued for the next few hours. reminders.py checks them every 15 minutes.
WARNINGS_URL = "https://api.data.gov.my/weather/warning/"


def _alerts_doc():
    return tracker._collection("settings").document("rain_alerts")


def alert_areas() -> list[str]:
    saved = _alerts_doc().get()
    areas = (saved.to_dict() or {}).get("areas") if saved.exists else None
    return areas or [place()["name"].split(",")[0]]


def set_rain_alert_areas(areas: list[str]) -> dict:
    """Set the areas for live heavy-rain and thunderstorm alerts (MET Malaysia warnings).

    Warnings name districts, not towns, so use the district: Cyberjaya and Dengkil ->
    "Sepang"; Bangi, Kajang, Cheras -> "Hulu Langat"; Shah Alam, Petaling Jaya, Subang ->
    "Petaling"; Kuala Lumpur and Putrajaya stay as they are. Give the full list, e.g.
    home and work: ["Kuala Lumpur", "Petaling"].

    Args:
        areas: District names to watch.
    """
    cleaned = [a.strip() for a in areas if a.strip()]
    if not cleaned:
        return {"error": "Give at least one area."}
    _alerts_doc().set({"areas": cleaned}, merge=True)
    return {"saved": True, "rain_alert_areas": cleaned}


def _clock(when: datetime) -> str:
    return _hour(when.hour) if when.minute == 0 else f"{when.hour % 12 or 12}:{when:%M}{'am' if when.hour < 12 else 'pm'}"


def matching_warnings(warnings: list[dict], areas: list[str], now: datetime) -> list[dict]:
    """The land warnings (not sea ones) that mention one of the areas and haven't ended."""
    found = []
    for w in warnings:
        title = w["warning_issue"]["title_en"]
        if "sea" in title.lower() or "no advisory" in title.lower():
            continue
        until = datetime.fromisoformat(w["valid_to"]) if w.get("valid_to") else None
        if until and until <= now.replace(tzinfo=None):
            continue
        hit = [a for a in areas if re.search(rf"\b{re.escape(a)}\b", w.get("text_en", ""), re.I)]
        if hit:
            found.append({"key": f"{w['warning_issue']['issued']}|{title}", "title": title,
                          "areas": hit, "until": until})
    return found


async def new_rain_warnings(now: datetime) -> list[dict]:
    """Warnings for the owner's areas that haven't been sent yet (each is sent once)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(WARNINGS_URL, params={"limit": 50})
    response.raise_for_status()  # e.g. 429 "too many requests": try again next tick
    warnings = response.json()
    found = matching_warnings(warnings, alert_areas(), now)
    saved = _alerts_doc().get()
    sent = (saved.to_dict() or {}).get("sent", []) if saved.exists else []
    new = [w for w in found if w["key"] not in sent]
    if new:
        _alerts_doc().set({"sent": (sent + [w["key"] for w in new])[-30:]}, merge=True)
    return new


def alert_text(warning: dict) -> str:
    icon = "⛈️" if "thunder" in warning["title"].lower() else "🌧️"
    until = f" until {_clock(warning['until'])}" if warning["until"] else ""
    return (f"{icon} Heavy rain alert: {' & '.join(warning['areas'])}\n"
            f"MET Malaysia: {warning['title']}{until}.\n"
            "🚗 Your usual route can get dangerous in this. If you can, wait it out or take "
            "another way. Stay safe ♡")


TOOLS = [set_weather_place, set_rain_alert_areas]


# --- Live air quality (haze) alerts -------------------------------------------------
# Open-Meteo's air quality data (free, no key): the US AQI from PM2.5/PM10, the same
# 0-500 scale and bands as Malaysia's API (IPU). For anyone sensitive (asthma, sinus, allergies),
# "unhealthy for sensitive groups" (101+) already matters, so that's where alerts start.
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_BANDS = [  # (from AQI, level, label, advice)
    (0, 0, "Good to moderate", ""),
    (101, 1, "Unhealthy for sensitive groups", "Mask on outside and go easy on outdoor exercise."),
    (151, 2, "Unhealthy", "Wear a mask outside (N95 if you have one), keep windows closed."),
    (201, 3, "Very unhealthy", "Stay indoors if you can, mask on outside, air purifier on if you have one."),
    (301, 4, "Hazardous", "Stay indoors, windows shut. Mask on if you must go out."),
]
AIR_CLEAR_AT = 90  # back to "fine" only below this, so it doesn't flicker around 100


def air_band(aqi: float) -> tuple:
    return [band for band in AIR_BANDS if aqi >= band[0]][-1]


async def air_now() -> dict:
    """Current AQI and PM2.5 at the weather place, plus today's highest forecast AQI."""
    where = place()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(AIR_URL, params={
            "latitude": where["latitude"], "longitude": where["longitude"], "current": "us_aqi,pm2_5",
            "hourly": "us_aqi", "timezone": "Asia/Kuala_Lumpur", "forecast_days": 1})
    response.raise_for_status()
    data = response.json()
    return {"place": where["name"].split(",")[0], "aqi": round(data["current"]["us_aqi"]),
            "pm2_5": round(data["current"]["pm2_5"]),
            "today_max": round(max(a for a in data["hourly"]["us_aqi"] if a is not None))}


async def today_air() -> str:
    """The air quality line for the morning briefing."""
    try:
        air = await air_now()
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return f"Air quality unavailable ({error.__class__.__name__})."
    worst = air_band(air["today_max"])
    return (f"AQI now {air['aqi']} ({air_band(air['aqi'])[2]}), up to {air['today_max']} today ({worst[2]})."
            + (f" Sinus tip: {worst[3]}" if worst[1] else ""))


def _air_doc():
    return tracker._collection("settings").document("air_alerts")


async def air_change(now: datetime) -> str | None:
    """A message when the air gets worse than it's been today (or has cleared up
    after an alert), else None. Remembers the last level it told the owner."""
    air = await air_now()
    saved = _air_doc().get()
    state = saved.to_dict() if saved.exists else {}
    told = state.get("level", 0) if state.get("date") == now.date().isoformat() else 0
    _, level, label, advice = air_band(air["aqi"])
    if level > told:
        _air_doc().set({"date": now.date().isoformat(), "level": level})
        icon = "😷" if level < 3 else "🚨"
        return (f"{icon} Air quality alert: {air['place']}\nAQI {air['aqi']} · {label} (PM2.5 {air['pm2_5']} µg/m³)\n"
                f"{advice} Your sinuses will thank you ♡")
    if told and air["aqi"] < AIR_CLEAR_AT:
        _air_doc().set({"date": now.date().isoformat(), "level": 0})
        return f"😮‍💨 Air's better now in {air['place']}: AQI {air['aqi']}. Safe to open the windows ♡"
    return None
