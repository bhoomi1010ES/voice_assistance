from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.graph.types import (
    GraphFallbackReason,
    GraphRetrievalMode,
    GraphSkipReason,
    RelationshipType,
)

_MAX_QUERY_LENGTH = 2_000
_DEFAULT_MAX_QUERY_ENTITIES = 3
_DEFAULT_MAX_DEPTH = 2
_ENTITY_WORD = r"[A-Za-z0-9][A-Za-z0-9'_-]*"
_CAPITALIZED_ENTITY = re.compile(
    r"\b[A-Z][A-Za-z0-9'_-]*(?:\s+[A-Z][A-Za-z0-9'_-]*){0,3}\b"
)
_PREPOSITION_ENTITY = re.compile(
    rf"\b(?:with|to|about|for|from|at|on|of)\s+({_ENTITY_WORD}(?:\s+{_ENTITY_WORD}){{0,2}})",
    re.IGNORECASE,
)
_QUOTED_ENTITY = re.compile(r"[\"']([^\"']+)[\"']")

_NON_ENTITY_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
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
        "or",
        "the",
        "tell",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)

_RELATIONSHIP_PATTERNS: tuple[tuple[re.Pattern[str], RelationshipType], ...] = (
    (re.compile(r"\bworks?\s+on\b", re.IGNORECASE), RelationshipType.WORKS_ON),
    (re.compile(r"\bmanages?\b", re.IGNORECASE), RelationshipType.MANAGES),
    (re.compile(r"\breports?\s+to\b", re.IGNORECASE), RelationshipType.REPORTS_TO),
    (re.compile(r"\bmembers?\s+of\b", re.IGNORECASE), RelationshipType.MEMBER_OF),
    (re.compile(r"\bdepends?\s+on\b", re.IGNORECASE), RelationshipType.DEPENDS_ON),
    (re.compile(r"\bblocked\s+by\b", re.IGNORECASE), RelationshipType.BLOCKED_BY),
    (re.compile(r"\b(?:tested|testing)\s+by?\b", re.IGNORECASE), RelationshipType.TESTED_BY),
    (
        re.compile(
            r"\b(?:works?\s+with|related\s+to|connected\s+to|"
            r"colleague|friend|family)\b",
            re.IGNORECASE,
        ),
        RelationshipType.RELATED_TO,
    ),
)
_RELATIONSHIP_MARKER = re.compile(
    r"\b(?:works?\s+(?:with|on)|related\s+to|connected\s+to|colleague|friend|family|"
    r"manages?|reports?\s+to|members?\s+of|depends?\s+on|blocked\s+by|tested|testing|assigned\s+to)\b",
    re.IGNORECASE,
)
_DEPTH_TWO_MARKER = re.compile(
    r"\b(?:two[- ]hop|through|indirectly|whose|that|which)\b|"
    r"\b(?:testing|tested)\b.*\b(?:works?|manages?|reports?|depends?)\b|"
    r"\b(?:works?|manages?|reports?|depends?)\b.*\b(?:works?|manages?|reports?)\b",
    re.IGNORECASE,
)


class GraphQueryDecision(BaseModel):
    """Deterministic graph routing metadata kept separate from ``MemoryQueryPlan``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: str = Field(min_length=1, max_length=_MAX_QUERY_LENGTH)
    graph_mode: GraphRetrievalMode = "off"
    should_query: bool = False
    depth: int = Field(default=1, ge=1, le=_DEFAULT_MAX_DEPTH)
    query_entities: tuple[str, ...] = Field(default=(), max_length=10)
    relationship_type: RelationshipType | None = None
    skip_reason: GraphSkipReason | None = None
    fallback_reasons: tuple[GraphFallbackReason, ...] = Field(default=(), max_length=6)

    @field_validator("normalized_query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("query_entities")
    @classmethod
    def validate_query_entities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(" ".join(value.split()) for value in values)
        if any(not value or len(value) > 512 for value in cleaned):
            raise ValueError("graph query entities must be non-blank and at most 512 characters")
        if len(set(value.casefold() for value in cleaned)) != len(cleaned):
            raise ValueError("graph query entities must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_routing_state(self) -> GraphQueryDecision:
        if self.should_query and self.skip_reason is not None:
            raise ValueError("a graph query cannot be enabled with a skip reason")
        if not self.should_query and self.skip_reason is None:
            raise ValueError("a skipped graph query must include a skip reason")
        if self.should_query and not self.query_entities:
            raise ValueError("an enabled graph query must include an entity candidate")
        return self


def build_graph_query_decision(
    query: str,
    *,
    graph_rag_mode: GraphRetrievalMode = "off",
    memory_retrieval_mode: Literal["off", "shadow", "inject"] = "off",
    max_query_entities: int = _DEFAULT_MAX_QUERY_ENTITIES,
    max_depth: int = _DEFAULT_MAX_DEPTH,
) -> GraphQueryDecision:
    """Build a bounded graph decision without an LLM or database call."""

    if not 1 <= max_query_entities <= 10:
        raise ValueError("max_query_entities must be between 1 and 10")
    if not 1 <= max_depth <= _DEFAULT_MAX_DEPTH:
        raise ValueError("max_depth must be between 1 and 2 for the G0 contract")
    if graph_rag_mode not in {"off", "shadow", "inject"}:
        raise ValueError("graph_rag_mode must be off, shadow, or inject")
    if memory_retrieval_mode not in {"off", "shadow", "inject"}:
        raise ValueError("memory_retrieval_mode must be off, shadow, or inject")

    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("graph query must not be blank")
    if len(normalized) > _MAX_QUERY_LENGTH:
        return GraphQueryDecision(
            normalized_query=normalized[:_MAX_QUERY_LENGTH].rstrip(),
            graph_mode=graph_rag_mode,
            skip_reason=GraphSkipReason.QUERY_TOO_LONG,
        )
    if graph_rag_mode == "off":
        return GraphQueryDecision(
            normalized_query=normalized,
            graph_mode=graph_rag_mode,
            skip_reason=GraphSkipReason.GRAPH_DISABLED,
        )
    if memory_retrieval_mode == "off":
        return GraphQueryDecision(
            normalized_query=normalized,
            graph_mode=graph_rag_mode,
            skip_reason=GraphSkipReason.MEMORY_RETRIEVAL_DISABLED,
        )
    if _RELATIONSHIP_MARKER.search(normalized) is None:
        return GraphQueryDecision(
            normalized_query=normalized,
            graph_mode=graph_rag_mode,
            skip_reason=GraphSkipReason.NOT_RELATIONSHIP_QUERY,
        )

    relationship_type = next(
        (
            relationship
            for pattern, relationship in _RELATIONSHIP_PATTERNS
            if pattern.search(normalized)
        ),
        None,
    )
    entity_names = _extract_entity_names(normalized, max_count=max_query_entities)
    if not entity_names:
        return GraphQueryDecision(
            normalized_query=normalized,
            graph_mode=graph_rag_mode,
            relationship_type=relationship_type,
            skip_reason=GraphSkipReason.NO_ENTITY_CANDIDATE,
        )
    depth = 2 if max_depth >= 2 and _DEPTH_TWO_MARKER.search(normalized) else 1
    return GraphQueryDecision(
        normalized_query=normalized,
        graph_mode=graph_rag_mode,
        should_query=True,
        depth=depth,
        query_entities=entity_names,
        relationship_type=relationship_type,
    )


def _extract_entity_names(query: str, *, max_count: int) -> tuple[str, ...]:
    candidates: list[str] = []
    for pattern in (_QUOTED_ENTITY, _CAPITALIZED_ENTITY, _PREPOSITION_ENTITY):
        for match in pattern.finditer(query):
            value = " ".join(match.group(1 if pattern is not _CAPITALIZED_ENTITY else 0).split())
            value = value.strip(".,?!:;()[]{}")
            if not value or value.casefold() in _NON_ENTITY_TERMS:
                continue
            if all(token.casefold() in _NON_ENTITY_TERMS for token in value.split()):
                continue
            if any(value.casefold() == existing.casefold() for existing in candidates):
                continue
            candidates.append(value)
            if len(candidates) >= max_count:
                return tuple(candidates)
    return tuple(candidates)
