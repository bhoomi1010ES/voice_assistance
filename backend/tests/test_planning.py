from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.api import plans, reminders, tasks
from app.api.dependencies import get_current_principal
from app.db.dependencies import get_db
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
from app.planning.service import (
    PlanningError,
    change_state,
    execution_barrier,
    locked_state,
    recognize_mode_control,
    revoke_auth_state,
)
from app.services.voice_persistence import VoicePersistence
from app.websocket.protocol import ProtocolError, parse_control_message


class AsyncDB:
    """Exercise real ORM queries/transactions without external services.

    SQLite validates ownership and ORM revisions; the lock protocol is tested
    separately below. It cannot establish PostgreSQL concurrency behavior.
    """

    def __init__(self, sync):
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
        for constraint in table.constraints:
            if (
                isinstance(constraint, ForeignKeyConstraint)
                and constraint.ondelete
                and constraint.ondelete.startswith("SET NULL (")
            ):
                constraint.ondelete = (
                    "SET NULL"  # PostgreSQL-specific deletion tested in frozen DDL.
                )
    metadata.create_all(engine)
    owners = []
    with Session(engine, expire_on_commit=False) as db:
        for number in range(2):
            user = User(email=f"owner{number}@test.local", password_hash="test")
            db.add(user)
            db.flush()
            device = Device(
                user_id=user.id, device_identifier=f"device-{number}", platform="android"
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
            voice = VoiceSession(
                user_id=user.id,
                device_id=device.id,
                auth_session_id=auth.id,
                protocol_version=1,
                status="active",
            )
            db.add(voice)
            db.flush()
            principal = SimpleNamespace(user_id=user.id, device_id=device.id, session_id=auth.id)
            owners.append((principal, voice.id))
        db.commit()
    yield engine, owners
    engine.dispose()


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("Enable plan mode.", "plan"),
        ("Turn on planning mode!", "plan"),
        ("Let's plan this", "plan"),
        ("STOP PLANNING", "normal"),
        ("disable planning mode", "normal"),
        ("switch to normal mode", "normal"),
        ('He said "enable plan mode"', None),
        ('"enable plan mode"', None),
        ("If I enable plan mode", None),
        ("Don't enable plan mode", None),
        ("Enable plan mode?", None),
        ("How do I enable plan mode?", None),
        ("Enable plan mode and finish my report", None),
        ("yes", None),
    ],
)
def test_direct_mode_recognition(text, mode):
    assert recognize_mode_control(text) == mode


def test_protocol_requires_session_and_integer_revision():
    import json

    valid = {
        "type": "client.planning.set_mode",
        "session_id": str(uuid.uuid4()),
        "expected_state_version": 1,
        "mode": "plan",
    }
    assert parse_control_message(json.dumps(valid), max_bytes=4096).mode == "plan"
    for extra in [
        {"expected_state_version": "1"},
        {"mode": "auto"},
        {"user_id": str(uuid.uuid4())},
        {"expected_state_version": 0},
    ]:
        with pytest.raises(ProtocolError):
            parse_control_message(json.dumps({**valid, **extra}), max_bytes=4096)


async def test_state_ownership_revision_private_and_resume(storage):
    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        state = await locked_state(db, principal, session_id)
        assert state.mode == "normal" and state.state_version == 1
        await db.commit()
        enabled = await change_state(
            db, principal, session_id, expected_version=1, enabled=True, mode="plan"
        )
        await db.commit()
        assert enabled["mode"] == "plan" and enabled["automatic_actions_available"] is False
        with pytest.raises(PlanningError, match="planning_state_conflict"):
            await change_state(
                db, principal, session_id, expected_version=1, enabled=True, mode="normal"
            )
        with pytest.raises(PlanningError, match="session_not_available"):
            await locked_state(db, owners[1][0], session_id)
        foreign = Plan(user_id=owners[1][0].user_id, name="Foreign")
        sync.add(foreign)
        sync.commit()
        with pytest.raises(PlanningError, match="plan_not_found"):
            await change_state(
                db,
                principal,
                session_id,
                expected_version=2,
                enabled=True,
                plan_id=foreign.id,
                select_plan=True,
            )
        own = Plan(user_id=principal.user_id, name="Owned")
        sync.add(own)
        sync.commit()
        selected = await change_state(
            db,
            principal,
            session_id,
            expected_version=2,
            enabled=True,
            plan_id=own.id,
            select_plan=True,
        )
        await db.commit()
        assert selected["active_plan_name"] == "Owned"
        persistence = VoicePersistence()
        await persistence.finalize_session(
            db,
            principal,
            session_id=session_id,
            status="disconnected",
            close_code=1001,
            close_reason="network",
            total_frames=0,
            total_bytes=0,
            error_count=0,
        )
        await db.commit()
        assert state.mode == "plan"
        # SQLite returns naive datetimes; the existing resume method uses UTC.
        voice = sync.get(VoiceSession, session_id)
        voice.ended_at = datetime.now(UTC)
        resumed = await persistence.resume_session(
            db, principal, session_id, reconnect_grace_seconds=60
        )
        assert resumed is not None
        await db.commit()
        resumed.client_metadata = {"memory_excluded": True}
        await db.commit()
        with pytest.raises(PlanningError, match="planning_private_session"):
            await change_state(
                db, principal, session_id, expected_version=3, enabled=True, mode="plan"
            )
        disabled = await change_state(
            db, principal, session_id, expected_version=3, enabled=False, mode="normal"
        )
        await db.commit()
        assert disabled["mode"] == "normal" and disabled["active_plan_id"] is None


@pytest.mark.parametrize("reason", ["completed", "timed_out", "failed", "logout"])
async def test_lifecycle_revokes_without_deleting_saved_items(storage, reason):
    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        await change_state(db, principal, session_id, expected_version=1, enabled=True, mode="plan")
        task = Task(user_id=principal.user_id, title="Saved task")
        sync.add(task)
        sync.commit()
        if reason == "logout":
            await revoke_auth_state(db, principal.user_id, principal.session_id)
        else:
            await VoicePersistence().finalize_session(
                db,
                principal,
                session_id=session_id,
                status=reason,
                close_code=1000,
                close_reason=reason,
                total_frames=0,
                total_bytes=0,
                error_count=0,
            )
        await db.commit()
        state = sync.get(PlanningSession, session_id)
        assert state.mode == "normal" and state.state_version == 3
        assert sync.get(Task, task.id).title == "Saved task"


async def test_disabled_and_archived_controls_fail_closed(storage):
    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        with pytest.raises(PlanningError, match="planning_unavailable"):
            await change_state(
                db, principal, session_id, expected_version=1, enabled=False, mode="plan"
            )
        await db.rollback()
        await change_state(db, principal, session_id, expected_version=1, enabled=True, mode="plan")
        plan = Plan(user_id=principal.user_id, name="Archived", status="archived")
        sync.add(plan)
        sync.commit()
        with pytest.raises(PlanningError, match="plan_not_active"):
            await change_state(
                db,
                principal,
                session_id,
                expected_version=2,
                enabled=True,
                plan_id=plan.id,
                select_plan=True,
            )


def test_composite_ownership_and_cross_session_turn_constraint(storage):
    engine, owners = storage
    with Session(engine) as db:
        foreign = Plan(user_id=owners[1][0].user_id, name="Foreign")
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
        db.add(Task(user_id=owners[0][0].user_id, title="Invalid grouping", plan_id=foreign_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # The action's turn must belong to the original session as well as user.
        fk = next(c for c in PlanningBatch.__table__.foreign_key_constraints if len(c.columns) == 3)
        assert tuple(fk.columns.keys()) == ("turn_id", "session_id", "user_id")


def test_owner_scoped_apis_and_legacy_payloads(storage):
    engine, owners = storage
    app = FastAPI()
    for router in (plans.router, tasks.router, reminders.router):
        app.include_router(router)

    async def database():
        with Session(engine, expire_on_commit=False) as sync:
            yield AsyncDB(sync)

    async def principal(request: Request):
        if request.headers.get("x-owner") not in {"0", "1"}:
            raise HTTPException(401)
        return owners[int(request.headers["x-owner"])][0]

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_principal] = principal
    with TestClient(app) as client:
        a, b = {"x-owner": "0"}, {"x-owner": "1"}
        assert client.get("/plans").status_code == 401
        saved = client.post("/plans", headers=a, json={"name": "XYZ"})
        assert saved.status_code == 201, saved.text
        plan = saved.json()
        assert "user_id" not in plan
        assert client.get(f"/plans/{plan['id']}", headers=b).status_code == 404
        assert client.get("/plans", headers=b).json() == []
        assert (
            client.patch(
                f"/plans/{plan['id']}", headers=b, json={"expected_revision": 1, "goal": "Denied"}
            ).status_code
            == 404
        )
        updated = client.patch(
            f"/plans/{plan['id']}", headers=a, json={"expected_revision": 1, "goal": "Ship"}
        )
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        assert (
            client.patch(
                f"/plans/{plan['id']}", headers=a, json={"expected_revision": 1, "goal": "Stale"}
            ).status_code
            == 409
        )
        assert client.get(f"/plans/{plan['id']}/actions", headers=b).status_code == 404
        legacy = client.post("/tasks", headers=a, json={"title": "Old client"})
        assert legacy.status_code == 201 and legacy.json()["plan_id"] is None
        grouped = client.post("/tasks", headers=a, json={"title": "Grouped", "plan_id": plan["id"]})
        assert grouped.status_code == 201
        task = grouped.json()
        assert (
            client.post(
                "/tasks", headers=b, json={"title": "Denied", "plan_id": plan["id"]}
            ).status_code
            == 404
        )
        assert len(client.get(f"/tasks?plan_id={plan['id']}", headers=a).json()) == 1
        assert (
            client.patch(
                f"/tasks/{task['id']}", headers=a, json={"expected_revision": 1, "title": "Edited"}
            ).json()["revision"]
            == 2
        )
        assert (
            client.patch(
                f"/tasks/{task['id']}", headers=a, json={"expected_revision": 1, "title": "Stale"}
            ).status_code
            == 409
        )
        assert (
            client.patch(
                f"/tasks/{task['id']}", headers=a, json={"status": "completed"}
            ).status_code
            == 200
        )
        reminder = client.post(
            "/reminders",
            headers=a,
            json={
                "title": "Legacy reminder",
                "timezone": "UTC",
                "trigger_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            },
        )
        assert reminder.status_code == 201, reminder.text
        assert reminder.json()["plan_id"] is None
        rid = reminder.json()["id"]
        assert (
            client.patch(
                f"/reminders/{rid}", headers=a, json={"expected_revision": 1, "plan_id": plan["id"]}
            ).json()["revision"]
            == 2
        )
        assert (
            client.patch(
                f"/reminders/{rid}", headers=a, json={"expected_revision": 1, "body": "Stale"}
            ).status_code
            == 409
        )
        assert len(client.get(f"/reminders?plan_id={plan['id']}", headers=a).json()) == 1
        assert client.get(f"/plans/{plan['id']}", headers=a).json()["task_counts"] == {
            "completed": 1
        }


def test_migration_frozen_ddl_and_preserving_optional_provenance():
    path = Path(__file__).parents[1] / "migrations/versions/0021_planning_foundation.py"
    spec = importlib.util.spec_from_file_location("planning_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    module.op = SimpleNamespace(
        execute=statements.append, drop_table=lambda name: statements.append(name)
    )
    module.upgrade()
    ddl = "\n".join(statements)
    assert module.down_revision == "0020_user_knowledge_mode"
    for model in [Plan, PlanContextItem, PlanningSession, PlanningBatch, PlanningAction]:
        assert (
            str(CreateTable(model.__table__).compile(dialect=postgresql.dialect())).strip() in ddl
        )
    assert "ON DELETE SET NULL (plan_id)" in ddl
    assert "ON DELETE SET NULL (planning_action_id)" in ddl
    assert "revision INTEGER NOT NULL DEFAULT 1" in ddl
    assert "conversation_turns (id, session_id, user_id)" in ddl
    module.downgrade()


async def test_disable_serializes_with_execution_commit(monkeypatch):
    """Barrier harness verifies the same lock is held through both commits."""
    import app.planning.service as service

    lock = asyncio.Lock()
    entered, release, acknowledged = asyncio.Event(), asyncio.Event(), asyncio.Event()
    state = SimpleNamespace(
        mode="plan",
        state_version=2,
        active_plan_id=None,
        disabled_at=None,
        user_id=uuid.uuid4(),
        policy_version="plan-v1",
        enabled_at=None,
    )
    order = []

    class Transaction:
        held = False

        async def flush(self):
            pass

        async def scalar(self, _):
            return SimpleNamespace(client_metadata={})

        async def commit(self):
            order.append(self.name)
            if self.held:
                self.held = False
                lock.release()

    async def locked(db, *_args, **_kwargs):
        if not db.held:
            await lock.acquire()
            db.held = True
        return state

    async def cancel(*_args):
        pass

    monkeypatch.setattr(service, "locked_state", locked)
    monkeypatch.setattr(service, "cancel_pending", cancel)

    async def execution():
        db = Transaction()
        db.name = "mutation_commit"
        async with execution_barrier(db, None, None, 2):
            entered.set()
            await release.wait()
            await db.commit()

    async def disable():
        db = Transaction()
        db.name = "disable_commit"
        await change_state(
            db,
            SimpleNamespace(user_id=state.user_id),
            None,
            expected_version=2,
            enabled=True,
            mode="normal",
        )
        await db.commit()
        order.append("acknowledgement")
        acknowledged.set()

    running = asyncio.create_task(execution())
    await entered.wait()
    off = asyncio.create_task(disable())
    await asyncio.sleep(0)
    assert not acknowledged.is_set()
    release.set()
    await asyncio.gather(running, off)
    assert order == ["mutation_commit", "disable_commit", "acknowledgement"]
    db = Transaction()
    with pytest.raises(PlanningError, match="planning_consent_revoked"):
        async with execution_barrier(db, None, None, 2):
            pytest.fail("stale action was allowed")
    lock.release()


async def test_gateway_voice_precedes_confirmation_and_retries_do_not_reenable(storage):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app.core.config import Settings
    from app.websocket.gateway import VoiceGateway

    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        turn = ConversationTurn(
            user_id=principal.user_id, session_id=session_id, turn_number=1, status="committed"
        )
        sync.add(turn)
        sync.commit()
        turn_id = turn.id

    @asynccontextmanager
    async def factory():
        with Session(engine, expire_on_commit=False) as sync:
            yield AsyncDB(sync)

    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.session_factory = factory
    gateway.principal = principal
    gateway.settings = Settings(_env_file=None, plan_mode_enabled=True)
    gateway._session_id = session_id
    gateway._send = AsyncMock()
    gateway._speak_text = AsyncMock()
    gateway._emit_routed_final_text = AsyncMock()
    gateway.confirmation_store = SimpleNamespace(get=AsyncMock())
    response_id = uuid.uuid4()
    result = await gateway._resolve_pending_confirmation(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        transcript="Enable plan mode",
    )
    assert result["tool_execution_count"] == 0
    gateway.confirmation_store.get.assert_not_called()
    events = [call.args[0] for call in gateway._send.call_args_list]
    assert events[0]["type"] == "server.planning.state" and events[0]["planning"]["mode"] == "plan"
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        await change_state(
            db, principal, session_id, expected_version=2, enabled=True, mode="normal"
        )
        await db.commit()
        assert sync.scalar(select(Task)) is None
        assert sync.scalar(select(Reminder)) is None
        assert sync.scalar(select(Plan)) is None
        assert sync.scalar(select(PlanningBatch)) is None
    await gateway._resolve_planning_voice(
        session_id=session_id,
        turn_id=turn_id,
        response_id=uuid.uuid4(),
        transcript="Enable plan mode",
    )
    with Session(engine) as sync:
        assert sync.get(PlanningSession, session_id).mode == "normal"
        assert sync.get(PlanningSession, session_id).state_version == 3


async def test_disable_cancels_only_unexecuted_planning_proposals(storage):
    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        await change_state(db, principal, session_id, expected_version=1, enabled=True, mode="plan")
        turn = ConversationTurn(user_id=principal.user_id, session_id=session_id, turn_number=1)
        sync.add(turn)
        sync.flush()
        batch = PlanningBatch(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn.id,
            state_version=2,
            extractor_version="test",
            policy_version="plan-v1",
            source_digest="a" * 64,
        )
        sync.add(batch)
        sync.flush()
        actions = []
        for ordinal, status in enumerate(["pending", "completed"]):
            action = PlanningAction(
                user_id=principal.user_id,
                batch_id=batch.id,
                ordinal=ordinal,
                action_type="create_task",
                payload_json={},
                payload_digest="b" * 64,
                source_spans_json=[],
                disposition="CONFIRM",
                status=status,
            )
            sync.add(action)
            actions.append(action)
        sync.commit()
        await change_state(
            db, principal, session_id, expected_version=2, enabled=True, mode="normal"
        )
        await db.commit()
        assert actions[0].status == "cancelled" and actions[1].status == "completed"


async def test_exact_owned_voice_selection_and_ambiguity(storage):
    from app.planning.service import recognize_plan_selection, resolve_plan_selection

    engine, owners = storage
    principal, _ = owners[0]
    assert recognize_plan_selection("Select plan XYZ.") == "XYZ"
    assert recognize_plan_selection("Clear active plan") == ""
    assert recognize_plan_selection('He said "select plan XYZ"') is None
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        own = Plan(user_id=principal.user_id, name="XYZ")
        foreign = Plan(user_id=owners[1][0].user_id, name="XYZ")
        sync.add_all([own, foreign])
        sync.commit()
        assert await resolve_plan_selection(db, principal.user_id, "xyz") == own.id
        with pytest.raises(PlanningError, match="plan_not_found"):
            await resolve_plan_selection(db, principal.user_id, str(foreign.id))
        sync.add(Plan(user_id=principal.user_id, name="XYZ"))
        sync.commit()
        with pytest.raises(PlanningError, match="plan_ambiguous"):
            await resolve_plan_selection(db, principal.user_id, "XYZ")


def test_concurrent_edits_are_rejected_by_mapper_revision(storage):
    from sqlalchemy.orm.exc import StaleDataError

    engine, owners = storage
    with Session(engine, expire_on_commit=False) as first, Session(engine) as second:
        plan = Plan(user_id=owners[0][0].user_id, name="Concurrent")
        first.add(plan)
        first.commit()
        stale = second.get(Plan, plan.id)
        plan.goal = "First edit"
        first.commit()
        stale.goal = "Would overwrite"
        with pytest.raises(StaleDataError):
            second.commit()


@pytest.mark.parametrize("disconnected", [False, True])
async def test_expired_resume_revokes_consent(storage, disconnected):
    engine, owners = storage
    principal, session_id = owners[0]
    with Session(engine, expire_on_commit=False) as sync:
        db = AsyncDB(sync)
        await change_state(db, principal, session_id, expected_version=1, enabled=True, mode="plan")
        await db.commit()
        voice = sync.get(VoiceSession, session_id)
        voice.last_activity_at = datetime.now(UTC) - timedelta(minutes=5)
        voice.status = "disconnected" if disconnected else "active"
        voice.ended_at = voice.last_activity_at if disconnected else None
        resumed = await VoicePersistence().resume_session(
            db, principal, session_id, reconnect_grace_seconds=60, active_stale_after_seconds=105
        )
        assert resumed is None
        await db.commit()
        assert sync.get(PlanningSession, session_id).mode == "normal"
        assert voice.status == "timed_out"
