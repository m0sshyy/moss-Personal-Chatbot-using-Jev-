"""
The web dashboard: charts of your habits, money and health, served by the
cloud bot itself at  <bot address>/dashboard  (see cloud_server.py).

Logging in, with no password:
    1. Send /dashboard to Moss in Telegram.
    2. It replies with a one-time link (works once, for 15 minutes).
    3. Opening it sets a login cookie on that phone/browser for 30 days.
Only Telegram (= only you) can hand out links, so only you can see the page.

The page itself (PAGE below) is plain HTML + Chart.js: one colour and emoji per
metric, today's water as a filling glass and steps as a ring, and tapping any
bar shows that whole day at the top. It fetches the numbers
from  /dashboard/data  and draws them, so your data never leaves your cloud.
"""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from datetime import timedelta

import bills
import period
import report_card
import tracker

LINK_MINUTES = 15
SESSION_DAYS = 30
# Firebase Hosting (moss-alya.web.app, see README) passes on only a cookie named "__session".
COOKIE = "__session"


# --- Logging in ------------------------------------------------------------------
def _signing_key() -> bytes:
    # Derived from the job secret, so there's no extra secret to manage.
    return hmac.new(os.environ["JOB_SECRET"].encode(), b"dashboard-session", hashlib.sha256).digest()


def new_login_link_token() -> str:
    """A one-time login token (sent as a link in Telegram)."""
    token = secrets.token_urlsafe(24)
    tracker._collection("dashboard_logins").document(token).set(
        {"expire_at": tracker.now() + timedelta(minutes=LINK_MINUTES)})  # Firestore deletes it later
    return token


def use_login_token(token: str) -> bool:
    """True once for a valid, unexpired token; it can't be used again."""
    if not token or len(token) > 100:
        return False
    doc = tracker._collection("dashboard_logins").document(token)
    saved = doc.get()
    if not saved.exists:
        return False
    doc.delete()
    return saved.to_dict()["expire_at"] > tracker.now()


def new_session() -> str:
    """The cookie value: an expiry time plus a signature only the bot can make."""
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    signature = hmac.new(_signing_key(), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def valid_session(cookie: str | None) -> bool:
    try:
        expires, signature = (cookie or "").split(".", 1)
        expected = hmac.new(_signing_key(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expires) > time.time()
    except ValueError:
        return False


# --- The numbers -----------------------------------------------------------------
def data(days: int = 30) -> dict:
    """Everything the page draws, for the last `days` days."""
    today = tracker.now().date()
    start = today - timedelta(days=days - 1)
    entries = tracker.entries_between(start.isoformat(), today.isoformat())
    daily = defaultdict(lambda: defaultdict(float))
    for e in entries:
        day, category = daily[e["logged_at"][:10]], e["category"]
        if category in ("food", "drink"):
            day["kcal"] += e.get("calories") or 0
            day["sugar"] += e.get("sugar_g") or 0
        elif category == "water":
            day["water"] += e.get("amount") or 0
        elif category == "steps":
            day["steps"] += e.get("amount") or 0
        elif category == "expense":
            day["spent"] += e.get("amount") or 0
        elif category == "workout":
            day["workout_min"] += e.get("duration_min") or 0
            day["workouts"] += 1
        elif category == "sleep":
            day["sleep"] += e.get("amount") or 0
        elif category == "task":
            day["tasks"] += 1
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    series = {key: [round(daily[d][key], 1) for d in dates]
              for key in ("kcal", "sugar", "water", "steps", "spent", "workout_min", "workouts", "sleep", "tasks")}

    month = today.strftime("%Y-%m")
    by_type = defaultdict(float)
    for e in tracker.entries_between(f"{month}-01", today.isoformat()):
        if e["category"] == "expense":
            by_type[e.get("tag") or "other"] += e.get("amount") or 0
    goals = {g["name"]: g["target"] for g in tracker.get_goals()}
    supplements = tracker.list_quick_buttons()
    taken = tracker.logged_today()
    cycle = period.cycle_status()
    weights = [{"date": e["logged_at"][:10], "kg": e["amount"]}
               for e in tracker.entries_between((today - timedelta(days=365)).isoformat(), today.isoformat())
               if e["category"] == "weight" and e.get("amount")]
    today_key = today.isoformat()
    return {
        "today": {"date": f"{today:%A %d %B %Y}", "kcal": round(daily[today_key]["kcal"]),
                  "water": round(daily[today_key]["water"]), "steps": round(daily[today_key]["steps"]),
                  "spent": round(daily[today_key]["spent"], 2),
                  "supplements_taken": sum(1 for b in supplements if (b["category"], b["item"].lower()) in taken),
                  "supplements_total": len(supplements)},
        "owner": os.environ.get("OWNER_NAME", ""),
        "goals": {"water": goals.get("water"), "calories": goals.get("calories"), "steps": goals.get("steps")},
        "dates": dates, "series": series,
        "month": {"name": f"{today:%B %Y}", "spent": round(sum(by_type.values()), 2),
                  "by_type": sorted(([kind, round(rm, 2)] for kind, rm in by_type.items()), key=lambda kv: -kv[1])},
        "budgets": tracker.get_budgets(),
        "bills": sorted(bills.list_bills(), key=lambda b: b["days_until_due"]),
        "cycle": {key: cycle.get(key) for key in ("has_data", "cycle_day", "phase", "next_period",
                                                  "days_until_next", "average_cycle_days")},
        "weights": weights,
        "report_card": report_card_summary(),
    }


def report_card_summary() -> dict:
    """The latest report card (report_card.py) and the overall score of each run."""
    cards = report_card.history()
    return {"latest": cards[-1] if cards else None,
            "history": [{"run_at": card["run_at"], "overall": card["overall"]} for card in cards]}


# --- The page ------------------------------------------------------------------------
LOGIN_NEEDED = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Moss dashboard</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;background:#fff7fa;color:#3a2a31;display:grid;
place-items:center;min-height:100vh;margin:0;padding:16px;text-align:center}
@media (prefers-color-scheme:dark){body{background:#211a1e;color:#f3e9ee}}</style></head>
<body><div><p style="font-size:40px;margin:0">🎀</p><h1 style="font-size:20px">Moss dashboard</h1>
<p>{message}</p></div></body></html>"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#fff3f7">
<title>Moss dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --page: #fff3f7; --card: #ffffff; --line: #f7e1ea;
    --text: #33262c; --text-2: #75606a; --muted: #a8939c; --grid: #f1edef;
    --pink: #d6336c; --water: #2a78d6; --kcal: #e8590c; --sugar: #d6336c;
    --steps: #2b8a3e; --spent: #7048e8; --workout: #0c8599;
    --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--text);
         font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 16px 16px 28px; }
  button { font: inherit; color: inherit; cursor: pointer; }

  /* header */
  header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
  h1 { font-size: 24px; margin: 0; }
  .date { color: var(--text-2); margin: 2px 0 0; }
  .range { display: flex; background: var(--card); border-radius: 999px; padding: 3px;
           box-shadow: 0 1px 3px rgba(214, 51, 108, .12); }
  .range button { border: 0; background: none; border-radius: 999px; padding: 6px 14px; color: var(--text-2); }
  .range button[aria-pressed="true"] { background: var(--pink); color: #fff; font-weight: 600; }

  /* cards */
  .card { background: var(--card); border-radius: 20px; padding: 14px 16px;
          box-shadow: 0 1px 2px rgba(51, 38, 44, .04), 0 4px 14px rgba(214, 51, 108, .07); }

  /* today */
  .today { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }
  .today .card { display: flex; align-items: center; gap: 12px; min-height: 92px; }
  .big { font-size: 22px; font-weight: 700; line-height: 1.15; }
  .small { color: var(--text-2); font-size: 13px; }
  .glass { width: 40px; height: 56px; flex: none; border: 3px solid #b9d3f3; border-top: 0;
           border-radius: 4px 4px 12px 12px; position: relative; overflow: hidden; background: #f4f8fd; }
  .glass .fill { position: absolute; left: 0; right: 0; bottom: 0; height: 0;
                 background: linear-gradient(#6aa7ec, var(--water)); transition: height 1s ease; }
  .glass .fill::before { content: ""; position: absolute; left: -10%; right: -10%; top: -4px; height: 8px;
                         background: #8fbdf0; border-radius: 50%; }
  .ring { width: 56px; height: 56px; flex: none; transform: rotate(-90deg); }
  .ring circle { fill: none; stroke-width: 7; }
  .ring .track { stroke: #e3f2e6; }
  .ring .value { stroke: var(--steps); stroke-linecap: round; transition: stroke-dasharray 1s ease; }
  .icon { font-size: 32px; width: 44px; text-align: center; flex: none; }
  .meter { height: 8px; background: var(--grid); border-radius: 4px; overflow: hidden; margin-top: 6px; width: 100%; }
  .meter span { display: block; height: 100%; border-radius: 4px; transition: width 1s ease; }
  .dots { letter-spacing: 3px; color: var(--pink); font-size: 18px; }

  /* the day you tapped */
  .day { margin: 0 0 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
         border: 2px solid #f6c5d6; position: relative; padding-right: 48px; }
  .day[hidden] { display: none; }
  .day h2 { font-size: 16px; margin: 0 6px 0 0; }
  .day .close { position: absolute; top: 10px; right: 10px; border: 0; background: var(--page);
                border-radius: 999px; width: 30px; height: 30px; }
  .chip { background: var(--page); border-radius: 999px; padding: 4px 10px; font-size: 13px; white-space: nowrap; }
  .tip { text-align: center; color: var(--muted); font-size: 13px; margin: -6px 0 12px; }

  /* charts */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 12px; margin-bottom: 12px; }
  .chart-card .top { display: flex; align-items: center; gap: 8px; }
  .chart-card h2 { font-size: 17px; margin: 0; }
  .chart-card .numbers-btn { margin-left: auto; border: 0; background: var(--page); border-radius: 10px; padding: 3px 8px; font-size: 13px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 6px; }
  .chart { position: relative; height: 190px; }
  .numbers { margin-top: 8px; max-height: 220px; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td { padding: 4px 2px; border-bottom: 1px solid var(--grid); }
  td:last-child { text-align: right; font-variant-numeric: tabular-nums; }

  /* lists */
  .list-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--grid); }
  .list-row:last-child { border-bottom: 0; }
  .list-row .name { flex: 1; }
  .list-row .meter { margin-top: 4px; }
  .state { font-size: 12px; white-space: nowrap; }
  .empty { color: var(--muted); margin: 6px 0; }
  .cycle-big { font-size: 30px; font-weight: 700; }
  /* report card */
  .rc { margin-bottom: 12px; }
  .rc-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
  .rc-head h2 { font-size: 17px; margin: 0; }
  .rc-top { display: flex; align-items: center; gap: 18px; margin-top: 8px; }
  .rc-overall { text-align: center; flex: none; }
  .rc-big { font-size: 40px; font-weight: 800; color: var(--pink); line-height: 1; }
  .rc-scores { flex: 1; min-width: 0; }
  .rc h3 { font-size: 14px; margin: 6px 0 4px; color: var(--text-2); }
  .rc details { margin-top: 8px; }
  .rc summary { cursor: pointer; color: var(--text-2); font-size: 14px; padding: 4px 0; }
  .miss { padding: 7px 0; border-bottom: 1px solid var(--grid); font-size: 13px; }
  .miss:last-child { border-bottom: 0; }
  .miss .what { font-weight: 600; }
  footer { color: var(--muted); font-size: 12px; text-align: center; margin-top: 18px; }
  @media (max-width: 420px) {   /* small phones: slightly more compact tiles */
    .today { gap: 10px; }
    .today .card { gap: 8px; padding: 12px; }
    .icon { font-size: 26px; width: 30px; }
    .big { font-size: 20px; }
  }
</style>
</head>
<body>
<main>
  <header>
    <div><h1 id="hello">Hi 🎀</h1><p class="date" id="date"></p></div>
    <div class="range" role="group" aria-label="Show the last">
      <button data-days="7" aria-pressed="false">7 days</button>
      <button data-days="30" aria-pressed="true">30 days</button>
      <button data-days="90" aria-pressed="false">90 days</button>
    </div>
  </header>

  <section class="today" aria-label="Today">
    <div class="card" title="Water today"><div class="glass" aria-hidden="true"><div class="fill" id="glass"></div></div>
      <div><div class="big" id="t-water">–</div><div class="small" id="t-water-goal">💧 water</div></div></div>
    <div class="card" title="Steps today"><svg class="ring" viewBox="0 0 56 56" aria-hidden="true">
        <circle class="track" cx="28" cy="28" r="23"></circle><circle class="value" id="ring" cx="28" cy="28" r="23" stroke-dasharray="0 999"></circle></svg>
      <div><div class="big" id="t-steps">–</div><div class="small" id="t-steps-goal">👟 steps</div></div></div>
    <div class="card" title="Calories today"><div class="icon">🔥</div>
      <div style="flex:1"><div class="big" id="t-kcal">–</div><div class="small" id="t-kcal-goal">kcal</div>
        <div class="meter" id="t-kcal-meter" hidden><span style="background:var(--kcal)"></span></div></div></div>
    <div class="card" title="Money spent"><div class="icon">💸</div>
      <div style="flex:1"><div class="big" id="t-spent">–</div><div class="small" id="t-month">today</div>
        <div class="meter" id="t-month-meter" hidden><span style="background:var(--spent)"></span></div>
        <div class="small" id="t-month-total" style="margin-top:3px"></div></div></div>
    <div class="card" title="Supplements today"><div class="icon">💊</div>
      <div><div class="dots" id="t-dots">–</div><div class="small" id="t-supps">supplements</div></div></div>
  </section>

  <section class="card day" id="day" hidden aria-live="polite">
    <h2 id="day-title"></h2><div class="chips" id="day-chips" style="margin:0"></div>
    <button class="close" id="day-close" aria-label="Close">✕</button>
  </section>
  <p class="tip" id="tip">👆 Tap any bar to see that whole day</p>

  <section class="grid" id="charts"></section>

  <section class="grid">
    <div class="card"><div class="top" style="display:flex;gap:8px"><h2 style="font-size:17px;margin:0">💰 Budgets</h2></div><div id="budgets"></div></div>
    <div class="card"><h2 style="font-size:17px;margin:0">🛍️ Spent this month</h2><div class="chart" id="types-box" style="height:auto"><canvas id="types"></canvas></div></div>
    <div class="card"><h2 style="font-size:17px;margin:0">🧾 Bills &amp; subscriptions</h2><div id="bills"></div></div>
    <div class="card"><h2 style="font-size:17px;margin:0">🩸 Cycle</h2><div id="cycle"></div></div>
    <div class="card chart-card" id="weight-card" hidden><div class="top"><h2>⚖️ Weight</h2></div><div class="chips" id="weight-chips"></div><div class="chart"><canvas id="weight"></canvas></div></div>
  </section>
  <section class="card rc" id="rc" aria-label="Moss report card">
    <div class="rc-head"><h2>📊 Moss report card</h2><span class="small" id="rc-when"></span></div>
    <div id="rc-body"></div>
  </section>
  <footer>Only you can see this · Moss 🎀</footer>
</main>

<script>
const METRICS = [
  { key: "water", emoji: "💧", title: "Water", color: "#2a78d6", show: v => fmt(v / 1000, 1) + " L" },
  { key: "kcal", emoji: "🔥", title: "Calories", color: "#e8590c", show: v => fmt(v) + " kcal" },
  { key: "sugar", emoji: "🍬", title: "Sugar", color: "#d6336c", show: v => fmt(v) + " g" },
  { key: "steps", emoji: "👟", title: "Steps", color: "#2b8a3e", show: v => fmt(v) },
  { key: "spent", emoji: "💸", title: "Spending", color: "#7048e8", show: v => "RM" + fmt(v, 2) },
  { key: "workout_min", emoji: "🏃‍♀️", title: "Workouts", color: "#0c8599", show: v => fmt(v) + " min" },
];
const TYPE_EMOJI = { food: "🍜", shopping: "🛍️", transport: "🚗", bills: "🧾", health: "💊", fun: "🎮",
                     education: "📚", other: "✨", total: "💰" };
const charts = {};
let D = null, selected = null;

const fmt = (n, digits = 0) => Number(n).toLocaleString("en-MY", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const day = iso => new Date(iso + "T00:00:00");
const short = iso => day(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const long = iso => day(iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const chip = text => el("span", "chip", text);
const set = (id, text) => { document.getElementById(id).textContent = text; };
const sum = a => a.reduce((x, y) => x + y, 0);
const fade = hex => hex + "40";

/* ---------- today ---------- */
function today(d) {
  const t = d.today, g = d.goals;
  set("hello", "Hi " + (d.owner || "there") + " 🎀");
  set("date", t.date);
  set("t-water", fmt(t.water / 1000, 2) + " L");
  set("t-water-goal", g.water ? "💧 of " + fmt(g.water / 1000, 1) + " L" : "💧 water");
  document.getElementById("glass").style.height = Math.min(100, g.water ? t.water / g.water * 100 : t.water / 25) + "%";
  const stepGoal = g.steps || 10000;
  set("t-steps", fmt(t.steps));
  set("t-steps-goal", "👟 of " + fmt(stepGoal / 1000) + "k");
  document.getElementById("ring").setAttribute("stroke-dasharray", (Math.min(1, t.steps / stepGoal) * 144.5) + " 999");
  set("t-kcal", fmt(t.kcal));
  set("t-kcal-goal", g.calories ? "kcal of " + fmt(g.calories) : "kcal");
  meter("t-kcal-meter", g.calories ? t.kcal / g.calories : null);
  set("t-spent", "RM" + fmt(t.spent, 2));
  const total = d.budgets.find(b => b.spending_type === "total");
  set("t-month", "today");
  set("t-month-total", "🗓️ RM" + fmt(d.month.spent) + (total ? " / " + fmt(total.monthly_rm) : " this month"));
  meter("t-month-meter", total ? d.month.spent / total.monthly_rm : null);
  set("t-dots", t.supplements_total ? "●".repeat(t.supplements_taken) + "○".repeat(t.supplements_total - t.supplements_taken) : "–");
  set("t-supps", t.supplements_total ? t.supplements_taken + " of " + t.supplements_total + " taken" : "no supplements set");
}
function meter(id, share) {
  const m = document.getElementById(id);
  m.hidden = share === null;
  if (share !== null) requestAnimationFrame(() => m.firstElementChild.style.width = Math.min(100, share * 100) + "%");
}

/* ---------- charts ---------- */
function summary(m, values, d) {
  const logged = values.filter(v => v > 0), avg = logged.length ? sum(logged) / logged.length : 0;
  if (!logged.length) return ["nothing logged yet"];
  switch (m.key) {
    case "water": return ["avg " + m.show(avg) + "/day"].concat(d.goals.water ?
                    ["🎯 goal hit " + values.filter(v => v >= d.goals.water).length + " of " + values.length + " days"] : []);
    case "kcal": return ["avg " + m.show(avg) + "/day"].concat(d.goals.calories ? ["🎯 goal " + fmt(d.goals.calories)] : []);
    case "sugar": return ["avg " + m.show(avg) + "/day", "⚠️ over 50 g on " + values.filter(v => v > 50).length + " days"];
    case "steps": return ["avg " + m.show(avg) + "/day", "🏆 best " + m.show(Math.max(...values))];
    case "spent": return ["total " + m.show(sum(values)), "avg " + m.show(sum(values) / values.length) + "/day"];
    case "workout_min": return [fmt(sum(d.series.workouts)) + " workouts", fmt(sum(values)) + " min total"];
  }
}
function goalFor(m, d) {
  return { water: d.goals.water, kcal: d.goals.calories, sugar: 50, steps: d.goals.steps }[m.key] || null;
}
function buildCharts(d) {
  const box = document.getElementById("charts");
  box.replaceChildren();
  for (const m of METRICS) {
    const values = d.series[m.key];
    const card = el("div", "card chart-card");
    const top = el("div", "top");
    top.append(el("h2", "", m.emoji + " " + m.title));
    const btn = el("button", "numbers-btn", "🔢");
    btn.setAttribute("aria-label", "Show the numbers for " + m.title);
    top.append(btn);
    const chips = el("div", "chips");
    summary(m, values, d).forEach(text => chips.append(chip(text)));
    const area = el("div", "chart"), canvas = el("canvas");
    canvas.setAttribute("aria-label", m.title + " per day");
    area.append(canvas);
    const numbers = el("div", "numbers"); numbers.hidden = true;
    btn.onclick = () => { numbers.hidden = !numbers.hidden; if (!numbers.hidden) numbers.replaceChildren(table(m, d)); };
    card.append(top, chips, area, numbers);
    box.append(card);
    draw(m, canvas, d);
  }
}
function table(m, d) {
  const t = el("table");
  d.dates.map((iso, i) => [iso, d.series[m.key][i]]).filter(r => r[1] > 0).reverse()
    .forEach(([iso, v]) => { const r = t.insertRow(); r.insertCell().textContent = long(iso); r.insertCell().textContent = m.show(v); });
  return t.rows.length ? t : el("p", "empty", "Nothing logged yet.");
}
function draw(m, canvas, d) {
  charts[m.key]?.destroy();
  const goal = goalFor(m, d);
  const datasets = [{ data: d.series[m.key], backgroundColor: colors(m), borderRadius: 4, maxBarThickness: 16,
                      hoverBackgroundColor: m.color }];
  if (goal) datasets.push({ type: "line", data: d.dates.map(() => goal), borderColor: m.color + "99", borderWidth: 1.5,
                            borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 0, isGoal: true });
  charts[m.key] = new Chart(canvas, {
    type: "bar",
    data: { labels: d.dates.map(short), datasets },
    options: {
      maintainAspectRatio: false, animation: { duration: 500 },
      interaction: { mode: "index", intersect: false },
      onClick: (event, items, chart) => {
        const hit = chart.getElementsAtEventForMode(event, "index", { intersect: false }, false)[0];
        if (hit) select(hit.index);
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#33262c", padding: 10, displayColors: false,
          filter: item => !item.dataset.isGoal,
          callbacks: { title: items => long(d.dates[items[0].dataIndex]),
                       label: item => m.emoji + " " + m.show(item.parsed.y) + (goal ? "   🎯 " + m.show(goal) : "") },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false },
             ticks: { color: "#a8939c", maxTicksLimit: innerWidth < 600 ? 4 : 7, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: "#f1edef" }, border: { display: false },
             ticks: { color: "#a8939c", maxTicksLimit: 4, callback: v => m.key === "water" ? fmt(v / 1000, 1) + " L" : fmt(v) } },
      },
    },
  });
}
function colors(m) {
  const n = D.dates.length;
  return Array.from({ length: n }, (_, i) => selected === null || selected === i ? m.color : fade(m.color));
}

/* ---------- tapping a day ---------- */
function select(index) {
  selected = index;
  for (const m of METRICS) { charts[m.key].data.datasets[0].backgroundColor = colors(m); charts[m.key].update("none"); }
  const card = document.getElementById("day"), s = D.series;
  card.hidden = false;
  document.getElementById("tip").hidden = true;
  set("day-title", "📅 " + long(D.dates[index]));
  const chips = document.getElementById("day-chips");
  chips.replaceChildren(...METRICS.map(m => chip(m.emoji + " " + (s[m.key][index] ? m.show(s[m.key][index]) : "–"))));
  if (s.tasks[index]) chips.append(chip("✅ " + s.tasks[index] + " tasks"));
  if (s.sleep[index]) chips.append(chip("😴 " + fmt(s.sleep[index], 1) + " h"));
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
document.getElementById("day-close").onclick = () => {
  selected = null;
  for (const m of METRICS) { charts[m.key].data.datasets[0].backgroundColor = colors(m); charts[m.key].update("none"); }
  document.getElementById("day").hidden = true;
  document.getElementById("tip").hidden = false;
};

/* ---------- money, bills, cycle, weight ---------- */
function budgets(d) {
  const box = document.getElementById("budgets");
  if (!d.budgets.length) { box.replaceChildren(el("p", "empty", "Tell Moss “my food budget is RM200” ✨")); return; }
  box.replaceChildren(...d.budgets.map(b => {
    const pct = b.percent_used, [color, state] = pct >= 100 ? ["var(--critical)", "⚠️ over"] :
                                                 pct >= 80 ? ["var(--warning)", "👀 close"] : ["var(--good)", "✅ ok"];
    const row = el("div", "list-row");
    const name = el("div", "name");
    const line = el("div", "", (TYPE_EMOJI[b.spending_type] || "✨") + " " + b.spending_type[0].toUpperCase() + b.spending_type.slice(1));
    const small = el("span", "small", "   RM" + fmt(b.spent_rm) + " / " + fmt(b.monthly_rm));
    line.append(small);
    const bar = el("div", "meter"), fill = el("span");
    fill.style.background = color; bar.append(fill); name.append(line, bar);
    requestAnimationFrame(() => fill.style.width = Math.min(100, pct) + "%");
    row.append(name, el("span", "state", state + (b.left_rm >= 0 ? " · RM" + fmt(b.left_rm) + " left" : "")));
    return row;
  }));
}
function spentByType(d) {
  charts.types?.destroy();
  const box = document.getElementById("types-box");
  if (!d.month.by_type.length) { box.replaceChildren(el("p", "empty", "No spending yet this month 🌷")); return; }
  if (!box.querySelector("canvas")) box.replaceChildren(el("canvas"));
  box.style.height = (30 + d.month.by_type.length * 34) + "px";
  charts.types = new Chart(box.querySelector("canvas"), {
    type: "bar",
    data: { labels: d.month.by_type.map(t => (TYPE_EMOJI[t[0]] || "✨") + " " + t[0]),
            datasets: [{ data: d.month.by_type.map(t => t[1]), backgroundColor: "#7048e8", borderRadius: 4, maxBarThickness: 20 }] },
    options: { indexAxis: "y", maintainAspectRatio: false, animation: { duration: 500 },
               plugins: { legend: { display: false }, tooltip: { displayColors: false, backgroundColor: "#33262c",
                          callbacks: { label: i => "RM" + fmt(i.parsed.x, 2) } } },
               scales: { x: { beginAtZero: true, grid: { color: "#f1edef" }, border: { display: false },
                              ticks: { color: "#a8939c", maxTicksLimit: 4, callback: v => "RM" + fmt(v) } },
                         y: { grid: { display: false }, border: { display: false }, ticks: { color: "#33262c" } } } },
  });
}
function bills(d) {
  const box = document.getElementById("bills");
  if (!d.bills.length) { box.replaceChildren(el("p", "empty", "None yet ✨")); return; }
  box.replaceChildren(...d.bills.map(b => {
    const row = el("div", "list-row");
    row.append(el("span", "", b.kind === "subscription" ? "🔁" : "🧾"), el("span", "name", b.name),
               el("span", "small", "RM" + fmt(b.amount_rm, 2)));
    const when = b.paid ? "✅ paid" : b.days_until_due === 0 ? "today" : b.days_until_due === 1 ? "tomorrow" :
                 b.days_until_due < 0 ? "⚠️ overdue" : short(b.next_due) + " · " + b.days_until_due + "d";
    row.append(chip(when));
    return row;
  }));
}
function cycle(d) {
  const c = d.cycle, box = document.getElementById("cycle");
  if (!c.has_data) { box.replaceChildren(el("p", "empty", "Tap 🩸 Started in Telegram when it begins ✨")); return; }
  const n = c.days_until_next, next = n > 1 ? "in " + n + " days" : n === 1 ? "tomorrow" : n === 0 ? "today" : (-n) + (n === -1 ? " day" : " days") + " late";
  const row = el("div", "list-row");
  const left = el("div", "name");
  left.append(el("div", "cycle-big", "Day " + c.cycle_day), el("div", "small", c.phase));
  const right = el("div");
  right.style.textAlign = "right";
  right.append(el("div", "small", "next period"), el("div", "big", short(c.next_period)), el("div", "small", next));
  row.append(left, right);
  box.replaceChildren(row);
}
function weight(d) {
  const card = document.getElementById("weight-card");
  card.hidden = !d.weights.length;
  if (!d.weights.length) return;
  const w = d.weights, first = w[0].kg, last = w[w.length - 1].kg;
  document.getElementById("weight-chips").replaceChildren(chip("now " + fmt(last, 1) + " kg"),
    chip((last - first >= 0 ? "+" : "") + fmt(last - first, 1) + " kg since " + short(w[0].date)));
  charts.weight?.destroy();
  charts.weight = new Chart(document.getElementById("weight"), {
    type: "line",
    data: { labels: w.map(x => short(x.date)), datasets: [{ data: w.map(x => x.kg), borderColor: "#d6336c", borderWidth: 2,
            pointRadius: 4, pointBackgroundColor: "#d6336c", tension: 0.3 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false },
               tooltip: { displayColors: false, backgroundColor: "#33262c", callbacks: { label: i => "⚖️ " + fmt(i.parsed.y, 1) + " kg" } } },
               scales: { x: { grid: { display: false }, ticks: { color: "#a8939c", maxTicksLimit: 4 } },
                         y: { grace: "10%", grid: { color: "#f1edef" }, border: { display: false }, ticks: { color: "#a8939c", maxTicksLimit: 4 } } } },
  });
}

/* ---------- report card ---------- */
const pct = share => Math.round(share * 100) + "%";
function reportCard(d) {
  const c = d.report_card.latest, body = document.getElementById("rc-body");
  charts.rcTrend?.destroy(); charts.rcSweep?.destroy();
  if (!c) { body.replaceChildren(el("p", "empty", "No report card yet: run report_card.py on your PC ✨")); return; }
  set("rc-when", c.run_at + " · " + c.messages + " messages");
  const top = el("div", "rc-top"), overall = el("div", "rc-overall"), scores = el("div", "rc-scores");
  overall.append(el("div", "rc-big", pct(c.overall)), el("div", "small", "overall"));
  const rows = [["🧭", "Routing (Jev)", c.routing.score, pct(c.routing.safe) + " safe · " + c.routing.avg_tools + " of " + c.routing.all_tools + " tools"],
                ["🛠️", "Tool choice (Gemini)", c.tools.score, c.tools.ok + " of " + c.messages + " right" + (c.tools.nudged ? " · safety net used " + c.tools.nudged + "x" : "")],
                ["📚", "Second brain search", c.brain.score, "first result right " + pct(c.brain.first_right)]];
  scores.replaceChildren(...rows.map(([emoji, name, score, note]) => {
    const row = el("div", "list-row"), left = el("div", "name"), line = el("div", "", emoji + " " + name);
    const bar = el("div", "meter"), fill = el("span");
    fill.style.background = "var(--pink)"; bar.append(fill);
    left.append(line, bar, el("div", "small", note));
    requestAnimationFrame(() => fill.style.width = score * 100 + "%");
    row.append(left, el("span", "big", pct(score)));
    return row;
  }));
  top.append(overall, scores);
  const speed = el("div", "chips");
  speed.append(chip("⚡ Jev " + c.speed.jev_ms + " ms"), chip("✨ Gemini " + fmt(c.speed.gemini_ms / 1000, 1) + " s"),
               chip("🤖 " + c.models.chat), chip("🧠 " + c.models.embeddings));
  const two = el("div", "grid");
  const sweepBox = el("div"), sweepArea = el("div", "chart"), sweepCanvas = el("canvas");
  sweepCanvas.setAttribute("aria-label", "Second brain score at each cut-off");
  sweepArea.append(sweepCanvas);
  const b = c.brain, better = b.best_score > b.score;
  sweepBox.append(el("h3", "", "📚 Which cut-off (MIN_MATCH) works best"), sweepArea,
                  el("p", "small", "Now " + b.cut_off + ": " + pct(b.score) + " right" +
                     (better ? " · best " + b.best_cut_off + ": " + pct(b.best_score) : " · already the best ✨")));
  two.append(sweepBox);
  const history = d.report_card.history;
  let trendCanvas = null;
  if (history.length > 1) {
    const trendBox = el("div"), trendArea = el("div", "chart");
    trendCanvas = el("canvas");
    trendCanvas.setAttribute("aria-label", "Overall score, run by run");
    trendArea.append(trendCanvas);
    trendBox.append(el("h3", "", "📈 Overall, run by run"), trendArea);
    two.append(trendBox);
  }
  const misses = el("details");
  misses.append(el("summary", "", "🔍 What it got wrong (" + c.misses.length + ")"), ...c.misses.map(m => {
    const row = el("div", "miss");
    const did = { routing: "🧭 Jev gave", tools: "🛠️ Gemini did", brain: "📚 Search found" }[m.part];
    row.append(el("div", "what", "“" + m.say + "”"),
               el("div", "small", did + ": " + m.got.slice(0, 4).join(", ") + (m.got.length > 4 ? "…" : "") +
                  " · wanted " + m.want.join(" or ")));
    if (m.reply) row.append(el("div", "small", "💬 " + m.reply.slice(0, 140) + "…"));
    return row;
  }));
  body.replaceChildren(top, speed, two, misses);
  const scales = {
    x: { grid: { display: false }, border: { display: false }, ticks: { color: "#a8939c", maxRotation: 0, maxTicksLimit: 5 } },
    y: { min: 0, max: 100, grid: { color: "#f1edef" }, border: { display: false },
         ticks: { color: "#a8939c", maxTicksLimit: 5, callback: v => v + "%" } } };
  const tip = { backgroundColor: "#33262c", padding: 10, displayColors: false };
  const cutOffs = b.sweep.map(p => p[0]);
  charts.rcSweep = new Chart(sweepCanvas, {
    type: "line",
    data: { labels: cutOffs.map(t => t.toFixed(2)),
            datasets: [{ data: b.sweep.map(p => p[1] * 100), borderColor: "#d6336c", borderWidth: 2, tension: 0.2,
                         pointRadius: ctx => cutOffs[ctx.dataIndex] === b.cut_off ? 6 : 0, pointHoverRadius: 6,
                         pointBackgroundColor: "#d6336c", pointBorderColor: "#fff", pointBorderWidth: 2 }] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
               plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: {
                 title: items => "cut-off " + items[0].label + (cutOffs[items[0].dataIndex] === b.cut_off ? " (now)" : ""),
                 label: item => "📚 " + Math.round(item.parsed.y) + "% right" } } },
               scales },
  });
  if (trendCanvas) charts.rcTrend = new Chart(trendCanvas, {
    type: "line",
    data: { labels: history.map(h => short(h.run_at.slice(0, 10))),
            datasets: [{ data: history.map(h => h.overall * 100), borderColor: "#d6336c", borderWidth: 2, tension: 0.2,
                         pointRadius: 4, pointBackgroundColor: "#d6336c" }] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
               plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: {
                 title: items => history[items[0].dataIndex].run_at,
                 label: item => "📊 " + Math.round(item.parsed.y) + "% overall" } } },
               scales },
  });
}

/* ---------- loading ---------- */
async function load(days) {
  const response = await fetch("/dashboard/data?days=" + days, { credentials: "same-origin" });
  if (response.status === 401) { location.reload(); return; }
  D = await response.json();
  selected = null;
  document.getElementById("day").hidden = true;
  document.getElementById("tip").hidden = false;
  today(D); buildCharts(D); budgets(D); spentByType(D); bills(D); cycle(D); weight(D); reportCard(D);
}
for (const button of document.querySelectorAll(".range button")) {
  button.onclick = () => {
    document.querySelectorAll(".range button").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
    load(Number(button.dataset.days));
  };
}
load(30);
</script>
</body>
</html>
"""
