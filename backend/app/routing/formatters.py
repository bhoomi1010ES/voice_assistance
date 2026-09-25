"""Deterministic bounded formatting for authoritative structured reads."""

from __future__ import annotations

import json
from typing import Any

from app.services.structured_reads import format_local_datetime

MAX_TITLE_CHARACTERS = 96
MAX_RESULTS_IN_SPEECH = 3


def format_structured_read_answer(
    *,
    tool_name: str,
    result_content: str,
    success: bool,
    read_arguments: dict[str, Any],
) -> str:
    collection_name = "tasks" if tool_name == "list_tasks" else "reminders"
    if not success:
        return f"I couldn't check your {collection_name} right now."
    try:
        envelope = json.loads(result_content)
    except (TypeError, json.JSONDecodeError):
        return f"I couldn't check your {collection_name} right now."
    payload = envelope.get("result") if isinstance(envelope, dict) and envelope.get("ok") else None
    rows = payload.get(collection_name) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return f"I couldn't check your {collection_name} right now."
    if not rows:
        date_window = read_arguments.get("date_window")
        if date_window in {"today", "tomorrow"}:
            return f"You don't have any {collection_name} scheduled for {date_window}."
        if read_arguments.get("next_only") or read_arguments.get("upcoming"):
            return f"You don't have any upcoming {collection_name}."
        return f"You don't have any {collection_name} to show."

    summaries = [_summarize_row(row, collection_name) for row in rows[:MAX_RESULTS_IN_SPEECH]]
    if len(rows) == 1:
        return summaries[0] + "."
    remainder = len(rows) - len(summaries)
    summary = f"You have {len(rows)} {collection_name}: " + "; ".join(summaries) + "."
    if remainder > 0:
        summary = f"You have {len(rows)} {collection_name}. " + "; ".join(summaries)
        summary += f"; and {remainder} more."
    return summary[:600]


def _summarize_row(row: dict[str, Any], collection_name: str) -> str:
    singular_name = "task" if collection_name == "tasks" else "reminder"
    title = " ".join(str(row.get("title") or singular_name).split())
    if len(title) > MAX_TITLE_CHARACTERS:
        title = title[: MAX_TITLE_CHARACTERS - 1].rstrip() + "…"
    timestamp = (
        row.get("local_due_at") if collection_name == "tasks" else row.get("local_trigger_at")
    )
    timestamp = timestamp or (
        row.get("due_at") if collection_name == "tasks" else row.get("trigger_at")
    )
    local_time = format_local_datetime(timestamp if isinstance(timestamp, str) else None)
    if local_time:
        return f"{singular_name.capitalize()} '{title}' is scheduled for {local_time}"
    return f"{singular_name.capitalize()} '{title}'"
