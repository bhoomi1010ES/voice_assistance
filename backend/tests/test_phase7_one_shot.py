from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.reminder_tools import register_reminder_tools
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from app.llm.types import LLMToolCall
from app.models import Device, Reminder, User
from app.reminders.worker import ReminderWorker
from app.services.push_delivery import FakePushDeliveryProvider, PushDeliveryResult
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at
from app.services.tool_idempotency import PostgresToolIdempotencyStore

pytestmark = pytest.mark.integration


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


async def _db_factory(settings: Settings):
    if not settings.database_url:
        pytest.skip("DATABASE_URL is not configured")
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(select(1))
    except Exception as error:  # noqa: BLE001 - integration prerequisite
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {error}")
    return engine, factory


async def _create_user(factory, *, label: str) -> tuple[User, Device]:
    async with factory() as session:
        user = User(
            email=f"phase7-{label}-{uuid.uuid4().hex}@example.test",
            password_hash="not-a-real-password-hash",
            timezone="America/New_York",
        )
        session.add(user)
        await session.flush()
        device = Device(
            user_id=user.id,
            device_identifier=f"phase7-device-{uuid.uuid4().hex}",
            platform="android",
            push_token=f"phase7-token-{uuid.uuid4().hex}",
        )
        session.add(device)
        await session.commit()
        return user, device


async def _cleanup(factory, user_id: uuid.UUID) -> None:
    async with factory() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def _create_due_reminder(factory, user_id: uuid.UUID, *, title: str) -> uuid.UUID:
    async with factory() as session:
        reminder = Reminder(
            user_id=user_id,
            title=title,
            body="phase7 acceptance",
            trigger_at=datetime.now(UTC) - timedelta(seconds=1),
            timezone="America/New_York",
            status="scheduled",
            delivery_channel="push",
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(reminder)
        await session.commit()
        return reminder.id


@pytest_asyncio.fixture
async def phase7_db():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run Phase 7 PostgreSQL acceptance.")
    settings = _settings()
    engine, factory = await _db_factory(settings)
    created: list[uuid.UUID] = []
    try:
        yield factory, created
    finally:
        for user_id in created:
            await _cleanup(factory, user_id)
        await engine.dispose()


def test_timezone_rejects_ambiguous_and_nonexistent_local_times() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="ambiguous"):
        resolve_task_due_at(
            due_at=None,
            due_expression="November 1, 2026 at 1:30 AM",
            source_transcript=None,
            now_utc=datetime(2026, 10, 30, 12, tzinfo=UTC),
            timezone_name="America/New_York",
        )
    with pytest.raises(TaskDueDateResolutionError, match="nonexistent"):
        resolve_task_due_at(
            due_at=None,
            due_expression="March 8, 2026 at 2:30 AM",
            source_transcript=None,
            now_utc=datetime(2026, 3, 1, 12, tzinfo=UTC),
            timezone_name="America/New_York",
        )


def test_frozen_clock_resolves_time_only_without_hard_coded_date() -> None:
    resolved = resolve_task_due_at(
        due_at=None,
        due_expression="9 AM",
        source_transcript=None,
        now_utc=FrozenClock(datetime(2026, 9, 3, 12, tzinfo=UTC)).now_utc(),
        timezone_name="Asia/Kolkata",
    )
    assert resolved == datetime(2026, 9, 4, 3, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_one_shot_worker_delivery_and_restart_persistence(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="restart")
    created.append(user.id)
    reminder_id = await _create_due_reminder(factory, user.id, title="restart reminder")

    provider = FakePushDeliveryProvider()
    settings = _settings(reminder_max_attempts=3)
    first_worker = ReminderWorker(settings, provider=provider, worker_id="worker-a")
    async with factory() as session:
        assert await first_worker.run_once(session) is True
    async with factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        assert reminder.status == "sent"
        assert reminder.attempt_count == 1
    restarted_worker = ReminderWorker(settings, provider=provider, worker_id="worker-b")
    async with factory() as session:
        assert await restarted_worker.run_once(session) is False
    assert len(provider.deliveries) == 1


@pytest.mark.asyncio
async def test_two_workers_have_one_active_claim_and_one_delivery(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="concurrency")
    created.append(user.id)
    reminder_id = await _create_due_reminder(factory, user.id, title="concurrent reminder")
    provider = FakePushDeliveryProvider()
    settings = _settings()

    async with factory() as session_a, factory() as session_b:
        results = await asyncio.gather(
            ReminderWorker(settings, provider=provider, worker_id="worker-a").run_once(session_a),
            ReminderWorker(settings, provider=provider, worker_id="worker-b").run_once(session_b),
        )
    assert sum(results) == 1
    assert len(provider.deliveries) == 1
    async with factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None and reminder.status == "sent"


@pytest.mark.asyncio
async def test_stale_claim_recovery_and_bounded_retry_dead_letter(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="retry")
    created.append(user.id)
    retry_id = await _create_due_reminder(factory, user.id, title="retry reminder")
    provider = FakePushDeliveryProvider(
        outcomes=[
            PushDeliveryResult(
                delivered=False,
                retryable=True,
                failure_code="temporary_provider",
                failure_reason="temporary failure",
            ),
        ]
    )
    settings = _settings(reminder_max_attempts=2, reminder_retry_backoff_base_seconds=0.01)
    worker = ReminderWorker(settings, provider=provider, worker_id="worker-retry")
    async with factory() as session:
        reminder = await session.get(Reminder, retry_id)
        assert reminder is not None
        reminder.status = "processing"
        reminder.locked_by = "crashed-worker"
        reminder.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await worker.run_once(session) is True
    async with factory() as session:
        reminder = await session.get(Reminder, retry_id)
        assert reminder is not None
        assert reminder.status == "retry_wait"
        reminder.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    async with factory() as session:
        assert await worker.run_once(session) is True
    async with factory() as session:
        reminder = await session.get(Reminder, retry_id)
        assert reminder is not None and reminder.status == "sent"

    dead_id = await _create_due_reminder(factory, user.id, title="dead reminder")
    dead_provider = FakePushDeliveryProvider(
        outcomes=[
            PushDeliveryResult(delivered=False, retryable=True, failure_code="temporary"),
            PushDeliveryResult(delivered=False, retryable=True, failure_code="temporary"),
        ]
    )
    dead_worker = ReminderWorker(
        _settings(reminder_max_attempts=2, reminder_retry_backoff_base_seconds=0.01),
        provider=dead_provider,
        worker_id="worker-dead",
    )
    async with factory() as session:
        await dead_worker.run_once(session)
        reminder = await session.get(Reminder, dead_id)
        assert reminder is not None
        reminder.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        await dead_worker.run_once(session)
    async with factory() as session:
        reminder = await session.get(Reminder, dead_id)
        assert reminder is not None
        assert reminder.status == "failed"
        assert reminder.dead_lettered_at is not None
        assert reminder.failure_reason is not None


@pytest.mark.asyncio
async def test_recurrence_occurrences_are_durable_and_replay_safe(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="recurrence")
    created.append(user.id)
    async with factory() as session:
        reminder = Reminder(
            user_id=user.id,
            title="daily recurring reminder",
            body="phase7 recurrence",
            trigger_at=datetime.now(UTC) - timedelta(seconds=1),
            timezone="America/New_York",
            recurrence_rule="FREQ=DAILY;COUNT=2",
            status="scheduled",
            delivery_channel="push",
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(reminder)
        await session.commit()
        reminder_id = reminder.id

    provider = FakePushDeliveryProvider()
    settings = _settings()
    first_worker = ReminderWorker(settings, provider=provider, worker_id="worker-rec-a")
    async with factory() as session:
        assert await first_worker.run_once(session) is True
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        assert reminder.status == "scheduled"
        assert reminder.occurrence_count == 1
        reminder.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        reminder.trigger_at = reminder.next_attempt_at
        await session.commit()

    restarted_worker = ReminderWorker(settings, provider=provider, worker_id="worker-rec-b")
    async with factory() as session:
        assert await restarted_worker.run_once(session) is True
    async with factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        assert reminder.status == "sent"
        assert reminder.occurrence_count == 2
    assert len(provider.deliveries) == 2


@pytest.mark.asyncio
async def test_cancelled_reminder_is_not_delivered(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="cancel")
    created.append(user.id)
    reminder_id = await _create_due_reminder(factory, user.id, title="cancelled reminder")
    async with factory() as session:
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        reminder.status = "cancelled"
        reminder.next_attempt_at = None
        await session.commit()
    provider = FakePushDeliveryProvider()
    async with factory() as session:
        assert await ReminderWorker(_settings(), provider=provider).run_once(session) is False
    assert provider.deliveries == []


@pytest.mark.asyncio
async def test_reminder_tool_cannot_execute_against_another_user(phase7_db) -> None:
    factory, created = phase7_db
    user_a, _device_a = await _create_user(factory, label="tool-owner-a")
    user_b, _device_b = await _create_user(factory, label="tool-owner-b")
    created.extend([user_a.id, user_b.id])
    async with factory() as session:
        reminder = Reminder(
            user_id=user_b.id,
            title="User B private reminder",
            trigger_at=datetime.now(UTC) + timedelta(days=1),
            timezone="America/New_York",
            status="scheduled",
            delivery_channel="push",
            next_attempt_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(reminder)
        await session.commit()
        reminder_id = reminder.id

        registry = ToolRegistry()
        register_reminder_tools(registry)
        executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
        result = await executor.execute(
            LLMToolCall(
                tool_call_id="phase7-cross-user-reminder-tool",
                name="update_reminder",
                arguments={"reminder_id": str(reminder_id), "title": "forged update"},
            ),
            context=ToolExecutionContext(
                user_id=user_a.id,
                session_id=uuid.uuid4(),
                turn_id=uuid.uuid4(),
                response_id=uuid.uuid4(),
                scopes=frozenset({"reminders:write"}),
                confirmed_tool_call_ids=frozenset({"phase7-cross-user-reminder-tool"}),
                db=session,
                clock=FrozenClock(datetime(2026, 9, 3, 18, tzinfo=UTC)),
                user_timezone="America/New_York",
            ),
        )
        assert result.success is False
        reminder = await session.get(Reminder, reminder_id)
        assert reminder is not None
        assert reminder.title == "User B private reminder"


def _reminder_call() -> LLMToolCall:
    return LLMToolCall(
        tool_call_id="phase7-create-reminder-call",
        name="create_reminder",
        arguments={"title": "Call Rahul", "trigger_expression": "tomorrow at 9 AM"},
    )


def _reminder_context(*, user_id: uuid.UUID, db, turn_id: uuid.UUID) -> ToolExecutionContext:
    return ToolExecutionContext(
        user_id=user_id,
        session_id=uuid.uuid4(),
        turn_id=turn_id,
        response_id=uuid.uuid4(),
        scopes=frozenset({"reminders:write"}),
        confirmed_tool_call_ids=frozenset({"phase7-create-reminder-call"}),
        db=db,
        clock=FrozenClock(datetime(2026, 9, 3, 18, tzinfo=UTC)),
        user_timezone="Asia/Kolkata",
    )


@pytest.mark.asyncio
async def test_same_confirmed_create_reminder_call_is_idempotent(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="idempotency")
    created.append(user.id)
    registry = ToolRegistry()
    register_reminder_tools(registry)
    call = _reminder_call()
    turn_id = uuid.uuid4()
    async with factory() as session:
        executor = ToolExecutor(registry, idempotency_store=PostgresToolIdempotencyStore(session))
        context = _reminder_context(user_id=user.id, db=session, turn_id=turn_id)
        first = await executor.execute(call, context=context)
        await session.commit()
        replay = await executor.execute(call, context=context)
        await session.commit()
        count = len(
            list((await session.scalars(select(Reminder).where(Reminder.user_id == user.id))).all())
        )
    assert first.success and first.executed
    assert replay.success and replay.replayed and not replay.executed
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_confirmed_create_reminder_call_has_one_durable_insert(phase7_db) -> None:
    factory, created = phase7_db
    user, _device = await _create_user(factory, label="concurrent-idempotency")
    created.append(user.id)
    registry = ToolRegistry()
    register_reminder_tools(registry)
    call = _reminder_call()
    turn_id = uuid.uuid4()
    first_committed = asyncio.Event()
    async with factory() as session_a, factory() as session_b:
        executor_a = ToolExecutor(
            registry, idempotency_store=PostgresToolIdempotencyStore(session_a)
        )
        executor_b = ToolExecutor(
            registry, idempotency_store=PostgresToolIdempotencyStore(session_b)
        )

        async def first_request():
            result = await executor_a.execute(
                call,
                context=_reminder_context(user_id=user.id, db=session_a, turn_id=turn_id),
            )
            await session_a.commit()
            first_committed.set()
            return result

        async def replay_request():
            await first_committed.wait()
            result = await executor_b.execute(
                call,
                context=_reminder_context(user_id=user.id, db=session_b, turn_id=turn_id),
            )
            await session_b.commit()
            return result

        first, second = await asyncio.gather(first_request(), replay_request())
        count = len(
            list(
                (await session_a.scalars(select(Reminder).where(Reminder.user_id == user.id))).all()
            )
        )
    assert first.success and first.executed
    assert second.success and second.replayed and not second.executed
    assert count == 1
