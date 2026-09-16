from __future__ import annotations

import re
from dataclasses import dataclass

from app.llm.types import LLMNamedToolChoice, LLMToolChoice
from app.memory.types import MemoryIntent, build_memory_query_plan


@dataclass(frozen=True)
class WaitStatus:
    """The short, deterministic status spoken while a response is prepared."""

    category: str
    phrase: str


_TIME_OR_DATE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+)?time|"
    r"what\s+(?:is|was)\s+(?:today(?:'s)?\s+)?date|"
    r"what\s+date\s+is\s+it|today(?:'s)?\s+date)\b",
    re.IGNORECASE,
)
_PHRASES: dict[str, tuple[str, ...]] = {
    "memory_recall": (
        "I'm looking that up in memory.",
        "Checking what I have saved.",
    ),
    "memory_save": ("I'll save that.",),
    "task": ("I'm on it.", "Setting that up."),
    "time_date": ("Let me check that.",),
    "default": ("We're reviewing your query.", "Give me a moment."),
}


def classify_wait_status(
    transcript: str,
    *,
    memory_retrieval_will_run: bool,
    tool_choice: LLMToolChoice = "auto",
    variant_index: int = 0,
) -> WaitStatus:
    """Choose a latency-covering phrase without another model request.

    Tool routing is deliberately preferred over lexical heuristics so a
    request to save a memory is not described as a memory lookup. Retrieval
    language is considered only when the configured retrieval path will run.
    """

    category = "default"
    if isinstance(tool_choice, LLMNamedToolChoice):
        tool_name = tool_choice.function.name
        if tool_name == "memory_save":
            category = "memory_save"
        elif tool_name in {"create_task", "create_reminder", "update_task", "update_reminder"}:
            category = "task"
        elif tool_name in {"get_current_time", "get_current_date"}:
            category = "time_date"
    elif memory_retrieval_will_run:
        try:
            plan = build_memory_query_plan(transcript)
        except ValueError:
            plan = None
        if plan is not None and (
            plan.intent != MemoryIntent.GENERAL
            or any(
                marker in transcript.casefold()
                for marker in ("remember", "favorite", "favourite", "what do i like", "when did i")
            )
        ):
            category = "memory_recall"

    if category == "default" and _TIME_OR_DATE.search(transcript):
        # A current-time/date tool normally reaches the named-tool branch,
        # while this keeps the status useful for a provider without tools.
        category = "time_date"

    phrases = _PHRASES[category]
    phrase = phrases[variant_index % len(phrases)]
    return WaitStatus(category=category, phrase=phrase)
