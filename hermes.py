"""
Hermes 4 (Nous Research's open-weight models) as a stand-in for Gemini, so the
report card can compare them on the same exam (report_card.py --vs hermes).

Hermes is reached through an OpenAI-style "chat completions" API (Nous Portal by
default). Moss's tools are the same Python functions Gemini gets, described as
JSON Schema, so both models see exactly the same tools and instructions.

Hermes was trained to call tools by writing <tool_call>{"name": ..., "arguments": ...}</tool_call>
in its reply. Most servers turn that into proper "tool_calls"; if one doesn't,
the tags are read here, so either way counts.

Settings (.env):
    HERMES_API_KEY   your Nous Portal key (never paste it in chat)
    HERMES_BASE_URL  optional, default https://inference-api.nousresearch.com/v1
    HERMES_MODEL     optional, default Hermes-4-70B
"""

import contextlib
import io
import json
import os
import re
import time

import httpx
from google.genai import types

DEFAULT_URL = "https://inference-api.nousresearch.com/v1"
DEFAULT_MODEL = "Hermes-4-70B"
TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_schemas: dict[str, dict] = {}


def enabled() -> bool:
    return bool(os.environ.get("HERMES_API_KEY", "").strip())


def model() -> str:
    return os.environ.get("HERMES_MODEL", "").strip() or DEFAULT_MODEL


def _lower_types(schema):
    """Gemini writes types as "STRING"/"NUMBER"; JSON Schema wants "string"/"number"."""
    if isinstance(schema, dict):
        return {key: (value.lower() if key == "type" and isinstance(value, str) else _lower_types(value))
                for key, value in schema.items()}
    if isinstance(schema, list):
        return [_lower_types(item) for item in schema]
    return schema


def tool_schema(function) -> dict:
    """One of Moss's tool functions -> an OpenAI-style tool description (cached)."""
    name = function.__name__
    if name not in _schemas:
        import MosshyBot as bot
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            declaration = types.FunctionDeclaration.from_callable(client=bot.gemini._api_client, callable=function)
        described = declaration.model_dump(mode="json", exclude_none=True)
        _schemas[name] = {"type": "function", "function": {
            "name": name, "description": described.get("description", ""),
            "parameters": _lower_types(described.get("parameters", {"type": "object", "properties": {}}))}}
    return _schemas[name]


def calls_in(message: dict) -> list[str]:
    """The tool names in a reply: proper tool_calls, or <tool_call> tags in the text."""
    names = [call["function"]["name"] for call in message.get("tool_calls") or []]
    if not names:
        for raw in TOOL_CALL_TAG.findall(message.get("content") or ""):
            try:
                names.append(json.loads(raw)["name"])
            except (ValueError, KeyError):
                pass
    return names


async def first_move(system: str, messages: list[dict], functions: list | None) -> dict:
    """Ask Hermes for its first move (tool calls are only read, never run).
    messages: [{"role": "user"/"assistant", "content": ...}]. functions: Moss's tool
    functions to offer, or None/[] for none. Returns called, reply, the raw message,
    tokens and milliseconds."""
    body = {"model": model(), "messages": [{"role": "system", "content": system}, *messages]}
    if functions:
        body["tools"] = [tool_schema(function) for function in functions]
        body["tool_choice"] = "auto"
    url = (os.environ.get("HERMES_BASE_URL", "").strip() or DEFAULT_URL).rstrip("/") + "/chat/completions"
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json=body, headers={
            "Authorization": f"Bearer {os.environ['HERMES_API_KEY'].strip()}"})
    response.raise_for_status()
    data = response.json()
    message = data["choices"][0]["message"]
    called = calls_in(message)
    usage = data.get("usage") or {}
    return {"called": called, "reply": "" if called else (message.get("content") or "")[:200],
            "message": message, "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0), "ms": round((time.perf_counter() - start) * 1000)}
