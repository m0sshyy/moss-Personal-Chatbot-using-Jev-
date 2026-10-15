"""
The bot's memory: your data in Google Firestore, plus the "tools" Gemini can use.

How tools work:
    You: "had nasi lemak and 500ml water"
    Gemini reads that, decides to call log_entry() twice, and gets back a result.
    Then it replies to you in plain words ("Logged! 650 kcal so far today").

Gemini learns what each tool does from its *docstring* (the text in triple quotes)
and its *type hints* (str, float...). So those are written for Gemini to read.

Firestore is Google's cloud database (in Singapore, in your own Google Cloud
project). It stores "documents" (like little dicts) in "collections":
    entries        - one document per thing you logged
    goals          - one document per goal, named after it ("water", "calories")
    quick_buttons  - your one-tap supplement buttons
You can look inside at console.cloud.google.com -> Firestore -> Data.

Firestore can't add things up the way SQL can, so this file fetches the entries
for a date range and Python does the counting. At personal scale that's fast.
"""

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

# Malaysia is UTC+8 all year (no daylight saving), so a fixed offset is enough.
MALAYSIA = timezone(timedelta(hours=8), "MYT")

CATEGORIES = (
    "food",        # meals and snacks (calories)
    "water",       # amount in ml
    "drink",       # non-water drinks: coffee, teh tarik, bubble tea (calories)
    "supplement",  # vitamins, creatine, protein powder...
    "workout",     # gym sessions, runs, sports
    "period",      # item = "start", "end" or a symptom like "cramps"
    "work",        # tasks done, wins, hours worked
    "study",       # classes, courses, hours studied
    "expense",     # money spent, amount in RM
    "sleep",       # hours slept
    "weight",      # body weight in kg
    "mood",        # how the day felt
    "note",        # anything else worth remembering
    "task",        # a to-do ticked off (added automatically by complete_task)
    "steps",       # daily step count (synced from Apple Health, see siri.py)
)

# Optional numbers an entry can carry: exercise (workouts) and nutrition (food/drinks).
EXTRA_NUMBERS = ("distance_km", "duration_min", "burned_kcal", "sugar_g", "protein_g", "carbs_g", "fat_g")

_client = None


def db() -> firestore.Client:
    """The connection to Firestore, created the first time it's needed.
    On your PC it uses your `gcloud auth application-default login`; on
    Cloud Run, Google provides the login automatically."""
    global _client
    if _client is None:
        _client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    return _client


def _collection(name: str):
    """A Firestore collection. FIRESTORE_PREFIX (normally empty) lets tests use
    separate collections like "test_entries", so they never touch your real data."""
    return db().collection(os.environ.get("FIRESTORE_PREFIX", "") + name)


def now() -> datetime:
    return datetime.now(MALAYSIA)


def today() -> str:
    return now().strftime("%Y-%m-%d")


def setup_database() -> None:
    """Firestore needs no tables to be created. This just does one tiny read
    at startup, so a login problem shows up straight away with a clear error."""
    list(_collection("goals").limit(1).stream())


def entries_between(start_date: str, end_date: str) -> list[dict]:
    """Every entry from start_date to end_date (inclusive), oldest first.
    Each one is a dict with its document id under "id"."""
    query = (_collection("entries")
             .where(filter=firestore.FieldFilter("logged_at", ">=", start_date.strip()[:10]))
             .where(filter=firestore.FieldFilter("logged_at", "<", _day_after(end_date)))
             .order_by("logged_at"))
    return [{"id": doc.id, **doc.to_dict()} for doc in query.stream()]


# --- Tools Gemini can call -----------------------------------------------------
def log_entry(
    category: str,
    item: str,
    amount: float = 0,
    unit: str = "",
    calories: float = 0,
    notes: str = "",
    tag: str = "",
    logged_at: str = "",
    distance_km: float = 0,
    duration_min: float = 0,
    burned_kcal: float = 0,
    sugar_g: float = 0,
    protein_g: float = 0,
    carbs_g: float = 0,
    fat_g: float = 0,
) -> dict:
    """Save one thing the owner did, ate, drank, spent or felt.

    Args:
        category: One of: food, water, drink, supplement, workout, period, work,
            study, expense, sleep, weight, mood, note, task.
        item: Short name, e.g. "nasi lemak ayam", "brisk walk", "bench press",
            "Grab ride", "Python course week 3", "start" (for period).
        amount: The number that matters: 500 (ml water), 12.50 (RM), 45 (minutes),
            7.5 (hours sleep), 58.2 (kg). Use 0 if there is no number.
        unit: Unit for amount: ml, RM, min, hours, kg, sets, mg, serving...
        calories: kcal EATEN or DRUNK, for food and drinks only. Always 0 for
            workouts (calories burned go in burned_kcal).
        notes: Extra detail: workout sets/reps/weights, pace, shop name,
            symptoms, what was learned.
        tag: One-word group used for charts. Expenses: food, transport, bills,
            shopping, health, fun, education or other. Workouts: walk, run,
            cycling, legs, push, pull, cardio, sport, yoga, pilates... Study: the
            course name. Food: breakfast, lunch, dinner or snack. Leave empty for
            other categories.
        logged_at: When it happened, 'YYYY-MM-DD HH:MM'. Leave empty for right now.
        distance_km: Workouts: distance in km (e.g. 3.2 for a walk or run), else 0.
        duration_min: Workouts: how long in minutes, else 0.
        burned_kcal: Workouts: rough kcal burned, estimated from the activity,
            duration, distance and intensity (brisk walk ~4-5 kcal/min). Else 0.
        sugar_g, protein_g, carbs_g, fat_g: Food and drinks: estimated grams.
            Sugar = sugars incl. added sugar, syrup, condensed milk, sweet sauces
            (teh tarik ~20 g, can of soft drink ~35 g, bubble tea ~40-50 g).
    """
    category = category.strip().lower()
    if category not in CATEGORIES:
        return {"error": f"Unknown category '{category}'. Use one of: {', '.join(CATEGORIES)}"}
    when = logged_at.strip().replace("T", " ")[:16] or now().strftime("%Y-%m-%d %H:%M")
    entry = {
        "logged_at": when, "category": category, "item": item.strip(),
        "amount": amount, "unit": unit.strip(), "calories": calories,
        "notes": notes.strip(), "tag": tag.strip().lower(),
    }
    # Exercise and nutrition numbers are only stored when given, to keep other entries small.
    given = locals()
    entry.update({field: given[field] for field in EXTRA_NUMBERS if given[field]})
    _, doc = _collection("entries").add(entry)
    # Include today's totals so Gemini can report the running total straight
    # away, without a second round trip to call summarise().
    day = when[:10]
    result = {"saved": True, "entry_id": doc.id, "logged_at": when,
              f"totals_for_{day}": summarise(day, day)}
    if category == "expense":  # how the monthly budgets look now (and any 80% / 100% warnings)
        result.update(budget_check(entry["tag"] or "other", amount, when[:7]))
    return result


def save_daily(category: str, item: str, amount: float, unit: str, day: str, **numbers) -> None:
    """One entry per day for numbers that are synced (Apple Health steps, sleep):
    syncing the same day again replaces it instead of adding a duplicate."""
    entry = {"logged_at": f"{day} 23:59", "category": category, "item": item, "amount": amount,
             "unit": unit, "calories": 0, "notes": "from Apple Health", "tag": "apple-health",
             **{field: value for field, value in numbers.items() if value}}
    _collection("entries").document(f"health-{category}-{day}").set(entry)


def find_entries(start_date: str, end_date: str, category: str = "") -> list[dict]:
    """List saved entries between two dates (inclusive), newest first.

    Use this to answer questions like "what did I eat yesterday?", to find an
    entry's id before deleting it, or to review gym/study/work history.

    Args:
        start_date: 'YYYY-MM-DD'
        end_date: 'YYYY-MM-DD'
        category: Optional, only return this category.
    """
    category = category.strip().lower()
    found = [e for e in entries_between(start_date, end_date)
             if not category or e["category"] == category]
    return found[::-1][:300]  # newest first


def summarise(start_date: str, end_date: str) -> list[dict]:
    """Totals per category (and unit) between two dates, inclusive.

    Use this for "how much water today?", "calories this week?",
    "how much did I spend this month?", "how far did I walk this week?",
    "how much sugar today?". Returns count, total amount and total calories
    eaten for each category/unit pair, plus sugar/protein/carbs/fat grams for
    food and drinks, and km, minutes and kcal burned for workouts.

    Args:
        start_date: 'YYYY-MM-DD'
        end_date: 'YYYY-MM-DD'
    """
    sums = ("amount", "calories", *EXTRA_NUMBERS)
    groups = defaultdict(lambda: {"entries": 0, "days": set(), **{field: 0.0 for field in sums}})
    for e in entries_between(start_date, end_date):
        group = groups[(e["category"], e.get("unit") or "")]
        group["entries"] += 1
        group["days"].add(e["logged_at"][:10])
        for field in sums:
            group[field] += e.get(field) or 0
    totals = []
    for (category, unit), g in sorted(groups.items()):
        total = {"category": category, "unit": unit, "entries": g["entries"],
                 "total_amount": round(g["amount"], 2), "total_calories": round(g["calories"]),
                 "days_with_entries": len(g["days"])}
        # Only mention exercise / nutrition totals where there are some.
        total.update({f"total_{field}": round(g[field], 1) for field in EXTRA_NUMBERS if g[field]})
        totals.append(total)
    return totals


def delete_entry(entry_id: str) -> dict:
    """Delete one entry by its id (find the id first with find_entries).
    Use for "undo", "remove that" or to correct a mistake before re-logging."""
    doc = _collection("entries").document(str(entry_id))
    existed = doc.get().exists
    doc.delete()
    return {"deleted": existed, "entry_id": entry_id}


def update_entry(
    entry_id: str,
    item: str = "",
    amount: float = -1,
    unit: str = "",
    calories: float = -1,
    notes: str = "",
    tag: str = "",
    logged_at: str = "",
    distance_km: float = -1,
    duration_min: float = -1,
    burned_kcal: float = -1,
    sugar_g: float = -1,
    protein_g: float = -1,
    carbs_g: float = -1,
    fat_g: float = -1,
) -> dict:
    """Correct an entry that's already saved, e.g. when the owner says "that was
    half a portion", "no walnuts", "it was a crepe, not a waffle", "that was
    yesterday", "it was 4 km not 3", "it was kurang manis". Only the fields you
    fill in change; for "half a portion" halve the kcal AND the grams. Find the
    entry_id in the earlier log_entry result, or with find_entries.

    Args:
        entry_id: The entry to change.
        item: New name, or empty to keep.
        amount: New amount, or -1 to keep.
        unit: New unit, or empty to keep.
        calories: New kcal eaten estimate, or -1 to keep.
        notes: New notes (replaces the old ones), or empty to keep.
        tag: New tag, or empty to keep.
        logged_at: New time 'YYYY-MM-DD HH:MM', or empty to keep.
        distance_km: New workout distance, or -1 to keep.
        duration_min: New workout duration, or -1 to keep.
        burned_kcal: New kcal burned estimate, or -1 to keep.
        sugar_g, protein_g, carbs_g, fat_g: New grams, or -1 to keep.
    """
    doc = _collection("entries").document(str(entry_id))
    if not doc.get().exists:
        return {"error": f"No entry with id {entry_id}. Use find_entries to look it up."}
    changes = {key: value for key, value in {
        "item": item.strip(), "unit": unit.strip(), "notes": notes.strip(), "tag": tag.strip().lower(),
        "logged_at": logged_at.strip().replace("T", " ")[:16],
    }.items() if value}
    given = locals()
    changes.update({field: given[field] for field in ("amount", "calories", *EXTRA_NUMBERS)
                    if given[field] >= 0})
    doc.update(changes)
    entry = doc.get().to_dict()
    day = entry["logged_at"][:10]
    return {"updated": True, "entry_id": entry_id, "now": entry, f"totals_for_{day}": summarise(day, day)}


def set_goal(name: str, target: float, unit: str) -> dict:
    """Create or change one of the owner's goals.

    Args:
        name: Short goal name, e.g. "water", "calories", "gym", "study", "spending".
        target: The number to aim for, e.g. 2500.
        unit: What the number means, e.g. "ml per day", "kcal per day",
            "sessions per week", "hours per week", "RM per month".
    """
    name = name.strip().lower()
    _collection("goals").document(name).set({"name": name, "target": target, "unit": unit.strip()})
    return {"saved": True, "goal": name, "target": target, "unit": unit}


def get_goals() -> list[dict]:
    """List all of the owner's goals, to compare their progress against."""
    return [doc.to_dict() for doc in _collection("goals").order_by("name").stream()]


# --- Monthly budgets -------------------------------------------------------------
# A budget is a monthly limit in RM for one spending type (the "tag" of expense
# entries: food, transport, shopping...) or "total" for all spending together.
SPENDING_TYPES = ("total", "food", "transport", "shopping", "bills", "health", "fun", "education", "other")
WARN_AT = (80, 100)  # percent of a budget at which a warning is given


def set_budget(spending_type: str, monthly_rm: float) -> dict:
    """Set (or change) a monthly spending budget, e.g. "my food budget is RM400"
    or "keep my total spending under RM1500 a month". 0 removes the budget.

    Args:
        spending_type: total, food, transport, shopping, bills, health, fun,
            education or other.
        monthly_rm: The monthly limit in RM.
    """
    kind = spending_type.strip().lower()
    if kind not in SPENDING_TYPES:
        return {"error": f"Unknown spending type '{kind}'. Use one of: {', '.join(SPENDING_TYPES)}"}
    if monthly_rm <= 0:
        _collection("budgets").document(kind).delete()
        return {"removed": True, "spending_type": kind}
    _collection("budgets").document(kind).set({"type": kind, "monthly_rm": monthly_rm})
    return {"saved": True, "spending_type": kind, "monthly_rm": monthly_rm}


def _spent_this_month(month: str) -> dict[str, float]:
    """RM spent per spending type (plus "total") in a month 'YYYY-MM'."""
    first = f"{month}-01"
    last = (datetime.strptime(first, "%Y-%m-%d").replace(day=28) + timedelta(days=4)).replace(day=1) \
        - timedelta(days=1)
    spent = defaultdict(float)
    for e in entries_between(first, last.strftime("%Y-%m-%d")):
        if e["category"] == "expense":
            spent[e.get("tag") or "other"] += e.get("amount") or 0
            spent["total"] += e.get("amount") or 0
    return spent


def get_budgets() -> list[dict]:
    """The owner's monthly budgets with how much is spent and left this month.
    Use for "how much budget do I have left?", "am I over budget?"."""
    budgets = [doc.to_dict() for doc in _collection("budgets").stream()]
    if not budgets:
        return []
    spent = _spent_this_month(now().strftime("%Y-%m"))
    return [{"spending_type": b["type"], "monthly_rm": b["monthly_rm"],
             "spent_rm": round(spent[b["type"]], 2), "left_rm": round(b["monthly_rm"] - spent[b["type"]], 2),
             "percent_used": round(spent[b["type"]] / b["monthly_rm"] * 100)}
            for b in sorted(budgets, key=lambda b: b["type"])]


def budget_check(tag: str, amount: float, month: str) -> dict:
    """After an expense: the budgets it counts towards, and a warning for each
    one that just went past 80% or 100% this month."""
    budgets = {doc.id: doc.to_dict()["monthly_rm"] for doc in _collection("budgets").stream()}
    relevant = [kind for kind in (tag, "total") if kind in budgets]
    if not relevant:
        return {}
    spent = _spent_this_month(month)
    status, warnings = [], []
    for kind in relevant:
        limit, after = budgets[kind], spent[kind]
        before = after - (amount or 0)
        status.append({"spending_type": kind, "monthly_rm": limit, "spent_rm": round(after, 2),
                       "left_rm": round(limit - after, 2), "percent_used": round(after / limit * 100)})
        crossed = [level for level in WARN_AT if before < limit * level / 100 <= after]
        if crossed:
            warnings.append(f"{kind} budget just passed {max(crossed)}%: RM{after:.2f} of RM{limit:g} this month")
    return {"budgets": status, **({"budget_warnings": warnings} if warnings else {})}


def add_quick_button(
    item: str,
    category: str = "supplement",
    amount: float = 0,
    unit: str = "",
    calories: float = 0,
    tag: str = "",
) -> dict:
    """Add a one-tap button to the owner's 💊 Supplements panel, for something
    they log often (supplements, a usual coffee, a daily vitamin...). One call per button.

    Args:
        item: Button name exactly as the owner wrote it, e.g. "Fish Oil", "Vit C".
        category: Usually "supplement". Also allowed: drink, food, workout...
        amount: Usual dose, e.g. 1000 (IU), 5 (g). 0 if not given.
        unit: Unit for amount: IU, mg, g, tablet, capsule, scoop...
        calories: kcal, for food/drink buttons. Otherwise 0.
        tag: Optional group, like in log_entry.
    """
    category = category.strip().lower()
    if category not in CATEGORIES:
        return {"error": f"Unknown category '{category}'. Use one of: {', '.join(CATEGORIES)}"}
    _, doc = _collection("quick_buttons").add({
        "category": category, "item": item.strip(), "amount": amount, "unit": unit.strip(),
        "calories": calories, "tag": tag.strip().lower(), "created": now().isoformat(),
    })
    return {"added": True, "button_id": doc.id,
            "tip": "The owner opens the panel with the 💊 Supplements button or /supps."}


def list_quick_buttons() -> list[dict]:
    """List the owner's quick buttons (with their button_id), in the order they were added."""
    docs = _collection("quick_buttons").order_by("created").stream()
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]


def remove_quick_button(button_id: str) -> dict:
    """Remove a quick button (find its button_id with list_quick_buttons).
    This only removes the button, never anything already logged."""
    doc = _collection("quick_buttons").document(str(button_id))
    existed = doc.get().exists
    doc.delete()
    return {"removed": existed, "button_id": button_id}


# --- Tasks (to-do list) ---------------------------------------------------------
# One-off tasks stay on the list until ticked. Repeating tasks ("daily", or set
# weekdays like "mon,tue,wed,thu,fri") come back unticked on each of their days.
# Ticking any task also logs a "task" entry, so finished tasks show up in
# reports and the Excel export.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DRAFT_KINDS = ("daily_log", "work_report")  # see drafts.py


def normalise_repeat(text: str) -> str:
    """'' (one-off), 'daily', or weekday codes like 'tue,fri'.
    Understands "weekdays", "weekends", "Tuesday and Friday", "tue, fri"..."""
    text = text.strip().lower()
    if not text or text in ("none", "once", "one-off"):
        return ""
    if text in ("daily", "every day", "everyday"):
        return "daily"
    if text in ("weekdays", "weekday", "workdays", "mon-fri"):
        return "mon,tue,wed,thu,fri"
    if text in ("weekends", "weekend"):
        return "sat,sun"
    days = [day for day in WEEKDAYS if day in text]  # "tuesday" contains "tue"
    return "daily" if len(days) == 7 else ",".join(days)


def repeats_on(task: dict, day) -> bool:
    """Is this repeating task due on `day` (a date)?"""
    return task["repeat"] == "daily" or WEEKDAYS[day.weekday()] in task["repeat"].split(",")


def add_task(title: str, due_date: str = "", repeat: str = "", notes: str = "",
             remind_at: str = "", draft: str = "") -> dict:
    """Add a to-do to the owner's task list (one call per task). Also used for
    "remind me to ..." requests: give remind_at and the bot sends a reminder.

    Args:
        title: Short task, e.g. "Pay TNB bill", "Submit assignment 2", "Skincare", "Call mum".
        due_date: 'YYYY-MM-DD' if it has a deadline or a planned day, else empty.
        repeat: "" for a one-off task (stays until done); "daily" for a habit done
            every day (skincare, make bed); or the weekdays it repeats on, as
            codes: "mon,tue,wed,thu,fri" (weekdays), "tue,fri", "sat,sun"...
        notes: Optional extra detail.
        remind_at: When to send a reminder message: 'YYYY-MM-DD HH:MM' for a
            one-off task, or just 'HH:MM' for a repeating task (reminds on each
            of its days). Empty = no reminder. Arrives within ~15 min of the time.
        draft: For work write-ups, the reminder can come with a ready-made draft:
            "daily_log" (e.g. a log book entry from today's work
            notes) or "work_report" (a report to a supervisor covering the days
            since the last one). Empty for everything else.
    """
    repeat = normalise_repeat(repeat)
    _, doc = _collection("tasks").add({
        "title": title.strip(), "due": due_date.strip()[:10], "repeat": repeat, "notes": notes.strip(),
        "remind_at": remind_at.strip().replace("T", " ")[:16], "reminded": False,
        "draft": draft.strip().lower() if draft.strip().lower() in DRAFT_KINDS else "",
        "done": False, "done_at": "", "created": now().isoformat(),
    })
    return {"added": True, "task_id": doc.id,
            "tip": "The owner ticks tasks off with the 📝 Tasks button or /tasks."}


def list_tasks(include_finished: bool = False) -> list[dict]:
    """The owner's tasks with their task_id, due date, repeat and whether they're
    done (repeating tasks: done today). Finished one-off tasks are left out unless
    include_finished is true."""
    done_today = logged_today()
    tasks = []
    for doc in _collection("tasks").order_by("created").stream():
        task = {"task_id": doc.id, **doc.to_dict()}
        if task["repeat"]:
            task["done_today"] = ("task", task["title"].lower()) in done_today
        elif task["done"] and not include_finished:
            continue
        tasks.append(task)
    return tasks


def complete_task(task_id: str) -> dict:
    """Tick a task off (find its task_id with list_tasks)."""
    doc = _collection("tasks").document(str(task_id))
    snapshot = doc.get()
    if not snapshot.exists:
        return {"error": f"No task with id {task_id}. Use list_tasks to look it up."}
    task = snapshot.to_dict()
    if not task["repeat"]:
        doc.update({"done": True, "done_at": now().strftime("%Y-%m-%d %H:%M")})
    tag = {"": "to-do", "daily": "daily"}.get(task["repeat"], "weekly")
    log_entry("task", task["title"], tag=tag, notes=task.get("notes", ""))
    return {"completed": True, "title": task["title"], "repeat": task["repeat"]}


def update_task(task_id: str, title: str = "", due_date: str = "", notes: str = "",
                remind_at: str = "", repeat: str = "", draft: str = "") -> dict:
    """Rename a task, move its due date ("move X to Friday"), change its notes,
    change which days it repeats on, or set/move/remove its reminder. Only the
    fields you fill in change.

    Args:
        task_id: The task to change (from list_tasks).
        title: New title, or empty to keep.
        due_date: New 'YYYY-MM-DD', "none" to remove the date, or empty to keep.
        notes: New notes, or empty to keep.
        remind_at: New reminder time ('YYYY-MM-DD HH:MM', or 'HH:MM' for a
            repeating task), "none" to remove the reminder, or empty to keep.
        repeat: New repeat ("daily", "mon,wed,fri"...), "none" to make it a
            one-off task, or empty to keep.
        draft: "daily_log" or "work_report" to send a ready-made draft with its
            reminder, "none" to stop, or empty to keep.
    """
    doc = _collection("tasks").document(str(task_id))
    if not doc.get().exists:
        return {"error": f"No task with id {task_id}. Use list_tasks to look it up."}
    changes = {key: value.strip() for key, value in {"title": title, "notes": notes}.items() if value.strip()}
    if due_date.strip():
        changes["due"] = "" if due_date.strip().lower() == "none" else due_date.strip()[:10]
    if remind_at.strip():
        changes["remind_at"] = "" if remind_at.strip().lower() == "none" else remind_at.strip().replace("T", " ")[:16]
        changes["reminded"] = False
    if repeat.strip():
        changes["repeat"] = normalise_repeat(repeat)
    if draft.strip():
        changes["draft"] = draft.strip().lower() if draft.strip().lower() in DRAFT_KINDS else ""
    doc.update(changes)
    return {"updated": True, "task": doc.get().to_dict()}


def delete_task(task_id: str) -> dict:
    """Remove a task from the list for good (e.g. "I don't need to do X anymore",
    or to stop a daily task). Ticked-off history stays in the logs."""
    doc = _collection("tasks").document(str(task_id))
    existed = doc.get().exists
    doc.delete()
    return {"deleted": existed, "task_id": task_id}


NIGHT_FROM = "20:00"  # repeating tasks timed from 8pm (skincare...) only show from 8pm


def shows_yet(task: dict) -> bool:
    """A night-time repeating task (reminder at 8pm or later) stays out of the way until 8pm."""
    at = task.get("remind_at", "")
    return not (task["repeat"] and len(at) == 5 and at >= NIGHT_FROM and now().strftime("%H:%M") < NIGHT_FROM)


def repeating_done_today() -> int:
    """How many of today's repeating tasks are ticked (they leave the 📝 panel once done)."""
    return sum(1 for task in list_tasks() if task["repeat"] and repeats_on(task, now().date())
               and task["done_today"])


def tasks_for_today() -> tuple[list[tuple[dict, bool]], int]:
    """For the 📝 Tasks panel: (task, done?) pairs to show today, plus how many
    one-off tasks are planned for later days. Ticked repeating tasks are left out
    (they'll be back on their next day); ticked one-off tasks stay for the day, so
    the panel shows what got done."""
    shown, later, today_ = [], 0, today()
    for task in list_tasks(include_finished=True):
        if task["repeat"]:
            # e.g. a Mon-Fri task isn't shown on Saturday, nor skincare at 9am
            if repeats_on(task, now().date()) and not task["done_today"] and shows_yet(task):
                shown.append((task, False))
        elif task["done"]:
            if task["done_at"][:10] == today_:  # finished today: still shown, ticked
                shown.append((task, True))
        elif task["due"] and task["due"] > today_:
            later += 1
        else:
            shown.append((task, False))
    # Repeating tasks first, then by due date (overdue first; no date last).
    shown.sort(key=lambda pair: (not pair[0]["repeat"], pair[0]["due"] or "9999"))
    return shown, later


def task_is_done(task: dict, done_today: set | None = None) -> bool:
    """One-off: ticked at all. Repeating: ticked today."""
    if task["repeat"]:
        return ("task", task["title"].lower()) in (done_today if done_today is not None else logged_today())
    return task["done"]


def untick_task(task_id: str) -> dict | None:
    """For the 📝 Tasks panel: undo a tick (mis-tap)."""
    doc = _collection("tasks").document(str(task_id))
    snapshot = doc.get()
    if not snapshot.exists:
        return None
    task = snapshot.to_dict()
    if not task["repeat"]:
        doc.update({"done": False, "done_at": ""})
    undo_latest_today("task", task["title"])
    return task


# The list handed to Gemini. Add a function here and Gemini can start using it.
TOOLS = [log_entry, find_entries, summarise, update_entry, delete_entry, set_goal, get_goals,
         set_budget, get_budgets, add_quick_button, list_quick_buttons, remove_quick_button,
         add_task, list_tasks, complete_task, update_task, delete_task]


# --- Used by the quick buttons directly (no Gemini involved) -------------------
def quick_log(button_id: str) -> dict | None:
    """Log the entry a quick button stands for. None if the button is gone."""
    doc = _collection("quick_buttons").document(str(button_id)).get()
    if not doc.exists:
        return None
    b = doc.to_dict()
    saved = log_entry(b["category"], b["item"], b.get("amount") or 0, b.get("unit") or "",
                      b.get("calories") or 0, tag=b.get("tag") or "")
    return {**saved, "item": b["item"]}


def logged_today() -> set[tuple[str, str]]:
    """(category, item) pairs already logged today, so buttons can show a ✓."""
    return {(e["category"], e["item"].lower()) for e in entries_between(today(), today())}


def undo_latest_today(category: str, item: str = "") -> dict | None:
    """Delete today's most recent entry of this category (and item, if given).
    Used by the ↩️ Undo / untick buttons. Returns what was deleted, or None."""
    matches = [e for e in entries_between(today(), today())
               if e["category"] == category and (not item or e["item"].lower() == item.lower())]
    if not matches:
        return None
    latest = matches[-1]  # entries come oldest first
    _collection("entries").document(latest["id"]).delete()
    return latest


def water_today() -> float:
    return sum(e.get("amount") or 0 for e in entries_between(today(), today())
               if e["category"] == "water")


def first_entry_date() -> str | None:
    """The date of your very first entry (for /export all)."""
    first = list(_collection("entries").order_by("logged_at").limit(1).stream())
    return first[0].to_dict()["logged_at"][:10] if first else None


def _day_after(date: str) -> str:
    """'2026-10-13' -> '2026-10-14'. Lets 'up to end_date' include that whole day."""
    return (datetime.strptime(date.strip()[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
