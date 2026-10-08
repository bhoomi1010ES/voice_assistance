from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_principal, get_db
from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.task_tools import (
    CompleteTaskArguments,
    CreateTaskArguments,
    DeleteTaskArguments,
    complete_task_handler,
    create_task_handler,
    delete_task_handler,
)
from app.llm.tool_loop import ToolExecutionContext
from app.main import create_app
from app.models import (
    AuthSession,
    ConversationTurn,
    Device,
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    PlanningSession,
    Reminder,
    Task,
    User,
    VoiceSession,
)
from app.services.auth import AuthPrincipal
from app.services.task_linked_reminders import has_real_clock, sync_linked_reminder_for_task
from tests.test_support import NoopSTTService


class AsyncDB:
    def __init__(self, sync: Session) -> None:
        self.sync = sync

    async def scalar(self, statement):
        return self.sync.scalar(statement)

    async def scalars(self, statement):
        return self.sync.scalars(statement)

    async def execute(self, statement):
        return self.sync.execute(statement)

    def add(self, row):
        self.sync.add(row)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()

    async def rollback(self):
        self.sync.rollback()

    async def refresh(self, row):
        self.sync.refresh(row)

    async def delete(self, row):
        self.sync.delete(row)


@pytest.fixture
def storage():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    metadata = MetaData()
    models = [
        User,
        Device,
        AuthSession,
        VoiceSession,
        ConversationTurn,
        Plan,
        PlanContextItem,
        PlanningSession,
        PlanningBatch,
        PlanningAction,
        Task,
        Reminder,
    ]
    for model in models:
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
        for constraint in list(table.constraints):
            if (
                isinstance(constraint, ForeignKeyConstraint)
                and constraint.ondelete
                and constraint.ondelete.startswith("SET NULL (")
            ):
                target_col = constraint.ondelete[len("SET NULL (") : -1].strip()
                ref_target = constraint.elements[0].target_fullname
                table.constraints.remove(constraint)
                table.append_constraint(
                    ForeignKeyConstraint(
                        [target_col],
                        [ref_target],
                        ondelete="SET NULL",
                    )
                )
    metadata.create_all(engine)
    owners = []
    with Session(engine, expire_on_commit=False) as db:
        for number in range(2):
            user = User(
                email=f"taskowner{number}@test.local",
                password_hash="test",
                timezone="Asia/Kolkata",
            )
            db.add(user)
            db.flush()
            device = Device(
                user_id=user.id, device_identifier=f"task-device-{number}", platform="android"
            )
            db.add(device)
            db.flush()
            auth = AuthSession(
                user_id=user.id,
                device_id=device.id,
                refresh_token_hash=str(number),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            db.add(auth)
            db.flush()
            principal = AuthPrincipal(
                user_id=user.id,
                device_id=device.id,
                session_id=auth.id,
            )
            owners.append(principal)
        db.commit()
    yield engine, owners
    engine.dispose()


def test_has_real_clock() -> None:
    assert has_real_clock(None, "Asia/Kolkata") is False

    # 23:59 EOD date-only
    tz = ZoneInfo("Asia/Kolkata")
    eod = datetime(2026, 10, 8, 23, 59, 0, tzinfo=tz).astimezone(UTC)
    assert has_real_clock(eod, "Asia/Kolkata") is False

    # 3:13 PM
    timed = datetime(2026, 10, 8, 15, 13, 0, tzinfo=tz).astimezone(UTC)
    assert has_real_clock(timed, "Asia/Kolkata") is True

    # 9:00 AM
    morning = datetime(2026, 10, 8, 9, 0, 0, tzinfo=tz).astimezone(UTC)
    assert has_real_clock(morning, "Asia/Kolkata") is True

    # Midnight 00:00 is a specific clock time, not 23:59 EOD
    midnight = datetime(2026, 10, 8, 0, 0, 0, tzinfo=tz).astimezone(UTC)
    assert has_real_clock(midnight, "Asia/Kolkata") is True


@pytest.mark.asyncio
async def test_sync_linked_reminder_creates_scheduled_reminder(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        due = datetime(2026, 10, 8, 9, 43, tzinfo=UTC)
        task = Task(
            user_id=principal.user_id,
            title="Call Harsh",
            description="Discuss voice reminders",
            due_at=due,
            timezone="Asia/Kolkata",
            status="pending",
        )
        session.add(task)
        await session.flush()

        reminder = await sync_linked_reminder_for_task(session, task, reason="due_set")
        assert reminder is not None
        assert reminder.task_id == task.id
        assert reminder.user_id == principal.user_id
        assert reminder.title == "Call Harsh"
        assert reminder.body == "Discuss voice reminders"
        assert reminder.trigger_at == due
        assert reminder.status == "scheduled"
        assert reminder.delivery_channel == "push"
        assert reminder.recurrence_rule is None
        assert reminder.next_attempt_at == due


@pytest.mark.asyncio
async def test_sync_linked_reminder_idempotent_upsert(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        due = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
        task = Task(
            user_id=principal.user_id,
            title="Review document",
            due_at=due,
            timezone="UTC",
            status="pending",
        )
        session.add(task)
        await session.flush()

        r1 = await sync_linked_reminder_for_task(session, task, reason="due_set")
        await session.flush()
        r2 = await sync_linked_reminder_for_task(session, task, reason="due_set")
        await session.flush()

        assert r1.id == r2.id
        query = select(Reminder).where(Reminder.task_id == task.id, Reminder.status == "scheduled")
        active = list((await session.scalars(query)).all())
        assert len(active) == 1


@pytest.mark.asyncio
async def test_sync_linked_reminder_reschedules_on_due_changed(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        due1 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
        task = Task(
            user_id=principal.user_id,
            title="Standup",
            due_at=due1,
            timezone="UTC",
            status="pending",
        )
        session.add(task)
        await session.flush()

        r1 = await sync_linked_reminder_for_task(session, task, reason="due_set")
        await session.flush()
        assert r1.trigger_at == due1

        due2 = datetime(2026, 10, 8, 11, 30, tzinfo=UTC)
        task.due_at = due2
        r2 = await sync_linked_reminder_for_task(session, task, reason="due_changed")
        await session.flush()

        assert r2.id == r1.id
        assert r2.trigger_at == due2
        assert r2.next_attempt_at == due2
        assert r2.status == "scheduled"


@pytest.mark.asyncio
async def test_sync_linked_reminder_cancels_on_due_cleared_and_completion(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        due = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        task = Task(
            user_id=principal.user_id,
            title="Testing cancellation",
            due_at=due,
            timezone="UTC",
            status="pending",
        )
        session.add(task)
        await session.flush()

        reminder = await sync_linked_reminder_for_task(session, task, reason="due_set")
        await session.flush()
        assert reminder.status == "scheduled"

        # Complete task
        task.status = "completed"
        res = await sync_linked_reminder_for_task(session, task, reason="completed")
        assert res is None
        assert reminder.status == "cancelled"
        assert reminder.next_attempt_at is None


@pytest.mark.asyncio
async def test_sync_linked_reminder_cancels_on_deleted(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        due = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
        task = Task(
            user_id=principal.user_id,
            title="To be deleted",
            due_at=due,
            timezone="UTC",
            status="pending",
        )
        session.add(task)
        await session.flush()

        reminder = await sync_linked_reminder_for_task(session, task, reason="due_set")
        await session.flush()
        assert reminder.status == "scheduled"

        # Delete reason cancels before delete
        res = await sync_linked_reminder_for_task(session, task, reason="deleted")
        assert res is None
        assert reminder.status == "cancelled"


@pytest.mark.asyncio
async def test_sync_linked_reminder_skips_and_cancels_on_eod_date_only(storage) -> None:
    engine, owners = storage
    principal = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        tz = ZoneInfo("Asia/Kolkata")
        eod = datetime(2026, 10, 8, 23, 59, 0, tzinfo=tz).astimezone(UTC)
        task = Task(
            user_id=principal.user_id,
            title="Date only task",
            due_at=eod,
            timezone="Asia/Kolkata",
            status="pending",
        )
        session.add(task)
        await session.flush()

        res = await sync_linked_reminder_for_task(session, task, reason="due_set")
        assert res is None

        # Change task to a real clock -> reminder scheduled
        real_clock = datetime(2026, 10, 8, 14, 0, 0, tzinfo=tz).astimezone(UTC)
        task.due_at = real_clock
        res = await sync_linked_reminder_for_task(session, task, reason="due_changed")
        assert res is not None and res.status == "scheduled"

        # Change task back to EOD -> reminder cancelled
        task.due_at = eod
        res = await sync_linked_reminder_for_task(session, task, reason="due_changed")
        assert res is None
        query = select(Reminder).where(Reminder.task_id == task.id)
        reminders = list((await session.scalars(query)).all())
        assert len(reminders) == 1
        assert reminders[0].status == "cancelled"


def test_rest_api_task_lifecycle_syncs_linked_reminder(storage, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.tasks.SystemClock", lambda: FrozenClock(datetime(2026, 10, 8, 0, 0, tzinfo=UTC))
    )
    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    engine, owners = storage

    async def database():
        with Session(engine, expire_on_commit=False) as sync:
            yield AsyncDB(sync)

    async def get_principal(request: Request):
        return owners[0]

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_principal] = get_principal

    with TestClient(app) as client:
        # 1. Create task with clock -> creates task AND linked reminder
        resp = client.post(
            "/tasks",
            json={
                "title": "Call Harsh at 3:13 PM",
                "due_at": "2026-10-08T15:13:00+05:30",
                "timezone": "Asia/Kolkata",
            },
        )
        assert resp.status_code == 201
        task_data = resp.json()
        task_id = task_data["id"]

        with Session(engine, expire_on_commit=False) as sync:
            reminders = list(
                sync.scalars(select(Reminder).where(Reminder.task_id == uuid.UUID(task_id))).all()
            )
            assert len(reminders) == 1
            assert reminders[0].status == "scheduled"
            assert reminders[0].title == "Call Harsh at 3:13 PM"
            assert reminders[0].delivery_channel == "push"

        # 2. Update task due date to new clock -> reschedules reminder
        patch_resp = client.patch(
            f"/tasks/{task_id}",
            json={
                "due_at": "2026-10-08T16:00:00+05:30",
            },
        )
        assert patch_resp.status_code == 200

        with Session(engine, expire_on_commit=False) as sync:
            reminders = list(
                sync.scalars(select(Reminder).where(Reminder.task_id == uuid.UUID(task_id))).all()
            )
            assert len(reminders) == 1
            assert reminders[0].status == "scheduled"

        # 3. Complete task -> cancels reminder
        complete_resp = client.post(f"/tasks/{task_id}/complete")
        assert complete_resp.status_code == 200

        with Session(engine, expire_on_commit=False) as sync:
            reminders = list(
                sync.scalars(select(Reminder).where(Reminder.task_id == uuid.UUID(task_id))).all()
            )
            assert len(reminders) == 1
            assert reminders[0].status == "cancelled"

        # 4. Create date-only task -> NO reminder created
        date_only_resp = client.post(
            "/tasks",
            json={
                "title": "Date only task",
                "due_at": "2026-10-08T23:59:00+05:30",
                "timezone": "Asia/Kolkata",
            },
        )
        assert date_only_resp.status_code == 201
        date_task_id = date_only_resp.json()["id"]

        with Session(engine, expire_on_commit=False) as sync:
            reminders = list(
                sync.scalars(
                    select(Reminder).where(Reminder.task_id == uuid.UUID(date_task_id))
                ).all()
            )
            assert len(reminders) == 0

        # 5. Delete task -> cancels linked reminder
        timed_resp2 = client.post(
            "/tasks",
            json={
                "title": "Task to delete",
                "due_at": "2026-10-08T10:00:00+05:30",
                "timezone": "Asia/Kolkata",
            },
        )
        assert timed_resp2.status_code == 201
        delete_task_id = timed_resp2.json()["id"]

        with Session(engine, expire_on_commit=False) as sync:
            reminders = list(
                sync.scalars(
                    select(Reminder).where(Reminder.task_id == uuid.UUID(delete_task_id))
                ).all()
            )
            assert len(reminders) == 1
            del_reminder_id = reminders[0].id

        del_resp = client.delete(f"/tasks/{delete_task_id}")
        assert del_resp.status_code == 204

        with Session(engine, expire_on_commit=False) as sync:
            del_reminder = sync.scalar(select(Reminder).where(Reminder.id == del_reminder_id))
            assert del_reminder is not None
            assert del_reminder.status == "cancelled"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_voice_task_tool_handlers_sync_linked_reminder(storage) -> None:
    engine, owners = storage
    principal = owners[0]

    with Session(engine, expire_on_commit=False) as sync:
        session = AsyncDB(sync)
        context = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=principal.session_id,
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write", "reminders:write"}),
            db=session,
            user_timezone="Asia/Kolkata",
            timezone_source="device",
        )

        # 1. create_task_handler with clock
        clock_due = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
        args = CreateTaskArguments(title="Voice scheduled task", due_at=clock_due)
        res = await create_task_handler(context, args)
        assert "task_id" in res
        task_id = uuid.UUID(res["task_id"])

        reminders = list(
            (await session.scalars(select(Reminder).where(Reminder.task_id == task_id))).all()
        )
        assert len(reminders) == 1
        assert reminders[0].status == "scheduled"
        assert reminders[0].trigger_at.replace(tzinfo=UTC) == clock_due
        assert reminders[0].title == "Voice scheduled task"

        # 2. complete_task_handler
        comp_args = CompleteTaskArguments(task_id=task_id)
        await complete_task_handler(context, comp_args)

        reminders = list(
            (await session.scalars(select(Reminder).where(Reminder.task_id == task_id))).all()
        )
        assert len(reminders) == 1
        assert reminders[0].status == "cancelled"

        # 3. delete_task_handler
        clock_due2 = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
        args2 = CreateTaskArguments(title="Voice task to cancel", due_at=clock_due2)
        res2 = await create_task_handler(context, args2)
        task_id2 = uuid.UUID(res2["task_id"])

        reminders_before_del = list(
            (await session.scalars(select(Reminder).where(Reminder.task_id == task_id2))).all()
        )
        assert len(reminders_before_del) == 1
        rem2_id = reminders_before_del[0].id

        del_args = DeleteTaskArguments(task_id=task_id2)
        await delete_task_handler(context, del_args)

        rem2 = await session.scalar(select(Reminder).where(Reminder.id == rem2_id))
        assert rem2 is not None
        assert rem2.status == "cancelled"
