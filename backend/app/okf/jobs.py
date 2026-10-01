from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryItem, OkfSyncJob, User


async def enqueue_memory_sync(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
    policy_version: str,
) -> bool:
    """Add content-free source work in the source-memory transaction."""
    owner = (
        await session.execute(
            select(User.memory_enabled, User.memory_generation).where(User.id == user_id)
        )
    ).one_or_none()
    if owner is None or not owner.memory_enabled:
        return False
    generation = int(owner.memory_generation)
    key = f"upsert_memory:{memory_id}:{generation}:{policy_version}"
    result = await session.execute(
        pg_insert(OkfSyncJob)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            memory_id=memory_id,
            event_type="upsert_memory",
            idempotency_key=key,
            status="pending",
            attempts=0,
            available_at=datetime.now(UTC),
            policy_version=policy_version,
            memory_generation=generation,
        )
        .on_conflict_do_nothing(constraint="uq_okf_jobs_user_idempotency")
        .returning(OkfSyncJob.id)
    )
    return result.scalar_one_or_none() is not None


async def enqueue_user_rebuild(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    policy_version: str,
) -> bool:
    owner = (
        await session.execute(
            select(User.memory_enabled, User.memory_generation).where(User.id == user_id)
        )
    ).one_or_none()
    if owner is None or not owner.memory_enabled:
        return False
    generation = int(owner.memory_generation)
    key = f"rebuild_user:{user_id}:{generation}:{policy_version}"
    result = await session.execute(
        pg_insert(OkfSyncJob)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            memory_id=None,
            event_type="rebuild_user",
            idempotency_key=key,
            status="pending",
            attempts=0,
            available_at=datetime.now(UTC),
            policy_version=policy_version,
            memory_generation=generation,
        )
        .on_conflict_do_nothing(constraint="uq_okf_jobs_user_idempotency")
        .returning(OkfSyncJob.id)
    )
    return result.scalar_one_or_none() is not None


class OkfSyncWorker:
    """Lease-based worker for durable, owner-scoped OKF synchronization."""

    def __init__(self, settings) -> None:
        self.settings = settings

    async def claim(self, session: AsyncSession) -> uuid.UUID | None:
        now = datetime.now(UTC)
        lease_cutoff = now - timedelta(seconds=self.settings.okf_job_lease_seconds)
        await session.execute(
            update(OkfSyncJob)
            .where(
                OkfSyncJob.status == "running",
                or_(OkfSyncJob.locked_at.is_(None), OkfSyncJob.locked_at < lease_cutoff),
                OkfSyncJob.attempts >= self.settings.okf_job_max_attempts,
            )
            .values(
                status="dead",
                locked_at=None,
                completed_at=now,
                updated_at=now,
                last_error_code="lease_expired_max_attempts",
            )
        )
        row = await session.scalar(
            select(OkfSyncJob)
            .where(
                OkfSyncJob.attempts < self.settings.okf_job_max_attempts,
                or_(
                    and_(
                        OkfSyncJob.status.in_(("pending", "retry_wait")),
                        OkfSyncJob.available_at <= now,
                    ),
                    and_(
                        OkfSyncJob.status == "running",
                        or_(OkfSyncJob.locked_at.is_(None), OkfSyncJob.locked_at < lease_cutoff),
                    ),
                ),
            )
            .order_by(OkfSyncJob.available_at, OkfSyncJob.created_at, OkfSyncJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "running"
        row.attempts += 1
        row.locked_at = now
        row.updated_at = now
        await session.flush()
        return row.id

    async def process(self, session: AsyncSession, *, job_id: uuid.UUID) -> None:
        from .repository import OkfRepository, OkfSourceUnavailable
        from .service import OkfKnowledgeService

        job = await session.scalar(
            select(OkfSyncJob).where(OkfSyncJob.id == job_id).with_for_update()
        )
        if job is None or job.status != "running":
            return
        now = datetime.now(UTC)
        if job.event_type in {"remove_memory", "purge_user"}:
            # Privacy writes already ran synchronously in the source transaction.
            self._complete(job, now)
            return

        repository = OkfRepository()
        if job.event_type == "upsert_memory":
            try:
                memory = await repository.lock_eligible_source(
                    session, user_id=job.user_id, memory_id=job.memory_id
                )
            except OkfSourceUnavailable:
                self._cancel(job, now, "source_unavailable")
                return
            owner = await session.scalar(
                select(User).where(User.id == job.user_id).with_for_update()
            )
            if owner is None or not owner.memory_enabled:
                self._cancel(job, now, "memory_disabled")
                return
            if owner.memory_generation != job.memory_generation:
                # Ordinary concurrent writes can advance the account fence. Rebase
                # only while this exact source is still eligible; privacy changes
                # make it fail the eligibility check above.
                if self.settings.okf_enabled and self.settings.okf_sync_enabled:
                    await enqueue_memory_sync(
                        session,
                        user_id=job.user_id,
                        memory_id=memory.id,
                        policy_version=job.policy_version,
                    )
                self._cancel(job, now, "generation_superseded")
                return
            await OkfKnowledgeService(settings=self.settings).sync_memory(
                session, user_id=job.user_id, memory_id=job.memory_id
            )
            self._complete(job, now)
            return

        if job.event_type == "rebuild_user":
            owner = await session.scalar(
                select(User).where(User.id == job.user_id)
            )
            if owner is None or not owner.memory_enabled:
                self._cancel(job, now, "memory_disabled")
                return
            if owner.memory_generation != job.memory_generation:
                from .jobs import enqueue_user_rebuild

                await enqueue_user_rebuild(
                    session, user_id=job.user_id, policy_version=job.policy_version
                )
                self._cancel(job, now, "generation_superseded")
                return
            memory_ids = tuple(
                (
                    await session.scalars(
                        select(MemoryItem.id)
                        .where(MemoryItem.user_id == job.user_id, MemoryItem.status == "active")
                        .order_by(MemoryItem.id)
                    )
                ).all()
            )
            for memory_id in memory_ids:
                await enqueue_memory_sync(
                    session,
                    user_id=job.user_id,
                    memory_id=memory_id,
                    policy_version=job.policy_version,
                )
            self._complete(job, now)
            return
        raise ValueError("unsupported_okf_job_event")

    def fail(self, job: OkfSyncJob, error_code: str) -> None:
        now = datetime.now(UTC)
        job.locked_at = None
        job.last_error_code = error_code[:128]
        job.updated_at = now
        if job.attempts >= self.settings.okf_job_max_attempts:
            job.status = "dead"
            job.completed_at = now
            return
        job.status = "retry_wait"
        job.completed_at = None
        delay = min(300, 2 ** min(job.attempts, 8))
        job.available_at = now + timedelta(seconds=delay)

    @staticmethod
    def _complete(job: OkfSyncJob, now: datetime) -> None:
        job.status = "completed"
        job.completed_at = now
        job.locked_at = None
        job.last_error_code = None
        job.updated_at = now

    @staticmethod
    def _cancel(job: OkfSyncJob, now: datetime, reason: str) -> None:
        job.status = "cancelled"
        job.completed_at = now
        job.locked_at = None
        job.last_error_code = reason
        job.updated_at = now
