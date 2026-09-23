from __future__ import annotations

import math
import time
import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.graph.errors import GraphOwnershipError
from app.graph.service import GraphService
from app.graph.types import GraphEntity, GraphIndexResult
from app.models import Entity, MemoryEntity, MemoryItem, User, VoiceSession

from .policy import GRAPH_INDEX_POLICY_VERSION, derive_relationship_spec


class GraphIndexingService:
    """Build optional graph rows from one already-validated memory record."""

    def __init__(self, settings: Settings, *, graph_service: GraphService | None = None) -> None:
        self.settings = settings
        self.graph_service = graph_service or GraphService(settings)

    async def index_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        policy_version: str | None,
    ) -> GraphIndexResult:
        started_ns = time.perf_counter_ns()
        timings: dict[str, float] = {}
        entities_created = 0
        entities_reused = 0

        def elapsed_ms(start_ns: int) -> float:
            return round((time.perf_counter_ns() - start_ns) / 1_000_000, 3)

        def result(
            status: Literal["indexed", "already_indexed", "skipped"],
            reason_code: str | None = None,
            *,
            edges_created: int = 0,
        ) -> GraphIndexResult:
            completed_timings = dict(timings)
            completed_timings["total_ms"] = elapsed_ms(started_ns)
            return GraphIndexResult(
                status=status,
                reason_code=reason_code,
                entities_created=entities_created,
                entities_reused=entities_reused,
                edges_created=edges_created,
                timings_ms=tuple(sorted(completed_timings.items())),
            )

        if not self.settings.graph_write_enabled:
            return result("skipped", "graph_write_disabled")
        if policy_version != GRAPH_INDEX_POLICY_VERSION:
            return result("skipped", "unsupported_policy_version")

        stage_ns = time.perf_counter_ns()
        memory = await session.scalar(
            select(MemoryItem)
            .where(MemoryItem.id == memory_id, MemoryItem.user_id == user_id)
            .with_for_update()
        )
        if memory is None:
            timings["memory_load_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "memory_missing")

        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.memory_enabled:
            timings["memory_load_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "memory_disabled")
        if memory.status != "active":
            timings["memory_load_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "memory_not_active")
        if memory.source_kind == "legacy":
            timings["memory_load_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "unsupported_memory_type")

        if memory.source_session_id is not None:
            voice_session = await session.scalar(
                select(VoiceSession).where(
                    VoiceSession.id == memory.source_session_id,
                    VoiceSession.user_id == user_id,
                )
            )
            if (
                voice_session
                and (voice_session.client_metadata or {}).get("memory_excluded") is True
            ):
                timings["memory_load_ms"] = elapsed_ms(stage_ns)
                return result("skipped", "session_excluded")
        timings["memory_load_ms"] = elapsed_ms(stage_ns)

        stage_ns = time.perf_counter_ns()
        spec, reason = derive_relationship_spec(
            memory_type=memory.memory_type,
            subject=memory.subject,
            predicate=memory.predicate,
            object_json=memory.object_json,
        )
        timings["eligibility_ms"] = elapsed_ms(stage_ns)
        if spec is None:
            return result("skipped", reason or "invalid_relationship")
        if (
            isinstance(memory.confidence, bool)
            or not isinstance(memory.confidence, (int, float))
            or not math.isfinite(memory.confidence)
            or not 0.0 <= memory.confidence <= 1.0
        ):
            return result("skipped", "invalid_relationship")

        stage_ns = time.perf_counter_ns()
        subject_links = []
        if spec.source_entity_type != "self":
            subject_links = (
                await session.scalars(
                    select(Entity)
                    .join(
                        MemoryEntity,
                        (MemoryEntity.entity_id == Entity.id)
                        & (MemoryEntity.user_id == Entity.user_id),
                    )
                    .where(
                        MemoryEntity.memory_id == memory.id,
                        MemoryEntity.user_id == user_id,
                        MemoryEntity.relation == memory.predicate,
                        Entity.user_id == user_id,
                    )
                    .order_by(Entity.id.asc())
                    .limit(2)
                )
            ).all()
        if len(subject_links) > 1:
            timings["source_entity_resolution_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "ambiguous_subject")

        source: GraphEntity | None
        if subject_links:
            source = await self.graph_service.repository.get_owned_entity(
                session, user_id=user_id, entity_id=subject_links[0].id
            )
            if source is None:
                timings["source_entity_resolution_ms"] = elapsed_ms(stage_ns)
                return result("skipped", "cross_user_source")
            entities_reused += 1
        else:
            source_resolution = await self.graph_service.resolve_entity_exact(
                session,
                user_id=user_id,
                name=spec.source_name,
                entity_type=spec.source_entity_type,
            )
            if source_resolution.status == "ambiguous":
                timings["source_entity_resolution_ms"] = elapsed_ms(stage_ns)
                return result("skipped", "ambiguous_subject")
            if source_resolution.status == "resolved":
                source = source_resolution.entity
                entities_reused += 1
            elif spec.source_entity_type is not None:
                created_source = await self.graph_service.get_or_create_entity(
                    session,
                    user_id=user_id,
                    entity_type=spec.source_entity_type,
                    canonical_name=spec.source_name,
                )
                source = created_source.entity
                entities_created += int(created_source.created)
                entities_reused += int(not created_source.created)
            else:
                timings["source_entity_resolution_ms"] = elapsed_ms(stage_ns)
                return result("skipped", "missing_subject")
        timings["source_entity_resolution_ms"] = elapsed_ms(stage_ns)

        stage_ns = time.perf_counter_ns()
        target_resolution = await self.graph_service.resolve_entity_exact(
            session,
            user_id=user_id,
            name=spec.target_name,
            entity_type=spec.target_entity_type,
        )
        if target_resolution.status == "ambiguous":
            timings["target_entity_resolution_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "ambiguous_target")
        if target_resolution.status == "resolved":
            target = target_resolution.entity
            entities_reused += 1
        else:
            created_target = await self.graph_service.get_or_create_entity(
                session,
                user_id=user_id,
                entity_type=spec.target_entity_type,
                canonical_name=spec.target_name,
            )
            target = created_target.entity
            entities_created += int(created_target.created)
            entities_reused += int(not created_target.created)
        timings["target_entity_resolution_ms"] = elapsed_ms(stage_ns)
        if source is None or target is None:
            return result("skipped", "invalid_relationship")
        if source.id == target.id:
            return result("skipped", "invalid_relationship")

        stage_ns = time.perf_counter_ns()
        try:
            relationship = await self.graph_service.insert_relationship(
                session,
                user_id=user_id,
                source_entity_id=source.id,
                relationship_type=spec.relationship_type,
                target_entity_id=target.id,
                source_memory_id=memory.id,
                confidence=float(memory.confidence),
                valid_from=memory.valid_from,
                valid_to=memory.valid_to,
                extraction_policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
        except GraphOwnershipError:
            timings["relationship_insert_ms"] = elapsed_ms(stage_ns)
            return result("skipped", "cross_user_source")
        timings["relationship_insert_ms"] = elapsed_ms(stage_ns)
        if relationship.created:
            return result("indexed", edges_created=1)
        return result("already_indexed")
