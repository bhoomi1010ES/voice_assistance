from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.models import Device, Reminder
from app.reminders.worker import ReminderWorker
from app.services.push_delivery import PushDeliveryResult


class MockProvider:
    def __init__(self, responses: list[PushDeliveryResult] | None = None) -> None:
        self.responses = list(responses or [])
        self.sent_tokens: list[str] = []

    async def send(
        self,
        *,
        token: str,
        delivery_id: str,
        title: str,
        body: str | None,
    ) -> PushDeliveryResult:
        self.sent_tokens.append(token)
        if self.responses:
            return self.responses.pop(0)
        return PushDeliveryResult(delivered=True)


def _make_device(user_id: uuid.UUID, token: str) -> Device:
    device = Device(
        id=uuid.uuid4(),
        user_id=user_id,
        device_identifier=f"device-{uuid.uuid4().hex[:8]}",
        platform="android",
        device_kind="physical",
        push_token=token,
        push_token_revoked_at=None,
    )
    return device


def _make_reminder(user_id: uuid.UUID) -> Reminder:
    return Reminder(
        id=uuid.uuid4(),
        user_id=user_id,
        title="Test Reminder",
        body="Reminder Body",
        delivery_id=f"delivery-{uuid.uuid4().hex[:8]}",
        trigger_at=datetime.now(UTC),
        timezone="UTC",
        status="processing",
    )


def _mock_session(devices: list[Device]) -> AsyncMock:
    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = list(devices)
    session.scalars.return_value = scalars_mock
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_worker_single_device_success():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device = _make_device(user_id, "token-active-1")
    reminder = _make_reminder(user_id)
    session = _mock_session([device])

    provider = MockProvider([PushDeliveryResult(delivered=True)])
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    assert outcome.delivered is True
    assert device.push_token == "token-active-1"
    assert device.push_token_revoked_at is None
    assert provider.sent_tokens == ["token-active-1"]


@pytest.mark.asyncio
async def test_worker_unregistered_token_is_revoked_and_fails():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device = _make_device(user_id, "token-dead-1")
    reminder = _make_reminder(user_id)
    session = _mock_session([device])

    provider = MockProvider(
        [
            PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_token_unregistered",
                failure_reason="token unregistered",
            )
        ]
    )
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    assert outcome.delivered is False
    assert outcome.retryable is False
    assert outcome.failure_code == "push_token_unregistered"

    # Token cleared and marked revoked at current clock time
    assert device.push_token is None
    assert device.push_token_revoked_at == now
    assert session.flush.await_count == 1


@pytest.mark.asyncio
async def test_worker_revokes_invalid_device_and_delivers_to_second_device():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device1 = _make_device(user_id, "token-dead")
    device2 = _make_device(user_id, "token-healthy")
    reminder = _make_reminder(user_id)
    session = _mock_session([device1, device2])

    provider = MockProvider(
        [
            PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_token_unregistered",
            ),
            PushDeliveryResult(delivered=True),
        ]
    )
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    # Delivery succeeded overall because device 2 succeeded
    assert outcome.delivered is True
    # Device 1 was revoked
    assert device1.push_token is None
    assert device1.push_token_revoked_at == now
    # Device 2 remains active
    assert device2.push_token == "token-healthy"
    assert device2.push_token_revoked_at is None
    assert provider.sent_tokens == ["token-dead", "token-healthy"]


@pytest.mark.asyncio
async def test_worker_retryable_5xx_does_not_revoke_token():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device = _make_device(user_id, "token-transient-503")
    reminder = _make_reminder(user_id)
    session = _mock_session([device])

    provider = MockProvider(
        [
            PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="fcm_503",
                failure_reason="service unavailable",
            )
        ]
    )
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    assert outcome.delivered is False
    assert outcome.retryable is True
    assert outcome.failure_code == "fcm_503"

    # Token must NOT be revoked for transient errors
    assert device.push_token == "token-transient-503"
    assert device.push_token_revoked_at is None


@pytest.mark.asyncio
async def test_worker_all_devices_dead_reports_permanent_failure():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device1 = _make_device(user_id, "token-dead-1")
    device2 = _make_device(user_id, "token-dead-2")
    reminder = _make_reminder(user_id)
    session = _mock_session([device1, device2])

    provider = MockProvider(
        [
            PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_token_unregistered",
            ),
            PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_token_unregistered",
            ),
        ]
    )
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    assert outcome.delivered is False
    assert outcome.retryable is False
    assert outcome.failure_code == "push_token_unregistered"

    # Both devices are revoked
    assert device1.push_token is None
    assert device1.push_token_revoked_at == now
    assert device2.push_token is None
    assert device2.push_token_revoked_at == now
    assert session.flush.await_count == 2


@pytest.mark.asyncio
async def test_worker_permanent_provider_error_does_not_revoke_token():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    clock = FrozenClock(now)
    settings = Settings()
    user_id = uuid.uuid4()
    device = _make_device(user_id, "token-400")
    reminder = _make_reminder(user_id)
    session = _mock_session([device])

    provider = MockProvider(
        [
            PushDeliveryResult(
                delivered=False,
                retryable=False,
                failure_code="push_provider_error",
                failure_reason="permanent provider error 400",
            )
        ]
    )
    worker = ReminderWorker(settings, provider=provider, clock=clock)

    outcome = await worker._deliver(session, reminder)

    assert outcome.delivered is False
    assert outcome.retryable is False
    assert outcome.failure_code == "push_provider_error"

    # Token is NOT cleared for general provider errors
    assert device.push_token == "token-400"
    assert device.push_token_revoked_at is None
