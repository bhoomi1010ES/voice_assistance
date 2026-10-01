from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MemoryItem,
    OkfConcept,
    OkfConceptAssertion,
    OkfConceptSource,
    OkfConceptVersion,
    OkfSyncJob,
    User,
)

from .repository import OkfRepository, OkfSourceUnavailable


class OkfLifecycleService:
    """Synchronous privacy barrier shared by forget/exclusion/purge flows.

    The caller must invoke these methods inside the same transaction that
    performs the corresponding source-memory lifecycle change.
    """

    def __init__(
        self,
        repository: OkfRepository | None = None,
        *,
        policy_version: str = "okf-v1",
        sync_enabled: bool = True,
    ) -> None:
        self.repository = repository or OkfRepository()
        self.policy_version = policy_version
        self.sync_enabled = sync_enabled

    async def remove_memory_source(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """Remove source support and unsupported values before source deletion."""

        await self.repository.lock_source_for_lifecycle(
            session, user_id=user_id, memory_id=memory_id
        )
        await session.execute(
            update(OkfSyncJob)
            .where(
                OkfSyncJob.user_id == user_id,
                OkfSyncJob.memory_id == memory_id,
                OkfSyncJob.event_type == "upsert_memory",
                OkfSyncJob.status.in_(("pending", "retry_wait")),
            )
            .values(
                status="cancelled",
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        affected_version_ids = tuple(
            (
                await session.scalars(
                    select(OkfConceptSource.concept_version_id)
                    .where(
                        OkfConceptSource.user_id == user_id,
                        OkfConceptSource.memory_id == memory_id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        if not affected_version_ids:
            generation = await self.repository.bump_memory_generation(session, user_id=user_id)
            await self._enqueue_remove_job(
                session,
                user_id=user_id,
                memory_id=memory_id,
                generation=generation,
            )
            return ()

        versions = list(
            (
                await session.scalars(
                    select(OkfConceptVersion)
                    .where(
                        OkfConceptVersion.user_id == user_id,
                        OkfConceptVersion.id.in_(affected_version_ids),
                    )
                    .with_for_update()
                )
            ).all()
        )
        affected_assertion_ids = {version.assertion_id for version in versions}
        concept_ids = {version.concept_id for version in versions}
        await session.execute(
            delete(OkfConceptSource).where(
                OkfConceptSource.user_id == user_id,
                OkfConceptSource.memory_id == memory_id,
            )
        )

        for assertion_id in affected_assertion_ids:
            assertion = await session.scalar(
                select(OkfConceptAssertion)
                .where(
                    OkfConceptAssertion.id == assertion_id,
                    OkfConceptAssertion.user_id == user_id,
                )
                .with_for_update()
            )
            if assertion is None:
                continue
            assertion_versions = list(
                (
                    await session.scalars(
                        select(OkfConceptVersion).where(
                            OkfConceptVersion.user_id == user_id,
                            OkfConceptVersion.assertion_id == assertion_id,
                        )
                    )
                ).all()
            )
            current = next(
                (item for item in assertion_versions if item.version == assertion.current_version),
                None,
            )
            current_support_count = 0
            if current is not None:
                current_support_count = int(
                    await session.scalar(
                        select(func.count(OkfConceptSource.memory_id)).where(
                            OkfConceptSource.user_id == user_id,
                            OkfConceptSource.concept_version_id == current.id,
                            OkfConceptSource.evidence_role == "supports",
                        )
                    )
                    or 0
                )
            if current_support_count == 0:
                # Conservatively remove the whole claim when its projected
                # value no longer has direct supporting provenance.
                await session.delete(assertion)
            else:
                for version in assertion_versions:
                    if version.version == assertion.current_version:
                        continue
                    support_count = int(
                        await session.scalar(
                            select(func.count(OkfConceptSource.memory_id)).where(
                                OkfConceptSource.user_id == user_id,
                                OkfConceptSource.concept_version_id == version.id,
                                OkfConceptSource.evidence_role == "supports",
                            )
                        )
                        or 0
                    )
                    if support_count == 0:
                        await session.delete(version)

        await session.flush()
        for concept_id in concept_ids:
            remaining = int(
                await session.scalar(
                    select(func.count(OkfConceptAssertion.id)).where(
                        OkfConceptAssertion.user_id == user_id,
                        OkfConceptAssertion.concept_id == concept_id,
                    )
                )
                or 0
            )
            if remaining == 0:
                concept = await session.scalar(
                    select(OkfConcept)
                    .where(OkfConcept.id == concept_id, OkfConcept.user_id == user_id)
                    .with_for_update()
                )
                if concept is not None:
                    await session.delete(concept)
            else:
                concept = await session.scalar(
                    select(OkfConcept).where(
                        OkfConcept.id == concept_id, OkfConcept.user_id == user_id
                    )
                )
                if concept is not None:
                    await self._derive_status(session, concept)

        await session.flush()
        generation = await self.repository.bump_memory_generation(session, user_id=user_id)
        await self._enqueue_remove_job(
            session,
            user_id=user_id,
            memory_id=memory_id,
            generation=generation,
        )
        return tuple(sorted(affected_assertion_ids, key=str))

    async def exclude_session(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """Apply the source barrier to every memory from one owned session."""

        memory_ids = tuple(
            (
                await session.scalars(
                    select(MemoryItem.id)
                    .where(
                        MemoryItem.user_id == user_id,
                        MemoryItem.source_session_id == session_id,
                    )
                    .order_by(MemoryItem.id)
                    .with_for_update()
                )
            ).all()
        )
        owner_session = await session.scalar(select(User.id).where(User.id == user_id))
        if owner_session is None:
            raise OkfSourceUnavailable("OKF owner is unavailable")
        if not memory_ids:
            await self.repository.bump_memory_generation(session, user_id=user_id)
        affected: set[uuid.UUID] = set()
        for memory_id in memory_ids:
            affected.update(
                await self.remove_memory_source(session, user_id=user_id, memory_id=memory_id)
            )
        return tuple(sorted(affected, key=str))

    async def purge_user(self, session: AsyncSession, *, user_id: uuid.UUID) -> None:
        """Purge content-bearing OKF state and fence running work by generation."""

        # Keep the worker's lock order (source memory, then owner) to avoid a
        # deadlock with a claimed upsert while delete-all is fencing it.
        (
            await session.scalars(
                select(MemoryItem.id)
                .where(MemoryItem.user_id == user_id)
                .order_by(MemoryItem.id)
                .with_for_update()
            )
        ).all()
        owner = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if owner is None:
            raise OkfSourceUnavailable("OKF owner is unavailable")
        await session.execute(
            delete(OkfSyncJob).where(
                OkfSyncJob.user_id == user_id,
                OkfSyncJob.status != "running",
            )
        )
        # Removing the roots cascades assertions, versions, provenance, and
        # parent/child concepts. A running worker sees the bumped generation.
        await session.execute(delete(OkfConcept).where(OkfConcept.user_id == user_id))
        generation = await self.repository.bump_memory_generation(session, user_id=user_id)
        await self._enqueue_purge_job(session, user_id=user_id, generation=generation)

    async def _derive_status(self, session: AsyncSession, concept: OkfConcept) -> None:
        count = int(
            await session.scalar(
                select(func.count(OkfConceptAssertion.id)).where(
                    OkfConceptAssertion.user_id == concept.user_id,
                    OkfConceptAssertion.concept_id == concept.id,
                    OkfConceptAssertion.status == "active",
                )
            )
            or 0
        )
        concept.status = "retired" if count == 0 else "contested" if count > 1 else "active"
        concept.updated_at = datetime.now(UTC)

    async def _enqueue_remove_job(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        generation: int,
    ) -> None:
        if not self.sync_enabled:
            return
        key = f"remove_memory:{memory_id}:{generation}:{self.policy_version}"
        await session.execute(
            pg_insert(OkfSyncJob)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                memory_id=memory_id,
                event_type="remove_memory",
                idempotency_key=key,
                status="pending",
                attempts=0,
                available_at=datetime.now(UTC),
                policy_version=self.policy_version,
                memory_generation=generation,
            )
            .on_conflict_do_nothing(constraint="uq_okf_jobs_user_idempotency")
        )

    async def _enqueue_purge_job(
        self, session: AsyncSession, *, user_id: uuid.UUID, generation: int
    ) -> None:
        if not self.sync_enabled:
            return
        key = f"purge_user:{user_id}:{generation}:{self.policy_version}"
        await session.execute(
            pg_insert(OkfSyncJob)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                memory_id=None,
                event_type="purge_user",
                idempotency_key=key,
                status="pending",
                attempts=0,
                available_at=datetime.now(UTC),
                policy_version=self.policy_version,
                memory_generation=generation,
            )
            .on_conflict_do_nothing(constraint="uq_okf_jobs_user_idempotency")
        )
