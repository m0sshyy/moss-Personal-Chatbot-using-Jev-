"""
Weekly and monthly reports, with suggestions.

Python does the counting (exact numbers), Gemini does the writing (the story,
the comparisons, the suggestions). Splitting it this way means the numbers in
your report are never "made up" by the AI.

stats() gives the same set of numbers for any date range, so this week can be
compared with your last few weeks, and this month with your last few months.
"""

from collections import defaultdict
from datetime import date, timedelta

import bills
import tracker
from tracker import entries_between, get_goals

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def month_range(year: int, month: int) -> tuple[str, str]:
    """First and last day of a month, e.g. (2026, 2) -> ('2026-02-01', '2026-02-28')."""
    first = date(year, month, 1)
    next_first = date(year + month // 12, month % 12 + 1, 1)
    last = date.fromordinal(next_first.toordinal() - 1)
    return first.isoformat(), last.isoformat()


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def week_range(day: date) -> tuple[str, str]:
    """Monday to Sunday of the week that contains `day`."""
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


# --- The numbers ------------------------------------------------------------------
def stats(start: str, end: str, entries: list[dict] | None = None) -> dict:
    """The key numbers for one week or month. Days in the future don't count."""
    entries = entries_between(start, end) if entries is None else entries
    first = date.fromisoformat(start)
    last = min(date.fromisoformat(end), tracker.now().date())
    days = max(1, (last - first).days + 1)
    total = defaultdict(float)
    food_days, water_days, sleep, weights, steps = set(), set(), [], [], []
    spent_by_type = defaultdict(float)
    for e in entries:
        category, day = e["category"], e["logged_at"][:10]
        if category in ("food", "drink"):
            food_days.add(day)
            total["kcal"] += e.get("calories") or 0
            for field in ("sugar_g", "protein_g"):
                total[field] += e.get(field) or 0
        elif category == "water":
            water_days.add(day)
            total["water"] += e.get("amount") or 0
        elif category == "workout":
            total["workouts"] += 1
            for field in ("distance_km", "duration_min", "burned_kcal"):
                total[field] += e.get(field) or 0
        elif category == "expense":
            total["spent"] += e.get("amount") or 0
            spent_by_type[e.get("tag") or "other"] += e.get("amount") or 0
        elif category == "sleep":
            sleep.append(e.get("amount") or 0)
        elif category == "steps":
            steps.append(e.get("amount") or 0)
            total["walking_km"] += e.get("distance_km") or 0
        elif category == "weight":
            weights.append(f"{day}: {e.get('amount'):g} kg")
        elif category == "supplement":
            total["supplements"] += 1
        elif category == "task":
            total["tasks"] += 1
    per_food_day = lambda value: round(value / len(food_days)) if food_days else None
    numbers = {
        "dates": f"{start} to {last.isoformat()} ({days} days)",
        "entries_logged": len(entries),
        "days_with_food_logged": len(food_days),
        "avg_kcal_eaten_per_day": per_food_day(total["kcal"]),
        "avg_sugar_g_per_day": per_food_day(total["sugar_g"]),
        "avg_protein_g_per_day": per_food_day(total["protein_g"]),
        "avg_water_ml_per_day": round(total["water"] / days),
        "days_water_logged": len(water_days),
        "workouts": int(total["workouts"]),
        "workout_km": round(total["distance_km"], 1),
        "workout_minutes": round(total["duration_min"]),
        "kcal_burned": round(total["burned_kcal"]),
        "spent_rm": round(total["spent"], 2),
        "spent_by_type_rm": {kind: round(rm, 2) for kind, rm in
                             sorted(spent_by_type.items(), key=lambda kv: -kv[1])},
        "avg_sleep_hours": round(sum(sleep) / len(sleep), 1) if sleep else None,
        "avg_steps_per_day": round(sum(steps) / len(steps)) if steps else None,
        "days_steps_synced": len(steps) or None,
        "walking_km_total": round(total["walking_km"], 1) or None,
        "weights": weights,
        "supplement_doses_taken": int(total["supplements"]),
        "supplement_doses_planned": len(tracker.list_quick_buttons()) * days,
        "tasks_done": int(total["tasks"]),
    }
    return {key: value for key, value in numbers.items() if value not in (None, [], {})}


def _daily_lines(entries: list[dict]) -> list[str]:
    """Food/drink/water add up to many small entries, so give one line per day instead."""
    days = defaultdict(lambda: [0.0, 0.0, 0.0])  # day -> [kcal, sugar g, water ml]
    for e in entries:
        if e["category"] in ("food", "drink", "water"):
            day = days[e["logged_at"][:10]]
            day[0] += e.get("calories") or 0
            day[1] += e.get("sugar_g") or 0
            if e["category"] == "water":
                day[2] += e.get("amount") or 0
    return [f"{day}: {kcal:.0f} kcal, {sugar:.0f} g sugar, {water:.0f} ml water"
            for day, (kcal, sugar, water) in sorted(days.items())]


def _food_lines(entries: list[dict]) -> list[str]:
    """What was eaten/drunk (so Gemini can name the sugary drinks etc.)."""
    return [f"{e['logged_at']} {e['item']} ({e.get('calories') or 0:.0f} kcal, "
            f"{e.get('sugar_g') or 0:.0f} g sugar)"
            for e in entries if e["category"] in ("food", "drink")]


def _other_lines(entries: list[dict]) -> list[str]:
    """Everything except food/drink/water, one compact line each."""
    lines = []
    for e in entries:
        if e["category"] in ("food", "drink", "water"):
            continue
        group = f"{e['category']}/{e['tag']}" if e.get("tag") else e["category"]
        line = f"{e['logged_at']} [{group}] {e['item']}"
        if e.get("amount"):
            line += f" — {e['amount']:g} {e.get('unit') or ''}".rstrip()
        exercise = [f"{e[key]:g} {label}" for key, label in
                    (("distance_km", "km"), ("duration_min", "min"), ("burned_kcal", "kcal burned")) if e.get(key)]
        if exercise:
            line += " — " + ", ".join(exercise)
        if e.get("notes"):
            line += f" ({e['notes']})"
        lines.append(line)
    return lines


# --- How Gemini should coach -----------------------------------------------------
COACHING = """
How to write the suggestions:
- Base every point on the numbers above and quote them, e.g. "RM186 on food,
  up from RM120 last week".
- Flag clearly but kindly the things that matter most: food / eating-out
  spending that's high or rising (name the biggest spending type); sugar above
  ~50 g a day (WHO: under 50 g of free sugars, ideally under 25 g; say these are
  rough estimates), naming the main sugary items; calories well above or below
  the goal; water below the goal; fewer workouts than before; sleep under 7 h;
  supplements often missed.
- Each suggestion is small, specific and doable, with Malaysian-friendly ideas
  (teh o kurang manis instead of teh tarik, bring lunch from home twice, a
  15-minute walk after dinner, a water bottle on the desk).
- Celebrate real improvements compared with earlier periods.
- Weight: neutral and kind, focus on habits not the number. Never shame.
- If only a few days were logged, say so gently and don't over-read the numbers.
- Only use numbers given here; never invent any. Plain text, emojis as headers,
  no Markdown.
"""


def build_weekly_prompt(day: date) -> str | None:
    """Everything Gemini needs for the report on the week containing `day`."""
    start, end = week_range(day)
    entries = entries_between(start, end)
    if not entries:
        return None
    earlier = []
    for weeks_back in (1, 2, 3):
        s, e = week_range(day - timedelta(weeks=weeks_back))
        numbers = stats(s, e)
        if numbers["entries_logged"]:
            earlier.append(numbers)
    return "\n".join([
        f"Write my weekly report for the week {start} to {end}.",
        f"\nGOALS:\n{get_goals() or 'none set yet'}",
        f"\nTHIS WEEK:\n{stats(start, end, entries)}",
        f"\nMONTHLY BUDGETS (month so far):\n{tracker.get_budgets() or 'none set'}",
        f"\nMY LAST 3 WEEKS (to compare):\n{earlier or 'no earlier weeks logged'}",
        "\nPER DAY:\n" + ("\n".join(_daily_lines(entries)) or "none"),
        "\nWHAT I ATE AND DRANK:\n" + ("\n".join(_food_lines(entries)) or "none"),
        "\nEVERYTHING ELSE:\n" + ("\n".join(_other_lines(entries)) or "none"),
        """
Structure (about 15-20 short lines):
📊 This week: kcal/day, sugar/day, water/day, steps/day, workouts + km, money
   spent (top types), sleep, supplements, tasks done. Only the ones with data.
🌟 Wins: 2-3 things that went well
⚠️ Watch-outs: the 1-3 most important ones (e.g. "you spent too much on food",
   "you had a lot of sugar")
💡 Next week: 3 specific suggestions
📈 vs your last weeks: one or two lines on the trend""",
        COACHING,
    ])


def build_report_prompt(year: int, month: int) -> str | None:
    """Everything Gemini needs for the monthly report, or None if the month is empty."""
    start, end = month_range(year, month)
    entries = entries_between(start, end)
    if not entries:
        return None
    trend = []  # the months before, oldest first, for "habits vs your other months"
    y, m = year, month
    for _ in range(5):
        y, m = previous_month(y, m)
        numbers = stats(*month_range(y, m))
        if numbers["entries_logged"]:
            trend.insert(0, {"month": f"{MONTH_NAMES[m - 1]} {y}", **numbers})
    six_months_ago = (date(year, month, 1) - timedelta(days=150)).isoformat()
    weights = [f"{e['logged_at'][:10]}: {e['amount']:g} kg"
               for e in entries_between(six_months_ago, tracker.today()) if e["category"] == "weight"]
    return "\n".join([
        f"Write my personal growth report for {MONTH_NAMES[month - 1]} {year}.",
        f"\nGOALS:\n{get_goals() or 'none set yet'}",
        f"\nTHIS MONTH:\n{stats(start, end, entries)}",
        f"\nMONTHLY BUDGETS (current month so far):\n{tracker.get_budgets() or 'none set'}",
        f"\nBILLS & SUBSCRIPTIONS (fixed costs):\n{bills.list_bills() or 'none'}",
        f"\nMY EARLIER MONTHS (oldest first, to compare habits):\n{trend or 'no earlier months logged'}",
        f"\nWEIGHT HISTORY (last ~6 months):\n{weights or 'no weight logged'}",
        "\nPER DAY:\n" + ("\n".join(_daily_lines(entries)) or "none"),
        "\nEVERYTHING ELSE I LOGGED:\n" + ("\n".join(_other_lines(entries)) or "none"),
        """
Structure:
🌟 Highlights — the 3 best things about this month
💪 Health — kcal, sugar and protein per day vs goals; water; steps per day;
   workouts (count, km, minutes, kcal burned); supplement consistency; sleep;
   weight trend
🩸 Cycle — period start dates, cycle length, symptoms pattern (skip if no data)
✅ Tasks — to-dos done, notable ones, daily-habit consistency (skip if none)
💼 Work & 📚 Study — what I achieved, plus 2-4 resume-ready bullet points
   (start with a strong verb, include numbers where possible)
💸 Money — total spent, by type, biggest expenses, budgets kept or missed, and
   monthly fixed costs (bills + subscriptions)
📈 Trends vs my other months — for each habit that has data: better, same or
   worse, with the numbers (e.g. "sugar 62 -> 48 g/day")
⚠️ Watch-outs — the 1-3 most important things to fix
🎯 Next month — 3 specific, realistic focus goals""",
        COACHING,
    ])
