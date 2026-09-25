"""Deterministic routing from bounded memory evidence to a safe outcome."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.memory.types import FusedMemory, MemoryRetrievalResult, MemoryStatus

MAX_EVIDENCE_ITEMS = 5
MAX_EVIDENCE_CHARACTERS = 1_200
MAX_DIRECT_ANSWER_CHARACTERS = 240
_STOP_WORDS = frozenset(
    {
        "about",
        "are",
        "did",
        "do",
        "does",
        "for",
        "how",
        "i",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
    }
)
_EXACT_QUESTION = re.compile(
    r"^(?:what\s+(?:is|are|do)\s+my\b|who\s+(?:is|are)\s+my\b|"
    r"where\s+do\s+i\b|when\s+did\s+i\b|do\s+i\b|did\s+i\b)",
    re.IGNORECASE,
)
_SYNTHESIS_MARKERS = re.compile(
    r"\b(?:compare|difference|similar|summari[sz]e|explain|why|how|all|everything|"
    r"tell\s+me\s+about|what\s+do\s+you\s+remember)\b",
    re.IGNORECASE,
)


class MemoryEvaluationRoute(StrEnum):
    DIRECT_RAG = "DIRECT_RAG"
    RAG_PLUS_LLM = "RAG_PLUS_LLM"
    NO_RESULT = "NO_RESULT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class MemoryEvidence:
    memory_id: uuid.UUID
    source_message_id: uuid.UUID | None
    source: tuple[str, ...]
    rank: int
    score: float
    memory_type: str
    status: str
    created_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    subject: str | None
    predicate: str | None
    content: str


@dataclass(frozen=True)
class MemoryEvaluation:
    route: MemoryEvaluationRoute
    reason: str
    evidence: tuple[MemoryEvidence, ...] = ()
    direct_text: str | None = None
    evidence_ids: tuple[uuid.UUID, ...] = ()


def evaluate_memory_result(
    result: MemoryRetrievalResult,
    *,
    user_id: uuid.UUID,
    query: str,
    now: datetime,
) -> MemoryEvaluation:
    """Select abstention, direct evidence, or evidence-grounded synthesis.

    Retrieval ranks/scores are retained for diagnostics, never interpreted as
    probabilities. Direct answers require one current active structured/lexical
    fact with an explicit subject/predicate match and no competing fact.
    """

    if result.status != "ready":
        return MemoryEvaluation(
            MemoryEvaluationRoute.UNAVAILABLE,
            "retrieval_disabled" if result.status == "disabled" else "retrieval_degraded",
        )
    if any(memory.user_id != user_id for memory in result.memories):
        return MemoryEvaluation(MemoryEvaluationRoute.UNAVAILABLE, "owner_scope_violation")
    active_memories = tuple(
        memory for memory in result.memories if memory.status == MemoryStatus.ACTIVE
    )
    if not _asks_for_past(query):
        active_memories = tuple(
            memory
            for memory in active_memories
            if (memory.valid_from is None or _as_utc(memory.valid_from) <= _as_utc(now))
            and (memory.valid_to is None or _as_utc(memory.valid_to) > _as_utc(now))
        )
    if not active_memories:
        return MemoryEvaluation(MemoryEvaluationRoute.NO_RESULT, "no_current_matching_evidence")
    grounded_memories = tuple(
        memory for memory in active_memories if set(memory.sources) & {"structured", "fts"}
    )
    if not grounded_memories:
        return MemoryEvaluation(MemoryEvaluationRoute.NO_RESULT, "no_lexical_or_structured_support")

    memories = grounded_memories[:MAX_EVIDENCE_ITEMS]
    evidence = tuple(_to_evidence(memory) for memory in memories)
    evidence_ids = tuple(item.memory_id for item in evidence)
    if _has_conflicting_active_facts(memories):
        return MemoryEvaluation(
            MemoryEvaluationRoute.RAG_PLUS_LLM,
            "conflicting_evidence",
            evidence,
            evidence_ids=evidence_ids,
        )
    if len(memories) == 1 and _supports_one_exact_fact(memories[0], query=query, now=now):
        return MemoryEvaluation(
            MemoryEvaluationRoute.DIRECT_RAG,
            "single_supported_current_fact",
            evidence,
            direct_text=_format_direct_answer(memories[0]),
            evidence_ids=evidence_ids,
        )
    return MemoryEvaluation(
        MemoryEvaluationRoute.RAG_PLUS_LLM,
        "multiple_or_non_exact_evidence",
        evidence,
        evidence_ids=evidence_ids,
    )


def bound_memory_evidence(
    memories: tuple[FusedMemory, ...] | list[FusedMemory],
) -> tuple[FusedMemory, ...]:
    """Bound evidence count and per-record text before prompt assembly."""

    remaining = MAX_EVIDENCE_CHARACTERS
    bounded: list[FusedMemory] = []
    for memory in memories[:MAX_EVIDENCE_ITEMS]:
        if remaining <= 0:
            break
        content = " ".join(memory.content.split())[:remaining]
        if not content:
            continue
        bounded.append(memory.model_copy(update={"content": content}))
        remaining -= len(content)
    return tuple(bounded)


def _to_evidence(memory: FusedMemory) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory.memory_id,
        source_message_id=memory.source_message_id,
        source=memory.sources,
        rank=memory.rank,
        score=memory.score,
        memory_type=memory.memory_type.value,
        status=memory.status.value,
        created_at=memory.created_at,
        valid_from=memory.valid_from,
        valid_to=memory.valid_to,
        subject=memory.subject,
        predicate=memory.predicate,
        content=" ".join(memory.content.split())[:MAX_EVIDENCE_CHARACTERS],
    )


def _has_conflicting_active_facts(memories: tuple[FusedMemory, ...]) -> bool:
    keyed: dict[tuple[str, str], set[str]] = {}
    for memory in memories:
        if memory.status != MemoryStatus.ACTIVE or not memory.subject or not memory.predicate:
            continue
        key = (_normalize(memory.subject), _normalize(memory.predicate))
        value = _normalize(str(memory.object_json or memory.content))
        keyed.setdefault(key, set()).add(value)
    return any(len(values) > 1 for values in keyed.values())


def _supports_one_exact_fact(memory: FusedMemory, *, query: str, now: datetime) -> bool:
    if memory.status != MemoryStatus.ACTIVE or memory.memory_type.value == "summary":
        return False
    if memory.valid_from is not None and _as_utc(memory.valid_from) > _as_utc(now):
        return False
    if memory.valid_to is not None and _as_utc(memory.valid_to) <= _as_utc(now):
        return False
    if not memory.subject or not memory.predicate:
        return False
    if not (set(memory.sources) & {"structured", "fts"}):
        return False
    if not _EXACT_QUESTION.search(query.strip()) or _SYNTHESIS_MARKERS.search(query):
        return False
    anchors = {
        token
        for token in re.findall(r"[a-z0-9]+", f"{memory.subject} {memory.predicate}".casefold())
        if len(token) >= 3 and token not in _STOP_WORDS
    }
    query_tokens = {
        token for token in re.findall(r"[a-z0-9]+", query.casefold()) if token not in _STOP_WORDS
    }
    return bool(anchors & query_tokens)


def _format_direct_answer(memory: FusedMemory) -> str:
    content = " ".join(memory.content.split()).strip(" \t\r\n\"'")
    if len(content) > MAX_DIRECT_ANSWER_CHARACTERS:
        content = content[: MAX_DIRECT_ANSWER_CHARACTERS - 1].rstrip() + "…"
    return f"I have this saved: “{content}”"


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _asks_for_past(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:used to|previously|formerly|back then|in the past|what was)\b", query, re.I
        )
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
