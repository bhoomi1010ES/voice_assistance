from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.graph.errors import GraphEntityAmbiguous, GraphEntityNotFound

ResolutionStatus = Literal["resolved", "ambiguous", "not_found"]
ResolutionSource = Literal["canonical", "alias"]
TraversalDirection = Literal["outgoing", "incoming", "both"]


@dataclass(frozen=True, slots=True)
class GraphEntity:
    id: uuid.UUID
    user_id: uuid.UUID
    entity_type: str
    canonical_name: str
    normalized_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GraphAlias:
    id: uuid.UUID
    user_id: uuid.UUID
    entity_id: uuid.UUID
    alias: str
    normalized_alias: str
    source_memory_id: uuid.UUID | None
    source_kind: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GraphMemory:
    id: uuid.UUID
    user_id: uuid.UUID
    content: str
    memory_type: str
    subject: str | None
    predicate: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GraphEdge:
    relationship_id: uuid.UUID
    user_id: uuid.UUID
    source_entity_id: uuid.UUID
    source_name: str
    relationship_type: str
    target_entity_id: uuid.UUID
    target_name: str
    source_memory_id: uuid.UUID
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GraphNeighbor:
    origin_entity_id: uuid.UUID
    neighbor_entity_id: uuid.UUID
    edge: GraphEdge
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class GraphPath:
    """A directed traversal route with edges in traversal order."""

    entity_ids: tuple[uuid.UUID, ...]
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True, slots=True)
class GraphTraversalResult:
    start_entity: GraphEntity
    depth: int
    paths: tuple[GraphPath, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class GraphResolution:
    status: ResolutionStatus
    candidates: tuple[GraphEntity, ...]
    matched_by: ResolutionSource | None

    @property
    def entity(self) -> GraphEntity | None:
        """Return the unique match without selecting an ambiguous candidate."""

        return self.candidates[0] if self.status == "resolved" else None

    def require_entity(self) -> GraphEntity:
        if self.status == "not_found":
            raise GraphEntityNotFound("no graph entity matched the exact normalized name")
        if self.status == "ambiguous":
            raise GraphEntityAmbiguous("the exact normalized name matched multiple graph entities")
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class GraphAliasWriteResult:
    alias: GraphAlias
    created: bool


@dataclass(frozen=True, slots=True)
class GraphRelationshipWriteResult:
    edge: GraphEdge
    created: bool


@dataclass(frozen=True, slots=True)
class GraphEntityWriteResult:
    entity: GraphEntity
    created: bool


@dataclass(frozen=True, slots=True)
class GraphIndexResult:
    status: Literal["indexed", "already_indexed", "skipped"]
    reason_code: str | None = None
    entities_created: int = 0
    entities_reused: int = 0
    edges_created: int = 0
    aliases_created: int = 0
    timings_ms: tuple[tuple[str, float], ...] = ()


def resolution_result(
    candidates: tuple[GraphEntity, ...],
    *,
    matched_by: ResolutionSource | None,
) -> GraphResolution:
    if not candidates:
        return GraphResolution(status="not_found", candidates=(), matched_by=None)
    if len(candidates) == 1:
        return GraphResolution(status="resolved", candidates=candidates, matched_by=matched_by)
    return GraphResolution(status="ambiguous", candidates=candidates, matched_by=matched_by)


def entity_from_row(row: Any) -> GraphEntity:
    return GraphEntity(
        id=row.id,
        user_id=row.user_id,
        entity_type=row.entity_type,
        canonical_name=row.canonical_name,
        normalized_name=row.normalized_name,
        created_at=row.created_at,
    )
