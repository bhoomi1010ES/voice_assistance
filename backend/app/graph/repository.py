from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, delete, exists, func, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.graph.errors import (
    GraphInvalidTraversal,
    GraphOwnershipError,
    GraphRepositoryError,
    GraphWriteConflict,
)
from app.graph.types import (
    EntityType,
    GraphAlias,
    GraphEdge,
    GraphEntity,
    GraphEntityWriteResult,
    GraphMemory,
    GraphNeighbor,
    GraphRelationshipWriteResult,
    RelationshipType,
    entity_from_row,
)
from app.memory.types import normalize_memory_text
from app.models import Entity, EntityAlias, EntityRelationship, MemoryEntity, MemoryItem, User


def normalize_graph_name(value: str, *, max_length: int | None = None) -> str:
    """Use the Phase 6 memory convention for graph canonical names and aliases."""

    if not isinstance(value, str):
        raise ValueError("graph name must be text")
    normalized = normalize_memory_text(value)
    if not normalized:
        raise ValueError("graph name must not be blank")
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f"normalized graph name must be at most {max_length} characters")
    return normalized


def normalize_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def relationship_is_current(as_of: datetime):
    """Return the shared active/current validity condition for a supplied UTC time."""

    return and_(
        EntityRelationship.status == "active",
        or_(EntityRelationship.valid_from.is_(None), EntityRelationship.valid_from <= as_of),
        or_(EntityRelationship.valid_to.is_(None), EntityRelationship.valid_to >= as_of),
    )


class GraphRepository:
    """PostgreSQL-only persistence boundary for ownership-scoped graph rows."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    async def memory_enabled(self, session: AsyncSession, *, user_id: uuid.UUID) -> bool:
        """Return the current owner-scoped memory gate for graph reads."""

        enabled = await session.scalar(select(User.memory_enabled).where(User.id == user_id))
        return enabled is True

    async def cleanup_orphaned_entities(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        limit: int = 100,
    ) -> int:
        """Delete a bounded batch of graph entities with no retained evidence.

        Manual and canonical aliases are retained by policy. Memory-backed
        aliases and all graph edges may be removed once their source evidence
        is gone. The operation is owner-scoped and safe to replay; concurrent
        writers either retain the entity through a link or retry their insert
        after the delete transaction completes.
        """

        if not 1 <= limit <= 1_000:
            raise ValueError("orphan cleanup limit must be between 1 and 1000")
        retained_alias = exists(
            select(EntityAlias.id).where(
                EntityAlias.user_id == user_id,
                EntityAlias.entity_id == Entity.id,
                EntityAlias.source_kind.in_(("manual", "canonical")),
            )
        )
        memory_link = exists(
            select(MemoryEntity.memory_id).where(
                MemoryEntity.user_id == user_id,
                MemoryEntity.entity_id == Entity.id,
            )
        )
        active_edge = exists(
            select(EntityRelationship.id).where(
                EntityRelationship.user_id == user_id,
                EntityRelationship.status == "active",
                or_(
                    EntityRelationship.source_entity_id == Entity.id,
                    EntityRelationship.target_entity_id == Entity.id,
                ),
            )
        )
        candidate_ids = list(
            (
                await session.scalars(
                    select(Entity.id)
                    .where(
                        Entity.user_id == user_id,
                        ~retained_alias,
                        ~memory_link,
                        ~active_edge,
                    )
                    .order_by(Entity.id.asc())
                    .limit(limit)
                )
            ).all()
        )
        if not candidate_ids:
            return 0
        result = await session.execute(
            delete(Entity).where(Entity.user_id == user_id, Entity.id.in_(candidate_ids))
        )
        return int(result.rowcount or 0)

    async def get_owned_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
    ) -> GraphEntity | None:
        row = await session.scalar(
            select(Entity).where(Entity.id == entity_id, Entity.user_id == user_id)
        )
        return entity_from_row(row) if row is not None else None

    async def get_or_create_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_type: str,
        canonical_name: str,
    ) -> GraphEntityWriteResult:
        """Atomically reuse or create one entity under the owner/type/name key."""

        if not isinstance(entity_type, str) or not entity_type.strip():
            raise ValueError("entity_type must not be blank")
        normalized_entity_type = entity_type.strip().casefold()
        try:
            normalized_entity_type = EntityType(normalized_entity_type).value
        except ValueError:
            raise ValueError("unsupported graph entity type") from None
        if len(normalized_entity_type) > 64:
            raise ValueError("entity_type must be at most 64 characters")
        if len(canonical_name.strip()) > 512:
            raise ValueError("canonical_name must be at most 512 characters")
        normalized_name = normalize_graph_name(canonical_name, max_length=512)
        statement = (
            pg_insert(Entity)
            .values(
                user_id=user_id,
                entity_type=normalized_entity_type,
                canonical_name=canonical_name.strip(),
                normalized_name=normalized_name,
                metadata_json={},
            )
            .on_conflict_do_nothing(constraint="uq_entities_user_type_name")
            .returning(Entity.id)
        )
        try:
            async with session.begin_nested():
                inserted_id = await session.scalar(statement)
        except IntegrityError:
            raise GraphRepositoryError(
                "graph entity write violated a repository constraint"
            ) from None

        row = await session.scalar(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.entity_type == normalized_entity_type,
                Entity.normalized_name == normalized_name,
            )
        )
        if row is None:
            raise GraphRepositoryError("graph entity write could not be read back")
        return GraphEntityWriteResult(entity=entity_from_row(row), created=inserted_id is not None)

    async def find_entities_by_canonical_name(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        normalized_name: str,
        entity_type: str | None = None,
        limit: int | None = None,
    ) -> tuple[GraphEntity, ...]:
        query = select(Entity).where(
            Entity.user_id == user_id,
            Entity.normalized_name == normalized_name,
        )
        if entity_type is not None:
            query = query.where(Entity.entity_type == entity_type)
        ordered = query.order_by(Entity.canonical_name.asc(), Entity.id.asc())
        if limit is not None:
            if not 1 <= limit <= self.settings.graph_max_query_entities:
                raise ValueError("entity resolution limit exceeds the graph bound")
            ordered = ordered.limit(limit)
        rows = (await session.scalars(ordered)).all()
        return tuple(entity_from_row(row) for row in rows)

    async def find_entities_by_alias(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        normalized_alias: str,
        entity_type: str | None = None,
        limit: int | None = None,
    ) -> tuple[GraphEntity, ...]:
        query = (
            select(Entity)
            .join(
                EntityAlias,
                and_(
                    EntityAlias.entity_id == Entity.id,
                    EntityAlias.user_id == Entity.user_id,
                ),
            )
            .where(
                Entity.user_id == user_id,
                EntityAlias.user_id == user_id,
                EntityAlias.normalized_alias == normalized_alias,
            )
            .distinct()
        )
        if entity_type is not None:
            query = query.where(Entity.entity_type == entity_type)
        ordered = query.order_by(Entity.canonical_name.asc(), Entity.id.asc())
        if limit is not None:
            if not 1 <= limit <= self.settings.graph_max_query_entities:
                raise ValueError("entity resolution limit exceeds the graph bound")
            ordered = ordered.limit(limit)
        rows = (await session.scalars(ordered)).all()
        return tuple(entity_from_row(row) for row in rows)

    async def get_aliases_for_entity(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
    ) -> tuple[GraphAlias, ...]:
        rows = (
            await session.scalars(
                select(EntityAlias)
                .where(EntityAlias.user_id == user_id, EntityAlias.entity_id == entity_id)
                .order_by(EntityAlias.normalized_alias.asc(), EntityAlias.id.asc())
            )
        ).all()
        return tuple(_alias_dto(row) for row in rows)

    async def insert_alias(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_id: uuid.UUID,
        alias: str,
        source_kind: str,
        source_memory_id: uuid.UUID | None = None,
    ) -> tuple[GraphAlias, bool]:
        normalized_alias = normalize_graph_name(alias)
        if not isinstance(source_kind, str) or source_kind not in {
            "canonical",
            "memory",
            "manual",
            "legacy",
        }:
            raise ValueError("unsupported graph alias source kind")
        await self._require_owned_entities_and_memory(
            session,
            user_id=user_id,
            entity_ids=(entity_id,),
            memory_id=source_memory_id,
        )

        statement = (
            pg_insert(EntityAlias)
            .values(
                user_id=user_id,
                entity_id=entity_id,
                alias=alias.strip(),
                normalized_alias=normalized_alias,
                source_memory_id=source_memory_id,
                source_kind=source_kind,
            )
            .on_conflict_do_nothing(constraint="uq_entity_aliases_user_entity_normalized")
            .returning(EntityAlias.id)
        )
        try:
            async with session.begin_nested():
                inserted_id = await session.scalar(statement)
        except IntegrityError:
            raise GraphRepositoryError(
                "graph alias write violated a repository constraint"
            ) from None

        row = await session.scalar(
            select(EntityAlias).where(
                EntityAlias.user_id == user_id,
                EntityAlias.entity_id == entity_id,
                EntityAlias.normalized_alias == normalized_alias,
            )
        )
        if row is None:
            raise GraphRepositoryError("graph alias write could not be read back")
        return _alias_dto(row), inserted_id is not None

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
        if not isinstance(relationship_type, str) or not relationship_type.strip():
            raise ValueError("relationship_type must not be blank")
        if relationship_type != relationship_type.strip():
            raise ValueError("relationship_type must already be normalized")
        try:
            RelationshipType(relationship_type)
        except ValueError:
            raise ValueError("unsupported graph relationship type") from None
        if source_entity_id == target_entity_id:
            raise ValueError("graph relationships cannot connect an entity to itself")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("relationship confidence must be finite and between 0 and 1")
        if extraction_policy_version is not None and len(extraction_policy_version) > 64:
            raise ValueError("extraction_policy_version must be at most 64 characters")

        normalized_from = (
            normalize_utc_datetime(valid_from, field_name="valid_from")
            if valid_from is not None
            else None
        )
        normalized_to = (
            normalize_utc_datetime(valid_to, field_name="valid_to")
            if valid_to is not None
            else None
        )
        if normalized_from is not None and normalized_to is not None:
            if normalized_to < normalized_from:
                raise ValueError("valid_to must not precede valid_from")

        await self._require_owned_entities_and_memory(
            session,
            user_id=user_id,
            entity_ids=(source_entity_id, target_entity_id),
            memory_id=source_memory_id,
        )
        statement = (
            pg_insert(EntityRelationship)
            .values(
                user_id=user_id,
                source_entity_id=source_entity_id,
                relationship_type=relationship_type,
                target_entity_id=target_entity_id,
                source_memory_id=source_memory_id,
                confidence=confidence,
                valid_from=normalized_from,
                valid_to=normalized_to,
                status="active",
                extraction_policy_version=extraction_policy_version,
            )
            .on_conflict_do_nothing(constraint="uq_entity_relationships_evidence")
            .returning(EntityRelationship.id)
        )
        try:
            async with session.begin_nested():
                inserted_id = await session.scalar(statement)
        except IntegrityError:
            raise GraphRepositoryError(
                "graph relationship write violated a repository constraint"
            ) from None

        row = await session.scalar(
            select(EntityRelationship).where(
                EntityRelationship.user_id == user_id,
                EntityRelationship.source_entity_id == source_entity_id,
                EntityRelationship.relationship_type == relationship_type,
                EntityRelationship.target_entity_id == target_entity_id,
                EntityRelationship.source_memory_id == source_memory_id,
            )
        )
        if row is None:
            raise GraphRepositoryError("graph relationship write could not be read back")
        if inserted_id is None and not _relationship_replay_matches(
            row,
            confidence=confidence,
            valid_from=normalized_from,
            valid_to=normalized_to,
            extraction_policy_version=extraction_policy_version,
        ):
            raise GraphWriteConflict("relationship evidence replay has different attributes")
        edge = await self._edge_for_relationship(session, row)
        return GraphRelationshipWriteResult(edge=edge, created=inserted_id is not None)

    async def list_neighbors(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_ids: Sequence[uuid.UUID],
        direction: str,
        as_of: datetime,
        relationship_type: str | None = None,
        max_edges_per_entity: int | None = None,
    ) -> tuple[GraphNeighbor, ...]:
        origins = tuple(dict.fromkeys(entity_ids))
        if not origins:
            return ()
        if direction not in {"outgoing", "incoming", "both"}:
            raise GraphInvalidTraversal("direction must be outgoing, incoming, or both")
        if relationship_type is not None and not relationship_type.strip():
            raise GraphInvalidTraversal("relationship_type must not be blank")
        limit = (
            self.settings.graph_max_edges_per_entity
            if max_edges_per_entity is None
            else max_edges_per_entity
        )
        if not 1 <= limit <= self.settings.graph_max_edges_per_entity:
            raise GraphInvalidTraversal("edge limit must be within the configured graph bound")
        current_at = normalize_utc_datetime(as_of, field_name="as_of")
        statement = self._build_neighbor_statement(
            user_id=user_id,
            entity_ids=origins,
            direction=direction,
            as_of=current_at,
            relationship_type=relationship_type,
            max_edges_per_entity=limit,
        )
        rows = (await session.execute(statement)).mappings().all()
        return tuple(
            GraphNeighbor(
                origin_entity_id=row["origin_entity_id"],
                neighbor_entity_id=row["neighbor_entity_id"],
                edge=_edge_dto(row),
                truncated=row["edge_count"] > limit,
            )
            for row in rows
        )

    def _build_neighbor_statement(
        self,
        *,
        user_id: uuid.UUID,
        entity_ids: Sequence[uuid.UUID],
        direction: str,
        as_of: datetime,
        relationship_type: str | None,
        max_edges_per_entity: int,
    ) -> Select:
        """Build the structurally bounded active-evidence neighbor SQL."""

        source_entity = aliased(Entity, name="graph_source_entity")
        target_entity = aliased(Entity, name="graph_target_entity")

        def oriented_select(*, outgoing: bool) -> Select:
            origin_column = (
                EntityRelationship.source_entity_id
                if outgoing
                else EntityRelationship.target_entity_id
            )
            neighbor_column = (
                EntityRelationship.target_entity_id
                if outgoing
                else EntityRelationship.source_entity_id
            )
            origin_filter = (
                EntityRelationship.source_entity_id.in_(entity_ids)
                if outgoing
                else EntityRelationship.target_entity_id.in_(entity_ids)
            )
            query = (
                select(
                    origin_column.label("origin_entity_id"),
                    neighbor_column.label("neighbor_entity_id"),
                    EntityRelationship.id.label("relationship_id"),
                    EntityRelationship.user_id.label("user_id"),
                    EntityRelationship.source_entity_id.label("source_entity_id"),
                    source_entity.canonical_name.label("source_name"),
                    EntityRelationship.relationship_type.label("relationship_type"),
                    EntityRelationship.target_entity_id.label("target_entity_id"),
                    target_entity.canonical_name.label("target_name"),
                    EntityRelationship.source_memory_id.label("source_memory_id"),
                    EntityRelationship.confidence.label("confidence"),
                    EntityRelationship.valid_from.label("valid_from"),
                    EntityRelationship.valid_to.label("valid_to"),
                    EntityRelationship.status.label("status"),
                    EntityRelationship.created_at.label("created_at"),
                )
                .select_from(EntityRelationship)
                .join(
                    source_entity,
                    and_(
                        source_entity.id == EntityRelationship.source_entity_id,
                        source_entity.user_id == EntityRelationship.user_id,
                    ),
                )
                .join(
                    target_entity,
                    and_(
                        target_entity.id == EntityRelationship.target_entity_id,
                        target_entity.user_id == EntityRelationship.user_id,
                    ),
                )
                .join(
                    MemoryItem,
                    and_(
                        MemoryItem.id == EntityRelationship.source_memory_id,
                        MemoryItem.user_id == EntityRelationship.user_id,
                    ),
                )
                .where(
                    EntityRelationship.user_id == user_id,
                    source_entity.user_id == user_id,
                    target_entity.user_id == user_id,
                    MemoryItem.user_id == user_id,
                    MemoryItem.status == "active",
                    relationship_is_current(as_of),
                    origin_filter,
                )
            )
            if relationship_type is not None:
                query = query.where(EntityRelationship.relationship_type == relationship_type)
            return query

        if direction == "outgoing":
            oriented = oriented_select(outgoing=True).subquery("graph_oriented_edges")
        elif direction == "incoming":
            oriented = oriented_select(outgoing=False).subquery("graph_oriented_edges")
        elif direction == "both":
            oriented = union_all(
                oriented_select(outgoing=True),
                oriented_select(outgoing=False),
            ).subquery("graph_oriented_edges")
        else:
            raise GraphInvalidTraversal("direction must be outgoing, incoming, or both")

        edge_order = (
            oriented.c.confidence.desc(),
            oriented.c.created_at.desc(),
            oriented.c.relationship_id.asc(),
            oriented.c.neighbor_entity_id.asc(),
        )
        ranked = select(
            oriented,
            func.row_number()
            .over(partition_by=oriented.c.origin_entity_id, order_by=edge_order)
            .label("edge_rank"),
            func.count().over(partition_by=oriented.c.origin_entity_id).label("edge_count"),
        ).subquery("bounded_graph_edges")
        return (
            select(ranked)
            .where(ranked.c.edge_rank <= max_edges_per_entity)
            .order_by(ranked.c.origin_entity_id.asc(), ranked.c.edge_rank.asc())
        )

    async def get_source_memories(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        source_memory_ids: Sequence[uuid.UUID],
        limit: int | None = None,
    ) -> tuple[GraphMemory, ...]:
        memory_ids = tuple(dict.fromkeys(source_memory_ids))
        if not memory_ids:
            return ()
        memory_limit = self.settings.graph_max_memories if limit is None else limit
        if not 1 <= memory_limit <= self.settings.graph_max_memories:
            raise GraphInvalidTraversal("memory limit must be within the configured graph bound")
        rows = (
            await session.scalars(
                select(MemoryItem)
                .where(
                    MemoryItem.user_id == user_id,
                    MemoryItem.id.in_(memory_ids),
                    MemoryItem.status == "active",
                )
                .order_by(MemoryItem.created_at.desc(), MemoryItem.id.asc())
                .limit(memory_limit)
            )
        ).all()
        return tuple(
            GraphMemory(
                id=row.id,
                user_id=row.user_id,
                content=row.content,
                memory_type=row.memory_type,
                subject=row.subject,
                predicate=row.predicate,
                created_at=row.created_at,
            )
            for row in rows
        )

    async def _require_owned_entities_and_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        entity_ids: Sequence[uuid.UUID],
        memory_id: uuid.UUID | None,
    ) -> None:
        expected_entity_ids = set(entity_ids)
        owned_entity_ids = set(
            (
                await session.scalars(
                    select(Entity.id).where(
                        Entity.user_id == user_id,
                        Entity.id.in_(tuple(expected_entity_ids)),
                    )
                )
            ).all()
        )
        if owned_entity_ids != expected_entity_ids:
            raise GraphOwnershipError("one or more graph entities are not owned by user_id")
        if memory_id is not None:
            owned_memory = await session.scalar(
                select(MemoryItem.id).where(
                    MemoryItem.id == memory_id,
                    MemoryItem.user_id == user_id,
                )
            )
            if owned_memory is None:
                raise GraphOwnershipError("graph source memory is not owned by user_id")

    async def _edge_for_relationship(
        self,
        session: AsyncSession,
        row: EntityRelationship,
    ) -> GraphEdge:
        source_name = await session.scalar(
            select(Entity.canonical_name).where(
                Entity.id == row.source_entity_id,
                Entity.user_id == row.user_id,
            )
        )
        target_name = await session.scalar(
            select(Entity.canonical_name).where(
                Entity.id == row.target_entity_id,
                Entity.user_id == row.user_id,
            )
        )
        if source_name is None or target_name is None:
            raise GraphRepositoryError("graph relationship endpoints are unavailable")
        return GraphEdge(
            relationship_id=row.id,
            user_id=row.user_id,
            source_entity_id=row.source_entity_id,
            source_name=source_name,
            relationship_type=row.relationship_type,
            target_entity_id=row.target_entity_id,
            target_name=target_name,
            source_memory_id=row.source_memory_id,
            confidence=float(row.confidence),
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            status=row.status,
            created_at=row.created_at,
        )


def _alias_dto(row: EntityAlias) -> GraphAlias:
    return GraphAlias(
        id=row.id,
        user_id=row.user_id,
        entity_id=row.entity_id,
        alias=row.alias,
        normalized_alias=row.normalized_alias,
        source_memory_id=row.source_memory_id,
        source_kind=row.source_kind,
        created_at=row.created_at,
    )


def _edge_dto(row) -> GraphEdge:
    return GraphEdge(
        relationship_id=row["relationship_id"],
        user_id=row["user_id"],
        source_entity_id=row["source_entity_id"],
        source_name=row["source_name"],
        relationship_type=row["relationship_type"],
        target_entity_id=row["target_entity_id"],
        target_name=row["target_name"],
        source_memory_id=row["source_memory_id"],
        confidence=float(row["confidence"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _relationship_replay_matches(
    row: EntityRelationship,
    *,
    confidence: float,
    valid_from: datetime | None,
    valid_to: datetime | None,
    extraction_policy_version: str | None,
) -> bool:
    return (
        math.isclose(float(row.confidence), confidence, rel_tol=1e-6, abs_tol=1e-6)
        and row.valid_from == valid_from
        and row.valid_to == valid_to
        and row.extraction_policy_version == extraction_policy_version
    )
