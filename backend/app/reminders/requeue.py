"""Guarded requeueing for reminders that failed because push delivery was not ready.

This module provides a safe migration / administrative function to requeue reminders
that failed solely due to push infrastructure not being configured
('push_provider_unconfigured') or no active push device being registered
('no_active_push_device') at the time of delivery.

Safety guarantees:
1. Only rows with status == 'failed' are inspected.
2. Only rows with failure_code in ('push_provider_unconfigured', 'no_active_push_device').
3. Only future reminders (trigger_at > now) are requeued.
4. Past-due failed reminders (trigger_at <= now) remain failed.
5. Cancelled reminders are never resurrected.
6. Leases, locks, and failure fields are cleared so the worker can pick them up cleanly.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reminder

LOGGER = logging.getLogger("voice-assistance-backend")

REQUEUEABLE_FAILURE_CODES: tuple[str, ...] = (
    "push_provider_unconfigured",
    "no_active_push_device",
)


@dataclass(frozen=True)
class RequeueResult:
    """Summary of the requeue operation."""

    scanned_count: int
    requeued_count: int
    skipped_past_due_count: int
    requeued_reminder_ids: tuple[uuid.UUID, ...]


async def requeue_failed_push_reminders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    user_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> RequeueResult:
    """Requeue reminders that failed with push_provider_unconfigured or no_active_push_device.

    Args:
        session: Active database session.
        now: Reference timestamp for determining if trigger_at is in the future.
            Defaults to current UTC time.
        user_id: Optional user UUID filter. If None, applies across all users.
        dry_run: If True, identifies eligible rows without mutating or committing.

    Returns:
        RequeueResult containing counts and IDs of requeued reminders.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    query = select(Reminder).where(
        Reminder.status == "failed",
        Reminder.failure_code.in_(REQUEUEABLE_FAILURE_CODES),
    )
    if user_id is not None:
        query = query.where(Reminder.user_id == user_id)

    query = query.order_by(Reminder.trigger_at.asc(), Reminder.created_at.asc())
    reminders: Sequence[Reminder] = (await session.scalars(query)).all()

    requeued_ids: list[uuid.UUID] = []
    skipped_past_due = 0

    for reminder in reminders:
        # Strict guard: only future reminders are eligible
        if reminder.trigger_at <= now:
            skipped_past_due += 1
            continue

        requeued_ids.append(reminder.id)
        if not dry_run:
            reminder.status = "scheduled"
            reminder.next_attempt_at = reminder.trigger_at
            reminder.failure_code = None
            reminder.failure_reason = None
            reminder.dead_lettered_at = None
            reminder.locked_at = None
            reminder.locked_by = None
            reminder.lease_expires_at = None
            reminder.attempt_count = 0

    if not dry_run and requeued_ids:
        await session.commit()

    LOGGER.info(
        "requeued failed push reminders scanned=%d requeued=%d skipped_past_due=%d dry_run=%s",
        len(reminders),
        len(requeued_ids),
        skipped_past_due,
        dry_run,
    )

    return RequeueResult(
        scanned_count=len(reminders),
        requeued_count=len(requeued_ids),
        skipped_past_due_count=skipped_past_due,
        requeued_reminder_ids=tuple(requeued_ids),
    )
