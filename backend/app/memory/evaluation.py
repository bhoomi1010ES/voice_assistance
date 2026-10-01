"""Deterministic routing from bounded memory evidence to a safe outcome."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.memory.types import FusedMemory, MemoryRetrievalResult, MemoryStatus, MemoryType

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
_RELEVANCE_STOP_WORDS = _STOP_WORDS | frozenset(
    {
        "favorite",
        "favourite",
        "fact",
        "facts",
        "like",
        "memory",
        "preference",
        "prefer",
        "remember",
        "saved",
    }
)
# Predicate vocabulary for semantic evidence that has no lexical/structured
# retrieval signal. These are relation aliases, not query-to-answer rules:
# the actual value must still come from the owner-scoped memory evidence.
_PREDICATE_QUERY_ALIASES = {
    "home_location": frozenset(
        {"address", "city", "live", "location", "reside", "residence", "stay"}
    ),
    "home_base": frozenset(
        {"address", "city", "live", "location", "reside", "residence", "stay"}
    ),
}
_EXACT_QUESTION = re.compile(
    r"^(?:what\s+(?:is|are|do)\s+my\b|who\s+(?:is|are)\s+my\b|"
    r"what\s+.+\s+does\s+my\s+.+\s+(?:use|run|have)\b|"
    r"(?:which|what)\s+.+\s+(?:does\s+)?my\s+.+\s+(?:uses?|runs?|has)\b|"
    r"what\s+.+\s+did\s+i\s+say\s+my\s+.+\b|"
    r"which\s+.+\s+do\s+i\s+prefer\b|which\s+.+\s+did\s+i\s+say\b|"
    r"do\s+you\s+remember\b|where\s+do\s+i\b|when\s+did\s+i\b|"
    r"do\s+i\b|did\s+i\b)",
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
        memory
        for memory in active_memories
        if (
            set(memory.sources) & {"structured", "fts"}
            and _supports_query_anchor(memory, query=query, plan=result.plan)
        )
        or (
            set(memory.sources) == {"dense"}
            and _supports_dense_predicate_anchor(memory, query=query)
        )
    )
    if not grounded_memories:
        return MemoryEvaluation(
            MemoryEvaluationRoute.NO_RESULT,
            "no_relevant_lexical_or_structured_support",
        )

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


def _supports_query_anchor(memory: FusedMemory, *, query: str, plan) -> bool:
    """Reject broad structured scans and generic lexical overlap as evidence.

    Structured retrieval can return every record of an inferred type (for
    example, all preferences). That is candidate generation, not relevance
    proof. Require a query-specific anchor in the candidate's owned fields,
    while preserving time-bounded structured results whose relevance comes
    from the query plan's date filter.
    """

    if not plan.memory_types:
        # Broad/synthesis queries deliberately combine heterogeneous memories;
        # preserve the existing retrieval set instead of narrowing it using a
        # lexical heuristic intended for category-scoped candidate scans.
        return True
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", query.casefold())
        if len(token) >= 3 and token not in _STOP_WORDS
    }
    if not tokens:
        return bool(plan.start_at is not None or plan.end_at is not None)
    candidate_text = " ".join(
        value
        for value in (
            memory.content,
            memory.subject or "",
            memory.predicate or "",
            json.dumps(memory.object_json or {}, ensure_ascii=False, sort_keys=True),
        )
        if value
    ).casefold()
    candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate_text))
    if memory.memory_type in plan.memory_types:
        if memory.memory_type == MemoryType.PREFERENCE and not _matches_explicit_preference_field(
            memory, tokens=tokens
        ):
            return False
        anchors = tokens - _RELEVANCE_STOP_WORDS
        return bool(anchors & candidate_tokens)
    # Cross-type evidence can help a synthesis response, but a single shared
    # noun is too weak (e.g. a hardware fact is not a keyboard preference).
    return len(tokens & candidate_tokens) >= 2


def _matches_explicit_preference_field(memory: FusedMemory, *, tokens: set[str]) -> bool:
    """Keep broad preference scans from mixing distinct explicitly named fields."""

    predicate = re.sub(r"[-\s]+", "_", (memory.predicate or "").casefold())
    requested_fields = {
        "beverage": frozenset({"beverage", "drink"}),
        "response_style": frozenset({"response", "style", "tone"}),
        "editor": frozenset({"editor", "ide"}),
        "language": frozenset({"language"}),
        "commute": frozenset({"commute", "commuting"}),
        "tool": frozenset({"tool", "tools"}),
    }
    explicit = [
        field
        for field, aliases in requested_fields.items()
        if tokens & aliases
    ]
    return not explicit or predicate in explicit


def _supports_dense_predicate_anchor(memory: FusedMemory, *, query: str) -> bool:
    """Allow dense-only evidence only for an explicitly matching predicate.

    Vector similarity and reranker scores can surface plausible but wrong
    personal facts (for example, home location for an office/work question).
    Dense retrieval is therefore not evidence by itself. This narrow fallback
    requires a query term associated with the stored predicate and rejects an
    unmatched possessive subject or explicit hyphenated entity.
    """

    if memory.memory_type == MemoryType.PROJECT and _supports_named_project_anchor(
        memory, query=query
    ):
        return True
    if not memory.predicate:
        return False
    predicate = re.sub(r"[-\s]+", "_", memory.predicate.casefold())
    aliases = _PREDICATE_QUERY_ALIASES.get(predicate)
    if aliases is None:
        return False
    query_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
    predicate_tokens = set(re.findall(r"[a-z0-9]+", predicate))
    if not query_tokens & {"i", "me", "my", "our", "we"}:
        return False
    if not query_tokens & (aliases | predicate_tokens):
        return False

    candidate_text = " ".join(
        value
        for value in (
            memory.content,
            memory.subject or "",
            memory.predicate,
            json.dumps(memory.object_json or {}, ensure_ascii=False, sort_keys=True),
        )
        if value
    ).casefold()
    candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate_text))

    # A possessive qualifier such as "my brother" or "my office" must match
    # the evidence; the shared predicate word (e.g. "live") is insufficient.
    for match in re.finditer(r"\bmy\s+([a-z][a-z0-9-]*)", query.casefold()):
        qualifier = match.group(1)
        qualifier_tokens = set(re.findall(r"[a-z0-9]+", qualifier))
        if not qualifier_tokens <= (candidate_tokens | aliases | predicate_tokens):
            return False

    # Do not answer a named/hyphenated person or entity query with the current
    # user's personal fact unless that identifier is present in the evidence.
    for match in re.finditer(r"\b[a-z0-9]+(?:-[a-z0-9]+){2,}\b", query.casefold()):
        if match.group(0) not in candidate_text:
            return False
    return True


def _supports_named_project_anchor(memory: FusedMemory, *, query: str) -> bool:
    """Ground dense project evidence in both a user-memory request and its name.

    A project name is a strong query-specific anchor for a broad project-memory
    question ("what did I tell you about my Willow Beacon project?"). For a
    field-specific question, only admit candidates whose stored predicate
    matches the field asked about; this prevents a nearby project database fact
    from answering a framework question.
    """

    if not memory.subject or not memory.predicate:
        return False
    query_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
    if not query_tokens & {"i", "me", "my", "our", "we", "remember", "remembered", "told", "saved"}:
        return False
    subject_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", memory.subject.casefold())
        if len(token) >= 3 and token not in _STOP_WORDS and token != "project"
    }
    if not subject_tokens or not subject_tokens.issubset(query_tokens):
        return False

    predicate = re.sub(r"[-\s]+", "_", memory.predicate.casefold())
    query_fields = {
        "framework": {"framework", "stack", "technology", "platform", "toolchain"},
        "database": {"database", "datastore", "storage", "db"},
    }
    explicit_fields = set().union(*query_fields.values())
    has_explicit_field = bool(query_tokens & explicit_fields) or "data store" in query.casefold()
    if has_explicit_field:
        aliases = query_fields.get(predicate)
        if aliases is None:
            return False
        if not query_tokens & aliases and not (
            predicate == "database" and "data store" in query.casefold()
        ):
            return False
    return True


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
