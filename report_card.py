"""
Moss's report card: how well Moss understands messages, measured on a fixed exam
(report_card_cases.py), so a change can be judged by numbers instead of a feeling.

Three scores:
1. Routing (Jev): did Gemini get the tools the message needs?
     ok        the right tools (or none at all for small talk)
     wasteful  Jev was unsure, so Gemini got every tool (or tools it didn't need):
               still works, just costs more
     miss      a needed tool was left out, so Moss couldn't have done the job
2. Tool choice (Gemini): with the tools Jev gave it, was Gemini's first move right?
   Gemini's tool calls are only looked at, never run: sitting the exam saves nothing.
3. Second brain: does search find the right saved thing, and find nothing when
   nothing saved answers the question? Every cut-off (brain.MIN_MATCH) from 0.40 to
   0.80 is tried, so the best one is picked from evidence, not by eye.

Run it (about 2 minutes, a few sen of Gemini and Jev usage):
    .venv\\Scripts\\python report_card.py
    .venv\\Scripts\\python report_card.py --no-save     (just print, don't store it)
    .venv\\Scripts\\python report_card.py --site        (no exam: update the project page's numbers)
Each run is kept in Firestore "report_cards" and shown with /reportcard (and, after --site,
on the project page).
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

from google.genai import types

import brain
import jev_router
import tracker
from report_card_cases import BRAIN_DOCS, BRAIN_QUESTIONS, CASES

PARALLEL = 4  # messages sat at the same time
THRESHOLDS = [round(0.40 + 0.025 * i, 3) for i in range(17)]  # 0.40, 0.425 ... 0.80


# --- Marking (no internet needed, so the tests can check it) --------------------------
def grade_routing(case: dict, given: set[str] | None) -> str:
    """given: the tools Jev handed Gemini (None = every tool)."""
    want = set(case["want"])
    if given is None or (not want and given):
        return "wasteful"
    if not want:
        return "ok"
    covered = want <= given if case.get("need_all") else bool(want & given)
    return "ok" if covered else "miss"


def grade_tools(case: dict, called: list[str]) -> str:
    """called: the tools Gemini asked for first."""
    want, called = set(case["want"]), set(called)
    if not want:
        return "ok" if not called else "miss"
    right = want <= called if case.get("need_all") else bool(want & called)
    return "ok" if right else "miss"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b) or 1)


def rank(question: list[float], pieces: list[tuple[str, list[float]]]) -> list[tuple[str, float]]:
    """Each saved thing's best piece match for one question, best first."""
    best: dict[str, float] = {}
    for title, vector in pieces:
        best[title] = max(best.get(title, -1.0), cosine(question, vector))
    return sorted(best.items(), key=lambda kv: -kv[1])[:brain.TOP]


def brain_right(ranked: list[tuple[str, float]], expect: str | None, cut_off: float) -> bool:
    """Right = the expected thing comes back above the cut-off (Gemini sees all that
    do), or nothing does when nothing saved answers it."""
    found = [title for title, match in ranked if match >= cut_off]
    return not found if expect is None else expect in found


def sweep(ranked: list, questions: list[dict]) -> list[list[float]]:
    """[[cut_off, share right], ...] for every cut-off tried."""
    return [[t, round(sum(brain_right(r, q["expect"], t) for r, q in zip(ranked, questions)) / len(questions), 3)]
            for t in THRESHOLDS]


def best_cut_off(points: list[list[float]]) -> float:
    """The cut-off with the best score; if several tie, the middle one (safest either way)."""
    top = max(score for _, score in points)
    tied = [t for t, score in points if score == top]
    return tied[len(tied) // 2]


# --- Sitting the exam -------------------------------------------------------------------
async def sit(case: dict, gate: asyncio.Semaphore) -> dict:
    """One message: Jev picks tools, then Gemini makes its first move (not carried out)."""
    import MosshyBot as bot  # imported here: it needs the real settings (.env)
    async with gate:
        start = time.perf_counter()
        given = await jev_router.pick_tools(case["say"], case.get("after", ""))
        jev_ms = (time.perf_counter() - start) * 1000
        config = bot.config_for(given).model_copy(update={
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)})
        contents = []
        if case.get("after"):  # the conversation so far: my previous message
            contents += [types.Content(role="user", parts=[types.Part(text="(earlier message)")]),
                         types.Content(role="model", parts=[types.Part(text=case["after"])])]
        contents.append(types.Content(role="user", parts=[types.Part(text=bot.with_timestamp(case["say"]))]))
        called, tokens, error, reply, nudged = [], 0, "", "", False
        every_tool = bot.chat_config.model_copy(update={
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)})
        start = time.perf_counter()
        for attempt in range(2):  # one retry, for a busy moment at Google
            try:
                response = await bot.gemini.aio.models.generate_content(
                    model=bot.FAST_MODEL, contents=contents, config=config)
                called = [call.name for call in response.function_calls or []]
                reply = "" if called else (response.text or "")[:200]  # what Moss said instead
                if bot.claims_without_doing(reply, bool(called)):  # the safety net, as in the bot
                    nudged = True
                    again = contents + [response.candidates[0].content,
                                        types.Content(role="user", parts=[types.Part(text=bot.DO_IT_NOW)])]
                    response = await bot.gemini.aio.models.generate_content(
                        model=bot.FAST_MODEL, contents=again, config=every_tool)
                    called = [call.name for call in response.function_calls or []]
                    reply = "" if called else (response.text or "")[:200]
                tokens = response.usage_metadata.prompt_token_count or 0
                error = ""
                break
            except Exception as problem:
                error = str(problem)[:200]
                await asyncio.sleep(3)
        gemini_ms = (time.perf_counter() - start) * 1000
    return {"say": case["say"], "after": case.get("after", ""), "want": case["want"],
            "given": None if given is None else sorted(given), "called": called,
            "routing": grade_routing(case, given), "tools": "miss" if error else grade_tools(case, called),
            "error": error, "reply": reply, "nudged": nudged, "jev_ms": round(jev_ms), "gemini_ms": round(gemini_ms), "tokens": tokens}


def sit_brain_exam() -> dict:
    """Embed the pretend saved things and the questions (nothing is stored), then mark."""
    titles, texts = [], []
    for title, text in BRAIN_DOCS.items():
        for piece in brain.chunks(text):
            titles.append(title)
            texts.append(f"{title}\n{piece}")
    pieces = list(zip(titles, brain.embed(texts, "RETRIEVAL_DOCUMENT")))
    questions = brain.embed([q["ask"] for q in BRAIN_QUESTIONS], "RETRIEVAL_QUERY")
    ranked = [rank(vector, pieces) for vector in questions]
    points = sweep(ranked, BRAIN_QUESTIONS)
    answerable = [(r, q) for r, q in zip(ranked, BRAIN_QUESTIONS) if q["expect"]]
    misses = [{"part": "brain", "say": q["ask"], "want": [q["expect"] or "nothing"],
               "got": [f"{title} {match:.2f}" for title, match in r[:2]]}
              for r, q in zip(ranked, BRAIN_QUESTIONS) if not brain_right(r, q["expect"], brain.MIN_MATCH)]
    score = sum(brain_right(r, q["expect"], brain.MIN_MATCH) for r, q in zip(ranked, BRAIN_QUESTIONS))
    return {"score": round(score / len(BRAIN_QUESTIONS), 3),
            "cut_off": brain.MIN_MATCH, "best_cut_off": best_cut_off(points),
            "best_score": max(score for _, score in points), "sweep": points,
            "first_right": round(sum(r[0][0] == q["expect"] for r, q in answerable) / len(answerable), 3),
            "questions": len(BRAIN_QUESTIONS), "misses": misses}


def report(results: list[dict], brain_part: dict, all_tools: int) -> dict:
    """The marked exam -> one report card."""
    total = len(results)
    count = lambda part, grade: sum(r[part] == grade for r in results)
    routing = {"score": round(count("routing", "ok") / total, 3),
               "safe": round(1 - count("routing", "miss") / total, 3),
               "ok": count("routing", "ok"), "wasteful": count("routing", "wasteful"),
               "miss": count("routing", "miss"),
               "avg_tools": round(statistics.mean(all_tools if r["given"] is None else len(r["given"])
                                                  for r in results), 1),
               "all_tools": all_tools}
    tools = {"score": round(count("tools", "ok") / total, 3), "ok": count("tools", "ok"),
             "miss": count("tools", "miss"), "nudged": sum(bool(r.get("nudged")) for r in results)}
    misses = [{"part": part, "say": r["say"], "want": r["want"] or ["no tool"],
               "got": (r["given"] if part == "routing" else r["called"]) or ["no tool"],
               **({"error": r["error"]} if r["error"] and part == "tools" else {}),
               **({"reply": r["reply"]} if r["reply"] and part == "tools" else {})}
              for r in results for part in ("routing", "tools") if r[part] == "miss"]
    return {
        "run_at": tracker.now().strftime("%Y-%m-%d %H:%M"), "messages": total,
        "overall": round((routing["score"] + tools["score"] + brain_part["score"]) / 3, 3),
        "routing": routing, "tools": tools, "brain": {k: v for k, v in brain_part.items() if k != "misses"},
        "speed": {"jev_ms": round(statistics.median(r["jev_ms"] for r in results)),
                  "gemini_ms": round(statistics.median(r["gemini_ms"] for r in results)),
                  "prompt_tokens": round(statistics.mean(r["tokens"] for r in results if r["tokens"]) if
                                         any(r["tokens"] for r in results) else 0)},
        "misses": (misses + brain_part["misses"])[:60],
        # Every message, short: which kinds of tools Jev gave and what Gemini did.
        "results": [{"say": r["say"], "groups": groups(r["given"]), "called": r["called"],
                     "routing": r["routing"], "tools": r["tools"], "nudged": bool(r.get("nudged"))}
                    for r in results],
    }


def groups(given: list[str] | None) -> list[str]:
    """Tool names -> the kinds of job Jev said yes to (["all"] when it was unsure)."""
    if given is None:
        return ["all"]
    return [name for name, tools in jev_router.GROUPS.items() if tools <= set(given)]


async def run() -> dict:
    import MosshyBot as bot
    gate = asyncio.Semaphore(PARALLEL)
    results = await asyncio.gather(*(sit(case, gate) for case in CASES))
    brain_part = await asyncio.to_thread(sit_brain_exam)
    card = report(results, brain_part, len(bot.chat_config.tools))
    card["models"] = {"chat": bot.FAST_MODEL, "embeddings": brain.EMBED_MODEL,
                      "router": "Jev" if jev_router.enabled() else "off"}
    return card


# --- Keeping and showing report cards ------------------------------------------------------
def _cards():
    return tracker._collection("report_cards")


def save(card: dict) -> None:
    _cards().document(card["run_at"].replace(" ", "_")).set(card)


def history(how_many: int = 12) -> list[dict]:
    """The latest report cards, oldest first."""
    return [doc.to_dict() for doc in _cards().order_by("run_at").stream()][-how_many:]


# --- The public project page (site/report-card.html) ------------------------------------
SITE_DATA = Path(__file__).with_name("site") / "report-card.json"
# The exam messages shown on the page (all made up, nothing private).
SHOWCASE = ["had nasi lemak ayam for lunch", "grab to office rm18", "remind me to call mama at 3pm",
            "aina's birthday is on 3 november", "what's my gym locker code?",
            "roti canai and teh o ais, and remind me to pay the bill at 8pm",
            "spotify rm16.90 every 15th", "thank you moss!"]


def site_data(cards: list[dict]) -> dict:
    """The numbers the project page draws: first run vs latest, the trend, examples."""
    first, latest = cards[0], cards[-1]
    scores = lambda card: {"overall": card["overall"], "routing": card["routing"]["score"],
                           "tools": card["tools"]["score"], "brain": card["brain"]["score"]}
    examples = {r["say"]: r for r in latest.get("results", [])}
    return {"run_at": latest["run_at"], "messages": latest["messages"],
            "brain_questions": latest["brain"]["questions"], "before": scores(first), "after": scores(latest),
            "history": [{"run_at": card["run_at"], "overall": card["overall"]} for card in cards],
            "avg_tools": latest["routing"]["avg_tools"], "all_tools": latest["routing"]["all_tools"],
            "safe": latest["routing"]["safe"], "nudged": latest["tools"].get("nudged", 0),
            "speed": latest["speed"], "models": latest["models"], "sweep": latest["brain"]["sweep"],
            "cut_off": latest["brain"]["cut_off"],
            "examples": [{**examples[say], "tools_given": tools_given(examples[say]["groups"], latest)}
                         for say in SHOWCASE if say in examples]}


def tools_given(kinds: list[str], card: dict) -> int:
    """How many tools Gemini got for those kinds of job."""
    if kinds == ["all"]:
        return card["routing"]["all_tools"]
    return len(set().union(*(jev_router.GROUPS[kind] for kind in kinds))) if kinds else 0


def pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def text(card: dict) -> str:
    """A report card as a short Telegram message."""
    r, t, b = card["routing"], card["tools"], card["brain"]
    tip = ""
    if b["best_cut_off"] != round(b["cut_off"], 3) and b["best_score"] > b["score"]:
        tip = f"\n💡 The second brain would score {pct(b['best_score'])} with MIN_MATCH {b['best_cut_off']:g}"
    return (f"📊 Moss report card · {card['run_at']}\n"
            f"Overall: {pct(card['overall'])} · {card['messages']} messages\n\n"
            f"🧭 Routing (Jev): {pct(r['score'])} right · {pct(r['safe'])} safe\n"
            f"    {r['avg_tools']:g} of {r['all_tools']} tools per message on average\n"
            f"🛠️ Tool choice (Gemini): {pct(t['score'])}"
            + (f" · safety net used {t['nudged']}x" if t.get("nudged") else "") + "\n"
            f"📚 Second brain search: {pct(b['score'])} · first result right {pct(b['first_right'])}\n"
            f"⚡ Jev {card['speed']['jev_ms']} ms · Gemini {card['speed']['gemini_ms']} ms"
            f"{tip}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sit Moss's exam and print the report card.")
    parser.add_argument("--no-save", action="store_true", help="print only, don't store the result")
    parser.add_argument("--site", action="store_true",
                        help="no exam: write the saved results for the project page (then run publish_site.py)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # emojis on the Windows terminal
    if args.site:
        SITE_DATA.write_text(json.dumps(site_data(history(50)), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Wrote {SITE_DATA}. Publish it with: .venv\\Scripts\\python publish_site.py")
        return
    card = asyncio.run(run())
    print(text(card))
    if card["misses"]:
        print("\nWhat it got wrong:")
        for miss in card["misses"]:
            print(f"  [{miss['part']}] {miss['say']!r}  wanted {miss['want']}  got {miss['got']}"
                  + (f"  ({miss['error']})" if miss.get("error") else "")
                  + (f"\n      Moss said: {miss['reply']!r}" if miss.get("reply") else ""))
    if not args.no_save:
        save(card)
        print("\nSaved: see it with /reportcard ♡ (update the project page with --site)")


if __name__ == "__main__":
    main()
