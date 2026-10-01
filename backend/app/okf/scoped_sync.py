"""Explicit, fail-closed synchronization for controlled OKF evaluation runs.

The normal :class:`OkfWorkerService` intentionally claims from the global queue.
This module is a separate execution path that never calls ``claim`` and only
leases exact, validated job IDs supplied by an operator/test harness.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import MemoryItem, OkfSyncJob, User

from .jobs import OkfSyncWorker

LOGGER = logging.getLogger("voice-assistance-backend")
_RUN_ID = re.compile(r"^OKF[0-9]+-\d{8}T\d{6}Z-[0-9a-f]{6}$")
_SCOPED_JOB_TYPES = frozenset({"upsert_memory", "remove_memory"})


@dataclass(frozen=True)
class ScopedSyncAudit:
    evaluation_run_id: str
    owner_id: uuid.UUID
    requested_job_ids: tuple[uuid.UUID, ...]
    processed_job_ids: tuple[uuid.UUID, ...]
    rejected_job_ids: tuple[uuid.UUID, ...]
    skipped_job_ids: tuple[uuid.UUID, ...]
    job_types: tuple[tuple[uuid.UUID, str], ...]
    failures: tuple[tuple[uuid.UUID, str], ...]
    started_at: datetime
    ended_at: datetime


class ScopedSyncValidationError(ValueError):
    """A requested scoped batch was invalid; no job in it was claimed."""

    def __init__(self, code: str, audit: ScopedSyncAudit) -> None:
        super().__init__(code)
        self.code = code
        self.audit = audit


async def process_only(
    session_factory: async_sessionmaker[AsyncSession],
    worker: OkfSyncWorker,
    *,
    evaluation_run_id: str,
    owner_id: uuid.UUID,
    approved_disposable_owner_ids: Iterable[uuid.UUID],
    job_ids: Iterable[uuid.UUID],
) -> ScopedSyncAudit:
    """Process only exact run-owned jobs, atomically validating the whole scope.

    The run ID is bound to each job through its persisted policy version and
    idempotency key (``okf-v1.<run-id>``), and to each source through the
    immutable-at-creation ``metadata.okf_run_id`` tag. Broad ``rebuild_user``
    and ``purge_user`` work is rejected because it is not memory/run scoped.
    """

    started_at = datetime.now(UTC)
    requested = tuple(job_ids)
    processed: list[uuid.UUID] = []
    rejected: list[uuid.UUID] = []
    skipped: list[uuid.UUID] = []
    types: list[tuple[uuid.UUID, str]] = []
    failures: list[tuple[uuid.UUID, str]] = []
    policy_version = f"okf-v1.{evaluation_run_id}"

    def audit() -> ScopedSyncAudit:
        return ScopedSyncAudit(
            evaluation_run_id=evaluation_run_id,
            owner_id=owner_id,
            requested_job_ids=requested,
            processed_job_ids=tuple(processed),
            rejected_job_ids=tuple(rejected),
            skipped_job_ids=tuple(skipped),
            job_types=tuple(types),
            failures=tuple(failures),
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )

    def emit(status: str, code: str | None = None) -> None:
        record = audit()
        LOGGER.info(
            "Run-scoped OKF synchronization execution",
            extra={
                "event": "okf.scoped_sync.execution",
                "execution_status": status,
                "failure_code": code,
                "evaluation_run_id": record.evaluation_run_id,
                "owner_id": str(record.owner_id),
                "requested_job_count": len(record.requested_job_ids),
                "requested_job_ids": [str(value) for value in record.requested_job_ids],
                "processed_job_ids": [str(value) for value in record.processed_job_ids],
                "rejected_job_ids": [str(value) for value in record.rejected_job_ids],
                "skipped_job_ids": [str(value) for value in record.skipped_job_ids],
                "job_types": [
                    {"job_id": str(job_id), "job_type": job_type}
                    for job_id, job_type in record.job_types
                ],
                "failures": [
                    {"job_id": str(job_id), "failure_code": failure}
                    for job_id, failure in record.failures
                ],
                "started_at": record.started_at.isoformat(),
                "ended_at": record.ended_at.isoformat(),
            },
        )

    def reject(code: str, rejected_ids: Iterable[uuid.UUID] = requested) -> None:
        rejected.extend(value for value in rejected_ids if value not in rejected)
        emit("rejected", code)
        raise ScopedSyncValidationError(code, audit())

    if not _RUN_ID.fullmatch(evaluation_run_id):
        reject("invalid_evaluation_run_id")
    if not requested:
        reject("empty_job_scope", ())
    if len(set(requested)) != len(requested):
        reject("duplicate_job_id")
    approved_ids = frozenset(approved_disposable_owner_ids)
    if not approved_ids or owner_id not in approved_ids:
        reject("owner_not_approved")
    if len(policy_version) > 64:
        reject("run_policy_version_too_long")

    now = datetime.now(UTC)
    lease_cutoff = now - timedelta(seconds=worker.settings.okf_job_lease_seconds)
    claim_times: dict[uuid.UUID, datetime] = {}

    try:
        async with session_factory() as session:
            async with session.begin():
                owner = await session.scalar(select(User).where(User.id == owner_id))
                if (
                    owner is None
                    or owner.status != "active"
                    or (owner.name or "").casefold() != "okf"
                ):
                    reject("owner_not_disposable")

                rows = list(
                    (
                        await session.scalars(
                            select(OkfSyncJob)
                            .where(OkfSyncJob.id.in_(requested))
                            .order_by(OkfSyncJob.id)
                            .with_for_update()
                        )
                    ).all()
                )
                rows_by_id = {row.id: row for row in rows}
                unknown_ids = tuple(job_id for job_id in requested if job_id not in rows_by_id)
                if unknown_ids:
                    reject("unknown_job_id", unknown_ids)

                eligible_memory_ids = {
                    row.memory_id
                    for row in rows
                    if row.memory_id is not None
                    and row.user_id == owner_id
                    and row.event_type in _SCOPED_JOB_TYPES
                    and row.policy_version == policy_version
                }
                memory_rows = list(
                    (
                        await session.scalars(
                            select(MemoryItem).where(
                                MemoryItem.user_id == owner_id,
                                MemoryItem.id.in_(eligible_memory_ids),
                            )
                        )
                    ).all()
                )
                memories = {memory.id: memory for memory in memory_rows}

                invalid: list[uuid.UUID] = []
                for job_id in requested:
                    row = rows_by_id[job_id]
                    types.append((row.id, row.event_type))
                    memory = memories.get(row.memory_id)
                    idempotency_scope = row.idempotency_key.rsplit(":", 1)[-1]
                    lease_expired = row.status == "running" and (
                        row.locked_at is None or row.locked_at < lease_cutoff
                    )
                    runnable_state = (
                        row.status in {"pending", "retry_wait"} and row.available_at <= now
                    ) or lease_expired
                    valid = (
                        row.user_id == owner_id
                        and row.policy_version == policy_version
                        and idempotency_scope == policy_version
                        and row.event_type in _SCOPED_JOB_TYPES
                        and row.memory_id is not None
                        and memory is not None
                        and (memory.metadata_json or {}).get("okf_run_id") == evaluation_run_id
                        and runnable_state
                        and row.attempts < worker.settings.okf_job_max_attempts
                    )
                    # Non-active sources still belong to their tagged run. Let
                    # the ordinary processor apply the privacy/generation
                    # barrier and cancel the upsert rather than syncing stale data.
                    if not valid:
                        invalid.append(job_id)

                if invalid:
                    reject("job_scope_or_state_mismatch", invalid)

                # Claim only after every requested ID has passed validation.
                for job_id in requested:
                    row = rows_by_id[job_id]
                    claimed_at = datetime.now(UTC)
                    row.status = "running"
                    row.attempts += 1
                    row.locked_at = claimed_at
                    row.updated_at = claimed_at
                    claim_times[job_id] = claimed_at
                await session.flush()

        # The normal worker's processor retains source, owner-generation,
        # lifecycle, idempotency, retry, and dead-letter behavior. It is called
        # only with IDs validated and leased above; claim() is never invoked.
        for job_id in requested:
            try:
                async with session_factory() as session:
                    async with session.begin():
                        row = await session.scalar(
                            select(OkfSyncJob).where(OkfSyncJob.id == job_id).with_for_update()
                        )
                        if (
                            row is None
                            or row.status != "running"
                            or row.locked_at != claim_times[job_id]
                        ):
                            skipped.append(job_id)
                            continue
                        async with session.begin_nested():
                            await worker.process(session, job_id=job_id)
                            await session.flush()
                        processed.append(job_id)
            except Exception as error:  # noqa: BLE001 - preserve the worker's retry policy
                async with session_factory() as session, session.begin():
                    row = await session.scalar(
                        select(OkfSyncJob).where(OkfSyncJob.id == job_id).with_for_update()
                    )
                    if row is not None and row.status == "running":
                        worker.fail(row, f"scoped_worker_{type(error).__name__.lower()}")
                failures.append((job_id, type(error).__name__))
                processed.append(job_id)
    except ScopedSyncValidationError:
        raise
    except Exception:
        rejected.extend(value for value in requested if value not in rejected)
        emit("failed", "scoped_execution_error")
        raise

    result = audit()
    emit("completed" if not failures and not skipped else "partial")
    return result
