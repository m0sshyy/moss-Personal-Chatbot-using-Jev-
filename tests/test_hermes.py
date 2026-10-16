"""Tests for hermes.py and report_card.py --vs hermes: the same exam for another model."""

import asyncio
import json

import httpx
import pytest

import hermes
import MosshyBot
import report_card
import tracker


def test_tools_are_described_as_json_schema():
    schema = hermes.tool_schema(tracker.log_entry)
    assert schema["type"] == "function" and schema["function"]["name"] == "log_entry"
    params = schema["function"]["parameters"]
    assert params["type"] == "object" and params["properties"]["amount"]["type"] == "number"
    assert set(params["required"]) == {"category", "item"}
    assert "nasi lemak" in schema["function"]["description"]


def test_tool_calls_are_read_either_way():
    assert hermes.calls_in({"tool_calls": [{"function": {"name": "log_entry", "arguments": "{}"}}]}) == ["log_entry"]
    tagged = 'Sure!\n<tool_call>\n{"name": "add_task", "arguments": {"title": "📞 Call mama"}}\n</tool_call>'
    assert hermes.calls_in({"content": tagged}) == ["add_task"]
    assert hermes.calls_in({"content": "<tool_call>not json</tool_call>"}) == []
    assert hermes.calls_in({"content": "You're welcome Alya! 🌸"}) == []


@pytest.fixture
def portal(monkeypatch):
    """A pretend Nous Portal: answers each request with the next canned message."""
    monkeypatch.setenv("HERMES_API_KEY", "test-key")
    requests, answers = [], []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": answers.pop(0)}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 20}})
    monkeypatch.setattr(hermes.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    return requests, answers


def test_first_move_offers_the_tools(portal):
    requests, answers = portal
    answers.append({"content": None, "tool_calls": [{"function": {"name": "log_entry", "arguments": "{}"}}]})
    move = asyncio.run(hermes.first_move("Be Moss.", [{"role": "user", "content": "nasi lemak"}], [tracker.log_entry]))
    assert move["called"] == ["log_entry"] and move["tokens_in"] == 900 and move["reply"] == ""
    sent = requests[0]
    assert sent["model"] == "Hermes-4-70B" and sent["messages"][0] == {"role": "system", "content": "Be Moss."}
    assert [tool["function"]["name"] for tool in sent["tools"]] == ["log_entry"]


def test_no_tools_means_none_offered(portal):
    requests, answers = portal
    answers.append({"content": "Hehe you're welcome 🌸"})
    move = asyncio.run(hermes.first_move("Be Moss.", [{"role": "user", "content": "thanks!"}], []))
    assert move["called"] == [] and move["reply"].startswith("Hehe") and "tools" not in requests[0]


def test_hermes_gets_the_safety_net_too(portal):
    requests, answers = portal
    answers += [{"content": "📅 Added: Aina's birthday!"},
                {"content": None, "tool_calls": [{"function": {"name": "add_calendar_event", "arguments": "{}"}}]}]
    case = {"say": "aina's birthday is on 3 november", "want": ["add_calendar_event"]}
    move = asyncio.run(report_card.hermes_move(case, {"log_entry"}))
    assert move["nudged"] and move["called"] == ["add_calendar_event"]
    assert requests[1]["messages"][-1]["content"] == MosshyBot.DO_IT_NOW
    assert len(requests[1]["tools"]) == len(MosshyBot.chat_config.tools)  # every tool the second time


def test_both_models_get_the_same_jev_pick(monkeypatch, clock):
    seen = {}

    async def jev(text, previous=""):
        return {"log_entry"}

    async def gemini(case, given):
        seen["gemini"] = given
        return {"called": ["log_entry"], "reply": "", "nudged": False, "tokens": 3000, "error": "", "ms": 1200}

    async def rival(case, given):
        seen["hermes"] = given
        return {"called": [], "reply": "Yummy!", "nudged": False, "tokens": 4000, "error": "", "ms": 900}
    monkeypatch.setattr(report_card.jev_router, "pick_tools", jev)
    monkeypatch.setattr(report_card, "gemini_move", gemini)
    monkeypatch.setitem(report_card.RIVALS, "hermes", rival)
    case = {"say": "had nasi lemak", "want": ["log_entry"]}
    result = asyncio.run(report_card.sit(case, asyncio.Semaphore(1), ("hermes",)))
    assert seen == {"gemini": {"log_entry"}, "hermes": {"log_entry"}}
    assert result["tools"] == "ok" and result["rivals"]["hermes"]["tools"] == "miss"
    table = report_card.compare([result], "gemini-flash-lite-latest", ("hermes",))
    assert table["gemini-flash-lite-latest"]["score"] == 1.0 and table["Hermes-4-70B"]["score"] == 0.0
    assert table["Hermes-4-70B"]["misses"][0]["reply"] == "Yummy!"
    shown = report_card.comparison_text(table)
    assert "Hermes-4-70B" in shown and "100%" in shown
