"""Internal, owner-scoped GraphRAG persistence and bounded traversal foundation."""

from app.graph.errors import (
    GraphEntityAmbiguous,
    GraphEntityNotFound,
    GraphInvalidTraversal,
    GraphOwnershipError,
    GraphRepositoryError,
    GraphWriteConflict,
)
from app.graph.indexing import GraphIndexingService
from app.graph.policy import GRAPH_INDEX_POLICY_VERSION
from app.graph.repository import GraphRepository, normalize_graph_name
from app.graph.service import GraphService
from app.graph.types import (
    GraphAlias,
    GraphAliasWriteResult,
    GraphEdge,
    GraphEntity,
    GraphEntityWriteResult,
    GraphIndexResult,
    GraphMemory,
    GraphNeighbor,
    GraphPath,
    GraphRelationshipWriteResult,
    GraphResolution,
    GraphTraversalResult,
    TraversalDirection,
)

__all__ = [
    "GRAPH_INDEX_POLICY_VERSION",
    "GraphAlias",
    "GraphAliasWriteResult",
    "GraphEdge",
    "GraphEntity",
    "GraphEntityWriteResult",
    "GraphEntityAmbiguous",
    "GraphEntityNotFound",
    "GraphInvalidTraversal",
    "GraphIndexResult",
    "GraphIndexingService",
    "GraphMemory",
    "GraphNeighbor",
    "GraphOwnershipError",
    "GraphPath",
    "GraphRelationshipWriteResult",
    "GraphRepository",
    "GraphRepositoryError",
    "GraphResolution",
    "GraphService",
    "GraphTraversalResult",
    "GraphWriteConflict",
    "TraversalDirection",
    "normalize_graph_name",
]
