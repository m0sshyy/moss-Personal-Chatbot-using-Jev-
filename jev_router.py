"""
Jev as Moss's message router (Phase 6).

Jev is TypeSafe's "System One" model: instead of writing text, it makes fast,
typed decisions with a probability attached. Before Gemini sees one of your
messages, Jev answers a few yes/no questions about it in a single call (~0.3 s):

    Is this something to log?          Is it about tasks / reminders?
    Is it a question about past data?  Is it about the calendar?
    Is it about goals / quick buttons? Is it just small talk?

Gemini then only receives the tools for the jobs Jev said yes to, instead of
all ~20. In testing that made Gemini's prompts about 36% smaller (= cheaper).

Safety net: if Jev is unsure, errors, is slow, or there's no TYPESAFE_API_KEY,
Gemini simply gets every tool, exactly like before. Jev can only make things
cheaper, never break them.

Docs: https://docs.typesafe.ai  (questions = "primitives"; we use Noul, a
yes/no probability, one per label, since a message can need several at once).
"""

import logging
import os
import time

from typesafe_sdk import AsyncTypeSafeClient, Noul

log = logging.getLogger("jev")

INCLUDE_AT = 0.4    # a group's tools are given to Gemini from this probability up
SMALL_TALK_AT = 0.8  # "only small talk" this sure (and nothing else) -> no tools at all
TIMEOUT = 5          # seconds; after that, give up on Jev and use every tool

# Which of Gemini's tools each kind of message needs (names of functions in
# tracker.py, period.py and google_calendar.py).
GROUPS = {
    "log": {"log_entry", "update_entry", "delete_entry", "find_entries", "summarise", "get_goals",
            "cycle_status"},
    "history": {"find_entries", "summarise", "get_goals", "set_goal", "cycle_status", "get_budgets"},
    "work": {"work_notes", "set_draft_style", "log_entry", "find_entries", "list_tasks", "update_task"},
    "bills": {"add_bill", "list_bills", "mark_bill_paid", "update_bill", "delete_bill", "get_budgets"},
    "tasks": {"add_task", "list_tasks", "complete_task", "update_task", "delete_task"},
    "calendar": {"add_calendar_event", "list_calendar_events", "update_calendar_event",
                 "delete_calendar_event"},
    "settings": {"set_goal", "get_goals", "set_budget", "get_budgets", "add_quick_button",
                 "list_quick_buttons", "remove_quick_button", "set_weather_place",
                 "set_rain_alert_areas"},
    "brain": {"save_note", "save_link", "search_brain", "list_brain", "forget_brain_item"},
}

# The questions Jev answers, one per group plus "small talk". Each statement is
# a complete sentence: Jev only sees this text, never the names on the left.
# Each one judges `owner_message`; `my_previous_reply` (if any) is only context,
# so a "thanks" after a reminder isn't mistaken for a task request.
ABOUT = "Judge only `owner_message` (`my_previous_reply` is just context): "
QUESTIONS = {
    "log": Noul(instructions=ABOUT + (
        "the owner is telling me something they ate, drank, did, spent, felt, slept or weighed, "
        "something about their period, or is correcting or undoing something already logged, "
        "so it should be saved. Short notes count: an item with a price ('grab to office rm18', "
        "'nasi kerabu 12.50') is money spent.")),
    "history": Noul(instructions=ABOUT + (
        "the owner is asking about their own past data: totals, what they ate or spent, progress "
        "against goals, cycle predictions, or trends.")),
    "tasks": Noul(instructions=ABOUT + (
        "it is about the owner's to-do list or reminders: adding, ticking off, moving or "
        "listing tasks, or 'remind me to ...'. Saved notes are not tasks.")),
    "calendar": Noul(instructions=ABOUT + (
        "it is about calendar events: appointments, classes, meetings, birthdays, "
        "cancelling or moving an event, or what's on their schedule.")),
    "bills": Noul(instructions=ABOUT + (
        "it is about bills or subscriptions: adding one, paying one, a price change, cancelling, "
        "or what is due.")),
    "work": Noul(instructions=ABOUT + (
        "it is about the owner's job or studies: what they worked on, or asking me to write a log "
        "book entry, a report for their supervisor, or a work summary.")),
    "settings": Noul(instructions=ABOUT + (
        "the owner wants to set or change a goal (water, calories...), a monthly spending budget, "
        "their one-tap supplement buttons, or where they live or work (for the weather forecast and rain alerts). "
        "Just spending money is not a setting.")),
    "brain": Noul(instructions=ABOUT + (
        "the owner wants me to remember or save a note, link or document for later, or is asking "
        "about something they saved, read or studied (notes, articles, documents, codes), "
        "asks a question their saved notes could answer, or wants a saved note deleted ('forget the "
        "printer note').")),
    "small_talk": Noul(instructions=ABOUT + (
        "it is only a greeting, thanks, a reaction or general chat that needs nothing "
        "saved, changed or looked up.")),
}

_client = None


def enabled() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY", "").strip())


def _get_client() -> AsyncTypeSafeClient:
    global _client
    if _client is None:  # one client, reused for every message
        _client = AsyncTypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"].strip(), timeout=TIMEOUT)
    return _client


async def pick_tools(text: str, previous_reply: str = "") -> set[str] | None:
    """The names of the tools this message needs, an empty set for small talk,
    or None meaning "not sure: give Gemini every tool"."""
    if not enabled():
        return None
    # My last reply goes in too, so follow-ups like "yes, all of them" make sense.
    state = {"owner_message": text}
    if previous_reply:
        state["my_previous_reply"] = previous_reply[:800]
    start = time.perf_counter()
    try:
        response = await _get_client().system_one(state=state, questions=QUESTIONS)
    except Exception as error:  # Jev down, slow or out of credits: carry on without it
        log.warning("Jev failed (%s) - using every tool", error)
        return None
    scores = {name: answer.noul for name, answer in response.answers.items()}
    picked = [group for group in GROUPS if scores[group] >= INCLUDE_AT]
    if picked:
        tools = set().union(*(GROUPS[group] for group in picked))
    elif scores["small_talk"] >= SMALL_TALK_AT:
        tools = set()
    else:
        tools = None
    log.info("Jev %.0f ms: %s -> %s", (time.perf_counter() - start) * 1000,
             {name: round(score, 2) for name, score in scores.items()},
             sorted(picked) if picked else ("small talk" if tools == set() else "unsure: every tool"))
    return tools
