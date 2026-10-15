"""Tests for jev_router.py: which tools Gemini gets, and the safety net."""

import asyncio
from types import SimpleNamespace

import pytest

import jev_router
import MosshyBot


class FakeJev:
    """Pretends to be the TypeSafe client, answering with fixed probabilities."""
    def __init__(self, scores=None, error=None):
        self.scores, self.error, self.states = scores or {}, error, []

    async def system_one(self, state, questions):
        self.states.append(state)
        if self.error:
            raise self.error
        return SimpleNamespace(answers={name: SimpleNamespace(noul=self.scores.get(name, 0.0))
                                        for name in questions})


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def use(**kwargs):
        fake = FakeJev(**kwargs)
        monkeypatch.setattr(jev_router, "_get_client", lambda: fake)
        return fake
    return use


def pick(text, previous=""):
    return asyncio.run(jev_router.pick_tools(text, previous))


def test_one_kind_of_message_gets_only_its_tools(jev):
    jev(scores={"log": 0.92, "history": 0.28, "small_talk": 0.37})
    assert pick("had nasi lemak") == jev_router.GROUPS["log"]


def test_mixed_messages_get_both_sets_of_tools(jev):
    jev(scores={"log": 0.78, "tasks": 0.97})
    assert pick("roti canai, and remind me to pay the bill") == jev_router.GROUPS["log"] | jev_router.GROUPS["tasks"]


def test_small_talk_gets_no_tools(jev):
    jev(scores={"small_talk": 0.95})
    assert pick("thank you mosshy!") == set()


def test_unsure_means_every_tool(jev):
    jev(scores={"small_talk": 0.5, "log": 0.2})
    assert pick("hmm") is None


def test_jev_errors_fall_back_to_every_tool(jev):
    jev(error=TimeoutError("too slow"))
    assert pick("had nasi lemak") is None


def test_no_key_means_jev_is_off(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert pick("had nasi lemak") is None


def test_my_previous_reply_is_given_for_follow_ups(jev):
    fake = jev(scores={"calendar": 0.9})
    pick("yes, all of them", previous="Delete just this date or every repeat?")
    assert fake.states[-1] == {"owner_message": "yes, all of them",
                               "my_previous_reply": "Delete just this date or every repeat?"}


def test_gemini_gets_exactly_the_picked_tools():
    picked = MosshyBot.config_for({"log_entry", "summarise"})
    assert sorted(tool.__name__ for tool in picked.tools) == ["log_entry", "summarise"]
    assert MosshyBot.config_for(set()).tools is None              # small talk: none
    assert MosshyBot.config_for(None) is MosshyBot.chat_config    # unsure: all


# --- Voice notes ----------------------------------------------------------------------
class FakeVoiceUpdate:
    def __init__(self, duration):
        async def get_file():
            async def download():
                return bytearray(b"ogg-bytes")
            return SimpleNamespace(download_as_bytearray=download)
        self.replies = []

        async def reply_text(text, **kwargs):
            self.replies.append(text)

        async def send_action(action):
            pass
        self.message = SimpleNamespace(
            voice=SimpleNamespace(duration=duration, mime_type="audio/ogg", get_file=get_file), audio=None,
            reply_text=reply_text, reply_to_message=None, chat=SimpleNamespace(send_action=send_action))
        self.effective_user = SimpleNamespace(id=42)
        self.effective_chat = SimpleNamespace(id=42)


def test_voice_note_is_handled_like_typed_text(monkeypatch):
    calls = []

    async def fake_transcribe(audio, mime_type):
        assert (audio, mime_type) == (b"ogg-bytes", "audio/ogg")
        return "had nasi lemak"

    async def fake_ask(update, context, message, route_text=None, tool_names=None, header=""):
        calls.append((route_text, header))
    monkeypatch.setattr(MosshyBot, "transcribe", fake_transcribe)
    monkeypatch.setattr(MosshyBot, "ask_gemini", fake_ask)
    asyncio.run(MosshyBot.on_voice(FakeVoiceUpdate(duration=4), None))
    assert calls == [("had nasi lemak", "🎙️ “had nasi lemak”\n\n")]  # routed by Jev, shown on top


def test_too_long_voice_notes_are_refused(monkeypatch):
    update = FakeVoiceUpdate(duration=MosshyBot.MAX_VOICE_SECONDS + 1)
    asyncio.run(MosshyBot.on_voice(update, None))
    assert "too long" in update.replies[0] or "bit long" in update.replies[0]


# --- Safety net: "I've added it" without calling a tool -----------------------------------
def test_claims_without_doing():
    assert MosshyBot.claims_without_doing("🎀 I've added Aina's birthday to your calendar!", False)
    assert MosshyBot.claims_without_doing("📅 Added: Aina's birthday, Tue 3 Nov", False)
    assert MosshyBot.claims_without_doing("I'll save 8,000 steps as your new goal!", False)
    assert MosshyBot.claims_without_doing("Let me update your weather place right away", False)
    assert MosshyBot.claims_without_doing("🎀 Adding that right away for you, Alya!", False)
    assert not MosshyBot.claims_without_doing("🎀 I've added it!", True)          # a tool did run
    assert not MosshyBot.claims_without_doing("You're welcome Alya! 🌸", False)
    assert not MosshyBot.claims_without_doing("Let me know if you need anything ♡", False)


class FakeChat:
    """A Gemini chat that first only *says* it saved, then (when told) calls the tool."""
    def __init__(self, replies):
        self.replies, self.history, self.sent = replies, [], []

    async def send_message(self, message, config=None):
        self.sent.append((message, config))
        text, called = self.replies.pop(0)
        self.history.append(SimpleNamespace(role="user", parts=[SimpleNamespace(function_call=None)]))
        if called:
            self.history.append(SimpleNamespace(role="model", parts=[SimpleNamespace(function_call=called)]))
        self.history.append(SimpleNamespace(role="model", parts=[SimpleNamespace(function_call=None)]))
        return SimpleNamespace(text=text)

    def get_history(self, curated=True):
        return list(self.history)


def use_fake_chat(monkeypatch, chat):
    monkeypatch.setattr(MosshyBot, "get_conversation", lambda chat_id: chat)
    monkeypatch.setattr(MosshyBot, "conversations", {})              # leave no fake chat behind
    monkeypatch.setattr(MosshyBot.chat_memory, "save", lambda chat_id, history: [])


def test_an_empty_promise_is_sent_back_to_do_it(monkeypatch):
    chat = FakeChat([("📅 Added: Aina's birthday", None), ("📅 Added: Aina's birthday ✨", "add_calendar_event")])
    use_fake_chat(monkeypatch, chat)
    response = asyncio.run(MosshyBot.send_to_gemini(42, "aina's birthday is 3 nov", set()))
    assert response.text.endswith("✨") and len(chat.sent) == 2
    assert chat.sent[1] == (MosshyBot.DO_IT_NOW, MosshyBot.chat_config)  # with every tool


def test_a_real_action_is_not_asked_twice(monkeypatch):
    chat = FakeChat([("📅 Added: Aina's birthday", "add_calendar_event")])
    use_fake_chat(monkeypatch, chat)
    asyncio.run(MosshyBot.send_to_gemini(42, "aina's birthday is 3 nov", None))
    assert len(chat.sent) == 1
