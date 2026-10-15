"""Tests for report_card.py: marking the exam, the brain cut-off sweep, and keeping cards."""

import brain
import dashboard
import report_card
from report_card_cases import BRAIN_DOCS, BRAIN_QUESTIONS, CASES
from test_brain import word_vectors

LOG = {"say": "had nasi lemak", "want": ["log_entry"]}
CHAT = {"say": "thank you!", "want": []}
TWO = {"say": "roti canai, remind me at 8pm", "want": ["log_entry", "add_task"], "need_all": True}


def test_routing_marks():
    assert report_card.grade_routing(LOG, {"log_entry", "find_entries"}) == "ok"
    assert report_card.grade_routing(LOG, {"add_task"}) == "miss"
    assert report_card.grade_routing(LOG, None) == "wasteful"         # unsure: every tool
    assert report_card.grade_routing(CHAT, set()) == "ok"
    assert report_card.grade_routing(CHAT, {"log_entry"}) == "wasteful"
    assert report_card.grade_routing(TWO, {"log_entry"}) == "miss"     # both jobs need their tools
    assert report_card.grade_routing(TWO, {"log_entry", "add_task"}) == "ok"


def test_tool_choice_marks():
    assert report_card.grade_tools(LOG, ["log_entry"]) == "ok"
    assert report_card.grade_tools(LOG, []) == "miss"                  # said it, didn't do it
    assert report_card.grade_tools(CHAT, []) == "ok"
    assert report_card.grade_tools(CHAT, ["log_entry"]) == "miss"
    assert report_card.grade_tools(TWO, ["log_entry", "add_task"]) == "ok"
    assert report_card.grade_tools(TWO, ["log_entry"]) == "miss"


def test_brain_marks_and_cut_off_sweep():
    ranked = [[("SHAP notes", 0.8), ("RAG", 0.5)], [("Recipe", 0.62)]]
    questions = [{"expect": "SHAP notes"}, {"expect": None}]
    assert report_card.brain_right(ranked[0], "SHAP notes", 0.6)
    assert not report_card.brain_right(ranked[0], "SHAP notes", 0.85)  # cut-off too strict
    assert not report_card.brain_right(ranked[1], None, 0.6)           # found something it shouldn't
    points = dict((t, score) for t, score in report_card.sweep(ranked, questions))
    assert points[0.6] == 0.5 and points[0.7] == 1.0 and points[0.8] == 1.0
    assert report_card.best_cut_off(report_card.sweep(ranked, questions)) == 0.725  # middle of the tie (0.625-0.8)


def test_every_exam_tool_exists():
    import google_calendar
    import MosshyBot
    # The tests run without Google Calendar connected, so add its tools by hand.
    names = {tool.__name__ for tool in MosshyBot.chat_config.tools + google_calendar.TOOLS}
    for case in CASES:
        assert set(case["want"]) <= names, case
    assert all(q["expect"] in BRAIN_DOCS for q in BRAIN_QUESTIONS if q["expect"])


def test_brain_exam_runs_on_pretend_embeddings(monkeypatch):
    monkeypatch.setattr(brain, "embed", word_vectors)
    part = report_card.sit_brain_exam()
    assert part["questions"] == len(BRAIN_QUESTIONS) and len(part["sweep"]) == len(report_card.THRESHOLDS)
    assert 0 <= part["score"] <= 1 and part["best_cut_off"] in report_card.THRESHOLDS


def result(case, routing, tools, given=None, called=()):
    return {"say": case["say"], "after": "", "want": case["want"], "given": given, "called": list(called),
            "routing": routing, "tools": tools, "error": "", "reply": "" if called else "Okay!",
            "jev_ms": 200, "gemini_ms": 1000, "tokens": 3000}


def test_report_card_is_kept_and_shown(clock):
    results = [result(LOG, "ok", "ok", ["log_entry"], ["log_entry"]),
               result(CHAT, "ok", "ok", []),
               result(TWO, "miss", "miss", ["log_entry"], ["log_entry"]),
               result(LOG, "wasteful", "miss", None)]
    brain_part = {"score": 0.9, "cut_off": 0.6, "best_cut_off": 0.65, "best_score": 0.95, "sweep": [[0.6, 0.9]],
                  "first_right": 1.0, "questions": 20, "misses": [{"part": "brain", "say": "x", "want": ["y"], "got": []}]}
    card = report_card.report(results, brain_part, 36)
    assert card["routing"]["score"] == 0.5 and card["routing"]["safe"] == 0.75
    assert card["routing"]["avg_tools"] == (1 + 0 + 1 + 36) / 4
    assert card["tools"]["score"] == 0.5
    assert card["overall"] == round((0.5 + 0.5 + 0.9) / 3, 3)
    assert [m["part"] for m in card["misses"]] == ["routing", "tools", "tools", "brain"]
    assert card["misses"][2]["reply"] == "Okay!"                        # what Moss said instead
    card["models"] = {"chat": "gemini-flash-lite-latest", "embeddings": "gemini-embedding-2", "router": "Jev"}
    report_card.save(card)
    assert report_card.history()[-1]["run_at"] == "2026-10-13 09:00"
    shown = report_card.text(card)
    assert "Overall: 63%" in shown and "MIN_MATCH 0.65" in shown
    summary = dashboard.report_card_summary()
    assert summary["latest"]["overall"] == card["overall"] and summary["history"][0]["run_at"] == "2026-10-13 09:00"


def test_dashboard_without_report_cards(clock):
    assert dashboard.report_card_summary() == {"latest": None, "history": []}
