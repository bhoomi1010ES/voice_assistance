from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.graph.errors import (
    GraphEntityNotFound,
    GraphInvalidTraversal,
    GraphQueryCancelled,
    GraphRepositoryError,
)
from app.graph.query import GraphQueryDecision, build_graph_query_decision
from app.graph.repository import GraphRepository, normalize_graph_name, normalize_utc_datetime
from app.graph.types import (
    GraphAlias,
    GraphAliasWriteResult,
    GraphEntityWriteResult,
    GraphEvidenceBundle,
    GraphFallbackReason,
    GraphMemory,
    GraphPath,
    GraphRelationshipWriteResult,
    GraphResolution,
    GraphTraversalResult,
    RelationshipType,
    TraversalDirection,
    resolution_result,
)

_STAGE3_MAX_DEPTH = 2


@dataclass(frozen=True, slots=True)
class GraphEvidenceQueryResult:
    decision: GraphQueryDecision
    bundle: GraphEvidenceBundle | None
    fallback_reasons: tuple[GraphFallbackReason, ...] = ()
    cancelled: bool = False


class GraphService:
    """Thin internal facade for exact resolution and bounded graph traversal."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository: GraphRepository | None = None,
        read_session_factory: Any | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.repository = repository or GraphRepository(self.settings)
        # Graph reads may be given a separate session factory so a timeout or
        # database error cannot poison the transaction used by hybrid memory
        # retrieval.  The default remains the caller's session for backwards
        # compatibility with the low-level facade methods.
        self.read_session_factory = read_session_factory

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
        max_candidates: int | None = None,
    ) -> GraphResolution:
        normalized_name = normalize_graph_name(name, max_length=512)
        canonical = await self.repository.find_entities_by_canonical_name(
            session,
            user_id=user_id,
            normalized_name=normalized_name,
            entity_type=entity_type,
            limit=max_candidates,
        )
        if canonical:
            return resolution_result(canonical, matched_by="canonical")

        aliases = await self.repository.find_entities_by_alias(
            session,
            user_id=user_id,
            normalized_alias=normalized_name,
            entity_type=entity_type,
            limit=max_candidates,
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
        if not await self.repository.memory_enabled(session, user_id=user_id):
            raise GraphInvalidTraversal("memory disabled for graph reads")

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
        if not await self.repository.memory_enabled(session, user_id=user_id):
            return ()
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

    async def evidence_for_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        direction: TraversalDirection = "both",
        depth: int = 1,
        relationship_type: str | None = None,
        max_paths: int | None = None,
        cancellation_check: Any | None = None,
    ) -> GraphEvidenceBundle:
        """Traverse one seed and fetch every bounded active evidence memory."""

        _check_graph_cancellation(cancellation_check)
        traversal = await self.traverse(
            session,
            user_id=user_id,
            entity_id=entity_id,
            direction=direction,
            depth=depth,
            relationship_type=relationship_type,
            max_paths=max_paths,
        )
        _check_graph_cancellation(cancellation_check)
        source_memory_ids = tuple(
            dict.fromkeys(edge.source_memory_id for path in traversal.paths for edge in path.edges)
        )
        source_memories = await self.source_memories(
            session,
            user_id=user_id,
            source_memory_ids=source_memory_ids,
        )
        _check_graph_cancellation(cancellation_check)
        return GraphEvidenceBundle(
            paths=traversal.paths,
            source_memory_ids=source_memory_ids,
            source_memories=source_memories,
            truncated=traversal.truncated or len(source_memories) < len(source_memory_ids),
        )

    async def query_evidence(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        query: str,
        cancellation_check: Any | None = None,
        timeout_ms: int | None = None,
        read_session_factory: Any | None = None,
    ) -> GraphEvidenceQueryResult:
        """Route, resolve, traverse, and fetch evidence without RRF/prompt wiring."""

        decision = build_graph_query_decision(
            query,
            graph_rag_mode=self.settings.graph_rag_mode,
            memory_retrieval_mode=self.settings.memory_retrieval_mode,
            max_query_entities=self.settings.graph_max_query_entities,
            max_depth=min(_STAGE3_MAX_DEPTH, self.settings.graph_max_depth),
        )
        if not decision.should_query:
            return GraphEvidenceQueryResult(decision=decision, bundle=None)

        deadline_ms = self.settings.graph_rag_timeout_ms if timeout_ms is None else timeout_ms
        if not 1 <= deadline_ms <= 5_000:
            raise ValueError("graph query timeout must be between 1 and 5000 ms")

        factory = read_session_factory or self.read_session_factory
        if factory is not None:
            async with factory() as isolated_session:
                return await self._query_evidence_with_deadline(
                    isolated_session,
                    user_id=user_id,
                    decision=decision,
                    cancellation_check=cancellation_check,
                    deadline_ms=deadline_ms,
                )
        return await self._query_evidence_with_deadline(
            session,
            user_id=user_id,
            decision=decision,
            cancellation_check=cancellation_check,
            deadline_ms=deadline_ms,
        )

    async def _query_evidence_with_deadline(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        decision: GraphQueryDecision,
        cancellation_check: Any | None,
        deadline_ms: int,
    ) -> GraphEvidenceQueryResult:
        try:
            async with asyncio.timeout(deadline_ms / 1_000):
                return await self._query_evidence_inner(
                    session,
                    user_id=user_id,
                    decision=decision,
                    cancellation_check=cancellation_check,
                )
        except GraphQueryCancelled:
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=(GraphFallbackReason.GRAPH_CANCELLED,),
                cancelled=True,
            )
        except TimeoutError:
            await _safe_rollback(session)
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=(GraphFallbackReason.GRAPH_TIMEOUT,),
            )
        except GraphRepositoryError:
            await _safe_rollback(session)
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=(GraphFallbackReason.GRAPH_DATABASE_ERROR,),
            )
        except SQLAlchemyError:
            await _safe_rollback(session)
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=(GraphFallbackReason.GRAPH_DATABASE_ERROR,),
            )

    async def _query_evidence_inner(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        decision: GraphQueryDecision,
        cancellation_check: Any | None,
    ) -> GraphEvidenceQueryResult:
        seeds = []
        fallback_reasons: list[GraphFallbackReason] = []
        if not await self.repository.memory_enabled(session, user_id=user_id):
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=(GraphFallbackReason.MEMORY_DISABLED,),
            )
        for entity_name in decision.query_entities:
            _check_graph_cancellation(cancellation_check)
            resolution = await self.resolve_entity_exact(
                session,
                user_id=user_id,
                name=entity_name,
                max_candidates=self.settings.graph_max_query_entities,
            )
            if resolution.status == "not_found":
                fallback_reasons.append(GraphFallbackReason.ENTITY_NOT_FOUND)
            elif resolution.status == "ambiguous":
                fallback_reasons.append(GraphFallbackReason.ENTITY_AMBIGUOUS)
            seeds.extend(resolution.candidates)
        if not seeds:
            return GraphEvidenceQueryResult(
                decision=decision,
                bundle=None,
                fallback_reasons=tuple(dict.fromkeys(fallback_reasons)),
            )

        paths: list[GraphPath] = []
        path_keys: set[tuple[uuid.UUID, ...]] = set()
        max_paths = self.settings.graph_max_paths
        relationship_type = (
            decision.relationship_type.value
            if decision.depth == 1
            and decision.relationship_type not in {None, RelationshipType.RELATED_TO}
            else None
        )
        for seed in seeds:
            _check_graph_cancellation(cancellation_check)
            remaining = max_paths - len(paths)
            if remaining <= 0:
                break
            traversal = await self.traverse(
                session,
                user_id=user_id,
                entity_id=seed.id,
                direction="both",
                depth=decision.depth,
                relationship_type=relationship_type,
                max_paths=remaining,
            )
            if traversal.truncated:
                fallback_reasons.append(GraphFallbackReason.TRAVERSAL_TRUNCATED)
            for path in traversal.paths:
                key = tuple(edge.relationship_id for edge in path.edges)
                if key not in path_keys:
                    path_keys.add(key)
                    paths.append(path)
                    if len(paths) >= max_paths:
                        break

        _check_graph_cancellation(cancellation_check)
        source_memory_ids = tuple(
            dict.fromkeys(edge.source_memory_id for path in paths for edge in path.edges)
        )
        source_memories = await self.source_memories(
            session,
            user_id=user_id,
            source_memory_ids=source_memory_ids,
        )
        _check_graph_cancellation(cancellation_check)
        if len(source_memories) < len(source_memory_ids):
            fallback_reasons.append(GraphFallbackReason.EVIDENCE_INCOMPLETE)
        bundle = GraphEvidenceBundle(
            paths=tuple(paths),
            source_memory_ids=source_memory_ids,
            source_memories=source_memories,
            truncated=GraphFallbackReason.TRAVERSAL_TRUNCATED in fallback_reasons
            or GraphFallbackReason.EVIDENCE_INCOMPLETE in fallback_reasons,
        )
        return GraphEvidenceQueryResult(
            decision=decision,
            bundle=bundle,
            fallback_reasons=tuple(dict.fromkeys(fallback_reasons)),
        )


def _check_graph_cancellation(cancellation_check: Any | None) -> None:
    if cancellation_check is not None and cancellation_check():
        raise GraphQueryCancelled("graph query cancelled")


async def _safe_rollback(session: AsyncSession | None) -> None:
    """Rollback a failed graph read when the caller supplied a session."""

    if session is not None:
        await session.rollback()
