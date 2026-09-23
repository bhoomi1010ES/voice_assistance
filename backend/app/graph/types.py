from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from app.graph.errors import GraphEntityAmbiguous, GraphEntityNotFound

ResolutionStatus = Literal["resolved", "ambiguous", "not_found"]
ResolutionSource = Literal["canonical", "alias"]
TraversalDirection = Literal["outgoing", "incoming", "both"]


class EntityType(StrEnum):
    """Controlled entity types used by graph contracts.

    Legacy ``entities`` rows may still contain ``subject`` until the graph
    schema migration/backfill normalizes them. The contract intentionally does
    not silently treat that legacy storage value as a new graph type.
    """

    SELF = "self"
    PERSON = "person"
    PLACE = "place"
    ORGANIZATION = "organization"
    PROJECT = "project"
    PRODUCT = "product"
    EVENT = "event"
    OTHER = "other"


class RelationshipType(StrEnum):
    """Versioned relationship vocabulary exposed to graph callers."""

    COLLEAGUE_OF = "COLLEAGUE_OF"
    FRIEND_OF = "FRIEND_OF"
    FAMILY_OF = "FAMILY_OF"
    WORKS_AT = "WORKS_AT"
    WORKS_ON = "WORKS_ON"
    LIVES_IN = "LIVES_IN"
    LOCATED_AT = "LOCATED_AT"
    OWNS = "OWNS"
    MEMBER_OF = "MEMBER_OF"
    MANAGES = "MANAGES"
    REPORTS_TO = "REPORTS_TO"
    DEPENDS_ON = "DEPENDS_ON"
    RELATED_TO = "RELATED_TO"
    RESPONSIBLE_FOR = "RESPONSIBLE_FOR"
    TESTED_BY = "TESTED_BY"
    ASSIGNED_TO = "ASSIGNED_TO"
    BLOCKED_BY = "BLOCKED_BY"
    DISCUSSED_WITH = "DISCUSSED_WITH"


class GraphSkipReason(StrEnum):
    """Deterministic reasons a graph query is intentionally not run."""

    GRAPH_DISABLED = "graph_disabled"
    MEMORY_RETRIEVAL_DISABLED = "memory_retrieval_disabled"
    NOT_RELATIONSHIP_QUERY = "not_relationship_query"
    NO_ENTITY_CANDIDATE = "no_entity_candidate"
    QUERY_TOO_LONG = "query_too_long"


class GraphFallbackReason(StrEnum):
    """Non-secret reasons a graph result must fall back to hybrid retrieval."""

    ENTITY_NOT_FOUND = "entity_not_found"
    ENTITY_AMBIGUOUS = "entity_ambiguous"
    TRAVERSAL_TRUNCATED = "traversal_truncated"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    MEMORY_DISABLED = "memory_disabled"
    GRAPH_TIMEOUT = "graph_timeout"
    GRAPH_DATABASE_ERROR = "graph_database_error"
    GRAPH_CANCELLED = "graph_cancelled"


GraphRetrievalMode = Literal["off", "shadow", "inject"]


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
    """A traversal route with evidence edges in traversal order.

    An incoming hop can traverse an edge opposite to its stored source/target
    direction, so validation checks endpoint continuity while preserving the
    edge's canonical direction for relationship semantics.
    """

    entity_ids: tuple[uuid.UUID, ...]
    edges: tuple[GraphEdge, ...]

    def __post_init__(self) -> None:
        if len(self.entity_ids) < 2:
            raise ValueError("graph path must contain at least two entities")
        if len(self.edges) != len(self.entity_ids) - 1:
            raise ValueError("graph path edges must connect every entity hop")
        for index, edge in enumerate(self.edges):
            hop_entities = {
                self.entity_ids[index],
                self.entity_ids[index + 1],
            }
            if {edge.source_entity_id, edge.target_entity_id} != hop_entities:
                raise ValueError("graph path edge does not match entity hop")

    @property
    def hop_count(self) -> int:
        return len(self.edges)

    @property
    def source_memory_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(dict.fromkeys(edge.source_memory_id for edge in self.edges))


@dataclass(frozen=True, slots=True)
class GraphEvidenceBundle:
    """Complete, provenance-preserving evidence for one graph result set."""

    paths: tuple[GraphPath, ...]
    source_memory_ids: tuple[uuid.UUID, ...]
    source_memories: tuple[GraphMemory, ...] = ()
    truncated: bool = False

    def __post_init__(self) -> None:
        if any(not isinstance(memory_id, uuid.UUID) for memory_id in self.source_memory_ids):
            raise ValueError("graph evidence source memory IDs must be UUIDs")
        unique_memory_ids = tuple(dict.fromkeys(self.source_memory_ids))
        if unique_memory_ids != self.source_memory_ids:
            raise ValueError("graph evidence source memory IDs must be unique")
        path_memory_ids = {
            edge.source_memory_id for path in self.paths for edge in path.edges
        }
        if not path_memory_ids.issubset(self.source_memory_ids):
            raise ValueError("graph evidence must include every path source memory ID")
        supplied_memory_ids = tuple(memory.id for memory in self.source_memories)
        if not set(supplied_memory_ids).issubset(self.source_memory_ids):
            raise ValueError("graph source memories must belong to the evidence IDs")


@dataclass(frozen=True, slots=True)
class GraphIndexJobPayload:
    """Checked payload shared by graph-index enqueue and worker boundaries."""

    user_id: uuid.UUID
    memory_id: uuid.UUID
    policy_version: str
    job_type: Literal["index_memory_graph"] = "index_memory_graph"

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, uuid.UUID) or not isinstance(self.memory_id, uuid.UUID):
            raise ValueError("graph job user_id and memory_id must be UUIDs")
        if self.job_type != "index_memory_graph":
            raise ValueError("graph job type must be index_memory_graph")
        if not self.policy_version.strip():
            raise ValueError("graph job policy_version must not be blank")
        if len(self.policy_version) > 64:
            raise ValueError("graph job policy_version must be at most 64 characters")


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


@dataclass(frozen=True, slots=True)
class GraphBackfillResult:
    """One restartable, owner-scoped graph backfill batch."""

    user_id: uuid.UUID
    scanned: int
    indexed: int
    already_indexed: int
    skipped: int
    next_cursor: uuid.UUID | None
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, uuid.UUID):
            raise ValueError("graph backfill user_id must be a UUID")
        counts = (self.scanned, self.indexed, self.already_indexed, self.skipped)
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("graph backfill counts must be non-negative integers")
        if self.scanned != self.indexed + self.already_indexed + self.skipped:
            raise ValueError("graph backfill counts must add up to scanned rows")
        if self.next_cursor is not None and not isinstance(self.next_cursor, uuid.UUID):
            raise ValueError("graph backfill cursor must be a UUID")
        if not isinstance(self.complete, bool):
            raise ValueError("graph backfill complete must be a boolean")


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
