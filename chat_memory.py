"""
Saves your recent conversation with Gemini in Firestore, so the bot still
remembers it after a restart (Cloud Run puts the bot to sleep when it's idle,
and anything only kept in memory is lost).

Each Telegram chat has one Firestore document in the "chats" collection,
holding the recent messages (the same ~15 exchanges Gemini is shown).
"""

from google.genai import types

import tracker


FILE_START = "\nThe start of it:\n"  # how a sent file's text is put in a message (MosshyBot.on_document)


def _strip_images(content: types.Content) -> dict:
    """Photos are big (and Firestore documents max out at 1 MB), so a stored
    message keeps a short "[photo]" note instead of the image itself.
    Text from saved pages and files is left out too: it could hide instructions
    (guard.py), and it shouldn't keep steering later replies. Titles and ids stay,
    so "yes, forget that one" still works in the next message."""
    data = content.model_dump(mode="json", exclude_none=True)
    for part in data.get("parts", []):
        if "inline_data" in part:
            part.pop("inline_data")
            part["text"] = "[photo]"
        if FILE_START in part.get("text", ""):
            part["text"] = part["text"].split(FILE_START)[0] + "\n[the file's text was here]"
        reply = part.get("function_response")
        if reply and reply.get("name") in ("search_brain", "list_brain", "save_link"):
            reply["response"] = _without_saved_text(reply.get("response") or {})
    return data


def _without_saved_text(response: dict) -> dict:
    result = response.get("result", response)
    if not isinstance(result, dict):
        return {"note": "saved content was read here (not kept in memory)"}
    kept = {key: value for key, value in result.items() if key in ("saved", "already_saved", "item_id", "title", "total")}
    for key in ("results", "newest_first"):
        if key in result:
            kept[key] = [{k: item.get(k) for k in ("item_id", "title", "kind") if k in item} for item in result[key]]
    kept["note"] = "saved content was read here (its text isn't kept in memory; search again if needed)"
    return {"result": kept} if "result" in response else kept


def load(chat_id: int) -> list[types.Content]:
    doc = tracker._collection("chats").document(str(chat_id)).get()
    if not doc.exists:
        return []
    return [types.Content.model_validate(item) for item in doc.to_dict().get("history", [])]


def save(chat_id: int, history: list[types.Content]) -> list[types.Content]:
    """Saves the conversation and returns it without photos. The caller keeps
    using that photo-free copy, so a photo is sent to Gemini (and paid for)
    only once, not again with every later message."""
    stripped = [_strip_images(content) for content in history]
    tracker._collection("chats").document(str(chat_id)).set(
        {"history": stripped, "updated": tracker.now().isoformat()})
    return [types.Content.model_validate(item) for item in stripped]


def forget(chat_id: int) -> None:
    tracker._collection("chats").document(str(chat_id)).delete()
