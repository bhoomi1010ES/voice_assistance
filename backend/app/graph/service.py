from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.graph.errors import GraphEntityNotFound, GraphInvalidTraversal
from app.graph.repository import GraphRepository, normalize_graph_name, normalize_utc_datetime
from app.graph.types import (
    GraphAlias,
    GraphAliasWriteResult,
    GraphEntityWriteResult,
    GraphMemory,
    GraphPath,
    GraphRelationshipWriteResult,
    GraphResolution,
    GraphTraversalResult,
    TraversalDirection,
    resolution_result,
)

_STAGE3_MAX_DEPTH = 2


class GraphService:
    """Thin internal facade for exact resolution and bounded graph traversal."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository: GraphRepository | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.repository = repository or GraphRepository(self.settings)

    async def get_or_create_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_type: str,
        canonical_name: str,
    ) -> GraphEntityWriteResult:
        return await self.repository.get_or_create_entity(
            session,
            user_id=user_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
        )

    async def resolve_entity_exact(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        name: str,
        entity_type: str | None = None,
    ) -> GraphResolution:
        normalized_name = normalize_graph_name(name, max_length=512)
        canonical = await self.repository.find_entities_by_canonical_name(
            session,
            user_id=user_id,
            normalized_name=normalized_name,
            entity_type=entity_type,
        )
        if canonical:
            return resolution_result(canonical, matched_by="canonical")

        aliases = await self.repository.find_entities_by_alias(
            session,
            user_id=user_id,
            normalized_alias=normalized_name,
            entity_type=entity_type,
        )
        return resolution_result(aliases, matched_by="alias")

    async def get_aliases_for_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
    ) -> tuple[GraphAlias, ...]:
        return await self.repository.get_aliases_for_entity(
            session,
            user_id=user_id,
            entity_id=entity_id,
        )

    async def insert_alias(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        alias: str,
        source_kind: str,
        source_memory_id: uuid.UUID | None = None,
    ) -> GraphAliasWriteResult:
        row, created = await self.repository.insert_alias(
            session,
            user_id=user_id,
            entity_id=entity_id,
            alias=alias,
            source_kind=source_kind,
            source_memory_id=source_memory_id,
        )
        return GraphAliasWriteResult(alias=row, created=created)

    async def insert_relationship(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        source_entity_id: uuid.UUID,
        relationship_type: str,
        target_entity_id: uuid.UUID,
        source_memory_id: uuid.UUID,
        confidence: float,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        extraction_policy_version: str | None = None,
    ) -> GraphRelationshipWriteResult:
        return await self.repository.insert_relationship(
            session,
            user_id=user_id,
            source_entity_id=source_entity_id,
            relationship_type=relationship_type,
            target_entity_id=target_entity_id,
            source_memory_id=source_memory_id,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            extraction_policy_version=extraction_policy_version,
        )

    async def neighbors(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        direction: TraversalDirection = "outgoing",
        as_of: datetime | None = None,
        relationship_type: str | None = None,
        max_edges_per_entity: int | None = None,
    ) -> GraphTraversalResult:
        return await self.traverse(
            session,
            user_id=user_id,
            entity_id=entity_id,
            direction=direction,
            depth=1,
            as_of=as_of,
            relationship_type=relationship_type,
            max_edges_per_entity=max_edges_per_entity,
        )

    async def paths(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        direction: TraversalDirection = "outgoing",
        depth: int = 2,
        as_of: datetime | None = None,
        relationship_type: str | None = None,
        max_edges_per_entity: int | None = None,
        max_paths: int | None = None,
    ) -> GraphTraversalResult:
        return await self.traverse(
            session,
            user_id=user_id,
            entity_id=entity_id,
            direction=direction,
            depth=depth,
            as_of=as_of,
            relationship_type=relationship_type,
            max_edges_per_entity=max_edges_per_entity,
            max_paths=max_paths,
        )

    async def traverse(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        direction: TraversalDirection = "outgoing",
        depth: int = 1,
        as_of: datetime | None = None,
        relationship_type: str | None = None,
        max_edges_per_entity: int | None = None,
        max_paths: int | None = None,
    ) -> GraphTraversalResult:
        maximum_depth = min(_STAGE3_MAX_DEPTH, self.settings.graph_max_depth)
        if not 1 <= depth <= maximum_depth:
            raise GraphInvalidTraversal(
                f"traversal depth must be between 1 and the Stage 3 limit of {maximum_depth}"
            )
        if direction not in {"outgoing", "incoming", "both"}:
            raise GraphInvalidTraversal("direction must be outgoing, incoming, or both")
        path_limit = self.settings.graph_max_paths if max_paths is None else max_paths
        if not 1 <= path_limit <= self.settings.graph_max_paths:
            raise GraphInvalidTraversal("path limit must be within the configured graph bound")
        edge_limit = (
            self.settings.graph_max_edges_per_entity
            if max_edges_per_entity is None
            else max_edges_per_entity
        )
        if not 1 <= edge_limit <= self.settings.graph_max_edges_per_entity:
            raise GraphInvalidTraversal("edge limit must be within the configured graph bound")

        start_entity = await self.repository.get_owned_entity(
            session,
            user_id=user_id,
            entity_id=entity_id,
        )
        if start_entity is None:
            raise GraphEntityNotFound("graph entity is not available to user_id")
        traversal_time = normalize_utc_datetime(
            as_of or datetime.now(UTC),
            field_name="as_of",
        )
        first_hop = await self.repository.list_neighbors(
            session,
            user_id=user_id,
            entity_ids=(entity_id,),
            direction=direction,
            as_of=traversal_time,
            relationship_type=relationship_type,
            max_edges_per_entity=edge_limit,
        )

        paths: list[GraphPath] = []
        truncated = any(step.truncated for step in first_hop)
        path_limit_hit = False
        if depth == 1:
            for step in first_hop:
                paths.append(
                    GraphPath(
                        entity_ids=(entity_id, step.neighbor_entity_id),
                        edges=(step.edge,),
                    )
                )
                if len(paths) > path_limit:
                    paths.pop()
                    truncated = True
                    path_limit_hit = True
                    break
        else:
            second_hop = await self.repository.list_neighbors(
                session,
                user_id=user_id,
                entity_ids=tuple(dict.fromkeys(step.neighbor_entity_id for step in first_hop)),
                direction=direction,
                as_of=traversal_time,
                relationship_type=relationship_type,
                max_edges_per_entity=edge_limit,
            )
            by_origin: dict[uuid.UUID, list] = {}
            for step in second_hop:
                by_origin.setdefault(step.origin_entity_id, []).append(step)
            truncated = truncated or any(step.truncated for step in second_hop)

            for first in first_hop:
                for second in by_origin.get(first.neighbor_entity_id, ()):
                    if second.neighbor_entity_id in {entity_id, first.neighbor_entity_id}:
                        continue
                    if second.edge.relationship_id == first.edge.relationship_id:
                        continue
                    paths.append(
                        GraphPath(
                            entity_ids=(
                                entity_id,
                                first.neighbor_entity_id,
                                second.neighbor_entity_id,
                            ),
                            edges=(first.edge, second.edge),
                        )
                    )
                    if len(paths) > path_limit:
                        paths.pop()
                        truncated = True
                        path_limit_hit = True
                        break
                if path_limit_hit:
                    break

        return GraphTraversalResult(
            start_entity=start_entity,
            depth=depth,
            paths=tuple(paths),
            truncated=truncated,
        )

    async def source_memories(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        source_memory_ids: Sequence[uuid.UUID],
        limit: int | None = None,
    ) -> tuple[GraphMemory, ...]:
        return await self.repository.get_source_memories(
            session,
            user_id=user_id,
            source_memory_ids=source_memory_ids,
            limit=limit,
        )

    async def source_memories_for_paths(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        paths: Sequence[GraphPath],
        limit: int | None = None,
    ) -> tuple[GraphMemory, ...]:
        memory_ids = tuple(
            dict.fromkeys(edge.source_memory_id for path in paths for edge in path.edges)
        )
        return await self.source_memories(
            session,
            user_id=user_id,
            source_memory_ids=memory_ids,
            limit=limit,
        )
