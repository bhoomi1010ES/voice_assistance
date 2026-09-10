from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.models import Device, Reminder
from app.services.push_delivery import PushDeliveryProvider, PushDeliveryResult
from app.services.recurrence import (
    RecurrenceResolutionError,
    next_occurrence,
    parse_recurrence_rule,
    recurrence_finished,
)


class ReminderWorker:
    """Claim and deliver reminders using PostgreSQL as the source of truth."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: PushDeliveryProvider,
        clock: Clock | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.clock = clock or SystemClock()
        self.worker_id = worker_id or f"reminder-worker-{uuid.uuid4()}"

    async def run_once(self, session: AsyncSession) -> bool:
        now = self.clock.now_utc()
        await self._recover_stale_claims(session, now)
        reminder = await self._claim_one(session, now)
        if reminder is None:
            return False
        outcome = await self._deliver(session, reminder)
        await self._finish(session, reminder.id, outcome, now)
        return True

    async def _recover_stale_claims(self, session: AsyncSession, now: datetime) -> None:
        await session.execute(
            update(Reminder)
            .where(
                Reminder.status == "processing",
                Reminder.lease_expires_at.is_not(None),
                Reminder.lease_expires_at <= now,
            )
            .values(
                status="retry_wait",
                next_attempt_at=now,
                failure_code="stale_processing_claim",
                failure_reason="worker lease expired; delivery claim recovered",
                locked_at=None,
                locked_by=None,
                lease_expires_at=None,
            )
        )
        await session.commit()

    async def _claim_one(self, session: AsyncSession, now: datetime) -> Reminder | None:
        eligible = (Reminder.status == "scheduled") | (Reminder.status == "retry_wait")
        due = (Reminder.trigger_at <= now) & (
            Reminder.next_attempt_at.is_(None) | (Reminder.next_attempt_at <= now)
        )
        reminder = await session.scalar(
            select(Reminder)
            .where(eligible, due)
            .order_by(Reminder.trigger_at, Reminder.created_at, Reminder.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if reminder is None:
            await session.commit()
            return None
        reminder.status = "processing"
        reminder.locked_at = now
        reminder.locked_by = self.worker_id
        reminder.lease_expires_at = now + timedelta(seconds=self.settings.reminder_lease_seconds)
        reminder.last_attempt_at = now
        reminder.attempt_count += 1
        reminder.next_attempt_at = None
        await session.commit()
        return reminder

    async def _deliver(self, session: AsyncSession, reminder: Reminder) -> PushDeliveryResult:
        devices = list(
            (
                await session.scalars(
                    select(Device).where(
                        Device.user_id == reminder.user_id,
                        Device.revoked_at.is_(None),
                        Device.push_token.is_not(None),
                        Device.push_token_revoked_at.is_(None),
                    )
                )
            ).all()
        )
        if not devices:
            return PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="no_active_push_device",
                failure_reason="no active push device is registered",
            )

        retryable_failure: PushDeliveryResult | None = None
        permanent_failure: PushDeliveryResult | None = None
        for device in devices:
            assert device.push_token is not None
            try:
                result = await self.provider.send(
                    token=device.push_token,
                    delivery_id=reminder.delivery_id,
                    title=reminder.title,
                    body=reminder.body,
                )
            except Exception:  # noqa: BLE001 - provider details never enter durable state
                result = PushDeliveryResult(
                    delivered=False,
                    retryable=True,
                    failure_code="push_provider_error",
                    failure_reason="push provider request failed",
                )
            if result.delivered:
                return result
            if result.retryable:
                retryable_failure = result
            else:
                permanent_failure = result
        return (
            retryable_failure
            or permanent_failure
            or PushDeliveryResult(
                delivered=False,
                failure_code="push_delivery_failed",
                failure_reason="push delivery failed",
            )
        )

    async def _finish(
        self,
        session: AsyncSession,
        reminder_id: uuid.UUID,
        outcome: PushDeliveryResult,
        now: datetime,
    ) -> None:
        reminder = await session.scalar(
            select(Reminder)
            .where(Reminder.id == reminder_id, Reminder.locked_by == self.worker_id)
            .with_for_update()
        )
        if reminder is None:
            await session.rollback()
            return
        if reminder.status == "cancelled":
            await session.commit()
            return
        reminder.locked_at = None
        reminder.locked_by = None
        reminder.lease_expires_at = None
        if outcome.delivered:
            reminder.sent_at = now
            reminder.failure_code = None
            reminder.failure_reason = None
            if reminder.recurrence_rule is None:
                reminder.status = "sent"
            else:
                try:
                    rule = parse_recurrence_rule(reminder.recurrence_rule)
                    next_at = next_occurrence(
                        reminder.trigger_at,
                        timezone_name=reminder.timezone,
                        rule=rule,
                    )
                except RecurrenceResolutionError as error:
                    reminder.status = "failed"
                    reminder.failure_code = "invalid_recurrence"
                    reminder.failure_reason = _safe_reason(str(error))
                    reminder.dead_lettered_at = now
                    reminder.next_attempt_at = None
                else:
                    reminder.occurrence_count += 1
                    if recurrence_finished(
                        next_at,
                        rule=rule,
                        occurrences_sent=reminder.occurrence_count,
                    ):
                        reminder.status = "sent"
                        reminder.next_attempt_at = None
                    else:
                        reminder.status = "scheduled"
                        reminder.trigger_at = next_at
                        reminder.next_attempt_at = next_at
                        reminder.delivery_id = f"{reminder.id}:{next_at.isoformat()}"
        elif outcome.retryable and reminder.attempt_count < self.settings.reminder_max_attempts:
            reminder.status = "retry_wait"
            reminder.next_attempt_at = now + self._backoff(reminder.attempt_count)
            reminder.failure_code = _safe_code(outcome.failure_code or "temporary_delivery_failure")
            reminder.failure_reason = _safe_reason(outcome.failure_reason)
        else:
            reminder.status = "failed"
            reminder.next_attempt_at = None
            reminder.failure_code = _safe_code(
                outcome.failure_code
                or ("dead_letter" if outcome.retryable else "permanent_delivery_failure")
            )
            reminder.failure_reason = _safe_reason(outcome.failure_reason)
            reminder.dead_lettered_at = now if outcome.retryable else None
        await session.commit()

    def _backoff(self, attempt_count: int) -> timedelta:
        seconds = min(
            self.settings.reminder_retry_backoff_max_seconds,
            self.settings.reminder_retry_backoff_base_seconds * (2 ** max(0, attempt_count - 1)),
        )
        return timedelta(seconds=seconds)


def _safe_code(value: str) -> str:
    return "".join(character for character in value if character.isalnum() or character in "_-")[
        :128
    ]


def _safe_reason(value: str | None) -> str:
    if not value:
        return "delivery failed"
    return " ".join(value.split())[:512]
