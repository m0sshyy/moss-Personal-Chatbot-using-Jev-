"""
Saves your recent conversation with Gemini in Firestore, so the bot still
remembers it after a restart (Cloud Run puts the bot to sleep when it's idle,
and anything only kept in memory is lost).

Each Telegram chat has one Firestore document in the "chats" collection,
holding the recent messages (the same ~15 exchanges Gemini is shown).
"""

from google.genai import types

import tracker


def _strip_images(content: types.Content) -> dict:
    """Photos are big (and Firestore documents max out at 1 MB), so a stored
    message keeps a short "[photo]" note instead of the image itself."""
    data = content.model_dump(mode="json", exclude_none=True)
    for part in data.get("parts", []):
        if "inline_data" in part:
            part.pop("inline_data")
            part["text"] = "[photo]"
    return data


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
