from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Reminder
from app.reminders.requeue import RequeueResult, requeue_failed_push_reminders


def _make_reminder(
    *,
    user_id: uuid.UUID,
    trigger_at: datetime,
    status: str = "failed",
    failure_code: str | None = "push_provider_unconfigured",
    failure_reason: str | None = "push provider unconfigured",
    locked_by: str | None = "worker-1",
    dead_lettered_at: datetime | None = None,
    attempt_count: int = 3,
) -> Reminder:
    return Reminder(
        id=uuid.uuid4(),
        user_id=user_id,
        title="Test Reminder",
        body="Reminder Body",
        delivery_id=f"delivery-{uuid.uuid4().hex[:8]}",
        trigger_at=trigger_at,
        timezone="UTC",
        status=status,
        failure_code=failure_code,
        failure_reason=failure_reason,
        locked_by=locked_by,
        locked_at=datetime.now(UTC),
        lease_expires_at=datetime.now(UTC),
        dead_lettered_at=dead_lettered_at,
        attempt_count=attempt_count,
        next_attempt_at=None,
    )


def _mock_session(reminders: list[Reminder]) -> AsyncMock:
    session = AsyncMock()

    async def mock_scalars(query):
        mock_result = MagicMock()
        # Simulate query filtering in memory
        filtered = list(reminders)
        mock_result.all.return_value = filtered
        return mock_result

    session.scalars = AsyncMock(side_effect=mock_scalars)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_requeue_future_failed_reminder_with_push_provider_unconfigured():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    user_id = uuid.uuid4()
    future_time = now + timedelta(hours=2)

    reminder = _make_reminder(
        user_id=user_id,
        trigger_at=future_time,
        status="failed",
        failure_code="push_provider_unconfigured",
        failure_reason="provider missing",
        dead_lettered_at=now - timedelta(minutes=10),
    )

    session = _mock_session([reminder])

    result: RequeueResult = await requeue_failed_push_reminders(session, now=now)

    assert result.scanned_count == 1
    assert result.requeued_count == 1
    assert result.skipped_past_due_count == 0
    assert result.requeued_reminder_ids == (reminder.id,)

    # Verify all expected fields were reset for scheduled execution
    assert reminder.status == "scheduled"
    assert reminder.next_attempt_at == future_time
    assert reminder.failure_code is None
    assert reminder.failure_reason is None
    assert reminder.dead_lettered_at is None
    assert reminder.locked_at is None
    assert reminder.locked_by is None
    assert reminder.lease_expires_at is None
    assert reminder.attempt_count == 0

    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_requeue_future_failed_reminder_with_no_active_push_device():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    user_id = uuid.uuid4()
    future_time = now + timedelta(days=1)

    reminder = _make_reminder(
        user_id=user_id,
        trigger_at=future_time,
        status="failed",
        failure_code="no_active_push_device",
        failure_reason="no registered push device",
    )

    session = _mock_session([reminder])

    result = await requeue_failed_push_reminders(session, now=now)

    assert result.requeued_count == 1
    assert reminder.status == "scheduled"
    assert reminder.next_attempt_at == future_time
    assert reminder.failure_code is None


@pytest.mark.asyncio
async def test_requeue_leaves_past_due_failed_reminders_unchanged():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    user_id = uuid.uuid4()
    past_time = now - timedelta(hours=1)

    past_due_reminder = _make_reminder(
        user_id=user_id,
        trigger_at=past_time,
        status="failed",
        failure_code="push_provider_unconfigured",
        failure_reason="original error",
        attempt_count=5,
    )

    session = _mock_session([past_due_reminder])

    result = await requeue_failed_push_reminders(session, now=now)

    assert result.scanned_count == 1
    assert result.requeued_count == 0
    assert result.skipped_past_due_count == 1
    assert result.requeued_reminder_ids == ()

    # Past-due reminder remains failed
    assert past_due_reminder.status == "failed"
    assert past_due_reminder.failure_code == "push_provider_unconfigured"
    assert past_due_reminder.attempt_count == 5

    # No commit needed if nothing was modified
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_requeue_dry_run_mode_does_not_mutate():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    user_id = uuid.uuid4()
    future_time = now + timedelta(hours=3)

    reminder = _make_reminder(
        user_id=user_id,
        trigger_at=future_time,
        status="failed",
        failure_code="push_provider_unconfigured",
        attempt_count=3,
    )

    session = _mock_session([reminder])

    result = await requeue_failed_push_reminders(session, now=now, dry_run=True)

    assert result.scanned_count == 1
    assert result.requeued_count == 1
    assert result.requeued_reminder_ids == (reminder.id,)

    # In dry-run mode, object state is NOT modified
    assert reminder.status == "failed"
    assert reminder.failure_code == "push_provider_unconfigured"
    assert reminder.attempt_count == 3
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_requeue_mixed_batch_filters_accurately():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    user_id = uuid.uuid4()

    # 1. Future failed with push_provider_unconfigured -> REQUEUE
    r1 = _make_reminder(
        user_id=user_id,
        trigger_at=now + timedelta(hours=1),
        status="failed",
        failure_code="push_provider_unconfigured",
    )
    # 2. Future failed with no_active_push_device -> REQUEUE
    r2 = _make_reminder(
        user_id=user_id,
        trigger_at=now + timedelta(hours=5),
        status="failed",
        failure_code="no_active_push_device",
    )
    # 3. Past-due failed -> SKIP
    r3 = _make_reminder(
        user_id=user_id,
        trigger_at=now - timedelta(minutes=1),
        status="failed",
        failure_code="push_provider_unconfigured",
    )

    session = _mock_session([r1, r2, r3])

    result = await requeue_failed_push_reminders(session, now=now)

    assert result.scanned_count == 3
    assert result.requeued_count == 2
    assert result.skipped_past_due_count == 1
    assert set(result.requeued_reminder_ids) == {r1.id, r2.id}

    assert r1.status == "scheduled"
    assert r2.status == "scheduled"
    assert r3.status == "failed"


def test_cli_script_help():
    backend_root = Path(__file__).resolve().parents[1]
    script_path = backend_root / "scripts" / "requeue_failed_push_reminders.py"
    res = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "--dry-run" in res.stdout
    assert "--user-id" in res.stdout
    assert "requeue_failed_push_reminders" in res.stdout
