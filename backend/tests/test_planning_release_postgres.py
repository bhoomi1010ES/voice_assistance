"""Real PostgreSQL release checks in generated schemas; never touch application records."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.errors import LLMToolError
from app.llm.tool_loop import ToolExecutor, ToolRegistry, create_default_tool_registry
from app.models import (
    AuthSession,
    ConversationTurn,
    Device,
    MemoryItem,
    Message,
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    PlanningSession,
    Reminder,
    Task,
    ToolExecutionRecord,
    User,
    VoiceSession,
)
from app.planning.executor import PlanningExecutor
from app.planning.policy import PlanningConsent
from app.planning.repository import erase_proposal_content
from app.planning.service import PlanningError, change_state
from app.planning.types import PlanningSnapshot
from app.reminders.worker import ReminderWorker
from app.services.auth import AuthPrincipal
from app.services.push_delivery import FakePushDeliveryProvider
from app.services.tool_idempotency import PostgresToolIdempotencyStore

from .test_planning_policy_execution import make_decision, make_proposal

pytestmark = pytest.mark.integration


async def test_postgres_applied_migration_and_nullable_grouping_readiness():
    url = os.environ.get("PLAN_MODE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PLAN_MODE_TEST_DATABASE_URL for read-only migration readiness.")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "migrations"))
    expected_heads = ScriptDirectory.from_config(config).get_heads()
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            versions = list(
                (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars()
            )
            query = (
                "SELECT table_name,column_name,is_nullable FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name IN ('tasks','reminders') "
                "AND column_name IN ('plan_id','planning_action_id') "
                "ORDER BY table_name,column_name"
            )
            columns = [dict(row) for row in (await connection.execute(text(query))).mappings()]
        assert sorted(versions) == sorted(expected_heads)
        assert len(columns) == 4 and all(row["is_nullable"] == "YES" for row in columns)
        report = {
            "migration_heads": versions,
            "source_heads": expected_heads,
            "nullable_grouping_columns": columns,
            "application_records_mutated": False,
            "migration_upgrade_downgrade_roundtrip": "not exercised in PM-6",
        }
        (root / "docs" / "pm6_migration_readiness.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        await engine.dispose()


@pytest.fixture
async def release_database():
    url = os.environ.get("PLAN_MODE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PLAN_MODE_TEST_DATABASE_URL for isolated PostgreSQL release checks.")
    schema = "pm6_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    metadata = MetaData()
    for model in (
        User,
        Device,
        AuthSession,
        VoiceSession,
        ConversationTurn,
        Message,
        MemoryItem,
        Plan,
        PlanContextItem,
        PlanningSession,
        PlanningBatch,
        PlanningAction,
        Task,
        Reminder,
        ToolExecutionRecord,
    ):
        model.__table__.to_metadata(metadata)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with factory() as db:
            user = User(email="disposable-pm6@test.invalid", password_hash="not-a-login")
            db.add(user)
            await db.flush()
            device = Device(user_id=user.id, device_identifier="pm6-only", platform="android")
            db.add(device)
            await db.flush()
            auth = AuthSession(
                user_id=user.id,
                device_id=device.id,
                refresh_token_hash=uuid.uuid4().hex,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            db.add(auth)
            await db.flush()
            principal = AuthPrincipal(user_id=user.id, device_id=device.id, session_id=auth.id)
            voices = []
            for _ in range(2):
                voice = VoiceSession(
                    user_id=user.id,
                    device_id=device.id,
                    auth_session_id=auth.id,
                    protocol_version=1,
                    status="active",
                )
                db.add(voice)
                await db.flush()
                db.add(
                    PlanningSession(
                        session_id=voice.id,
                        user_id=user.id,
                        mode="plan",
                        state_version=1,
                    )
                )
                voices.append(voice.id)
            await db.commit()
        settings = Settings(
            _env_file=None,
            plan_mode_enabled=True,
            plan_extraction_mode="on",
            plan_auto_actions_enabled=True,
            plan_test_user_ids=(principal.user_id,),
        )
        yield factory, principal, voices, settings
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


async def new_turn(factory, principal, session_id, transcript="I need Report."):
    async with factory() as db:
        number = await db.scalar(
            select(func.coalesce(func.max(ConversationTurn.turn_number), 0)).where(
                ConversationTurn.session_id == session_id
            )
        )
        turn = ConversationTurn(
            user_id=principal.user_id,
            session_id=session_id,
            turn_number=number + 1,
            status="committed",
        )
        db.add(turn)
        await db.flush()
        db.add(Message(turn_id=turn.id, user_id=principal.user_id, role="user", content=transcript))
        await db.commit()
        return turn.id


async def run_batch(
    data, session_id, turn_id, decisions=None, registry=None, transcript="I need Report."
):
    factory, principal, _, settings = data
    async with factory() as db:
        state = await db.get(PlanningSession, session_id)
        snapshot = PlanningSnapshot(
            PlanningConsent(
                principal.user_id,
                session_id,
                True,
                True,
                state.mode,
                state.state_version,
                state.state_version,
            ),
            datetime.now(UTC),
            "UTC",
            state.active_plan_id,
        )
        executor = PlanningExecutor(
            settings,
            ToolExecutor(
                registry or create_default_tool_registry(),
                idempotency_store=PostgresToolIdempotencyStore(db),
            ),
        )
        return await executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript=transcript,
            snapshot=snapshot,
            decisions=decisions or (make_decision(make_proposal("CREATE_TASK", "Report")),),
            user_timezone="UTC",
        )


async def test_postgres_cross_session_concurrent_dedupe(release_database):
    factory, principal, voices, _ = release_database
    turns = [await new_turn(factory, principal, voice) for voice in voices]
    receipts = await asyncio.wait_for(
        asyncio.gather(
            *(
                run_batch(release_database, voice, turn)
                for voice, turn in zip(voices, turns, strict=True)
            )
        ),
        10,
    )
    assert sum(len(r.saved_actions) for r in receipts) == 1
    assert sum(len(r.duplicate_actions) for r in receipts) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        assert await db.scalar(select(func.count()).select_from(ToolExecutionRecord)) == 1


async def test_postgres_replay_uses_durable_receipt_with_fresh_connection(release_database):
    factory, principal, voices, _ = release_database
    turn = await new_turn(factory, principal, voices[0])
    original = await run_batch(release_database, voices[0], turn)
    replay = await run_batch(release_database, voices[0], turn)
    assert replay == original
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        assert await db.scalar(select(func.count()).select_from(PlanningBatch)) == 1
        assert await db.scalar(select(func.count()).select_from(ToolExecutionRecord)) == 1


async def test_postgres_coupled_failure_rolls_back_task_and_tool_record(release_database):
    factory, principal, voices, _ = release_database
    turn = await new_turn(factory, principal, voices[0])
    defaults = create_default_tool_registry()
    task, reminder = defaults.get("create_task"), defaults.get("create_reminder")
    registry = ToolRegistry()

    async def fail_reminder(context, arguments):
        raise LLMToolError("execution_failed")

    for tool, handler in ((task, task.handler), (reminder, fail_reminder)):
        registry.register(
            name=tool.name,
            description=tool.description,
            arguments_model=tool.arguments_model,
            handler=handler,
            argument_normalizer=tool.argument_normalizer,
            required_scopes=tool.required_scopes,
            read_only=False,
            requires_confirmation=True,
            max_calls_per_turn=tool.max_calls_per_turn,
        )
    at = datetime.now(UTC) + timedelta(days=1)
    decisions = (
        make_decision(make_proposal("CREATE_TASK", "Report")),
        make_decision(make_proposal("CREATE_REMINDER", "Report"), scheduled_at=at),
    )
    receipt = await run_batch(release_database, voices[0], turn, decisions, registry)
    assert len(receipt.failed_actions) == 2
    assert not receipt.saved_actions
    async with factory() as db:
        for model in (Task, Reminder, ToolExecutionRecord):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
        assert set(await db.scalars(select(PlanningAction.status))) == {"failed"}


@pytest.mark.parametrize("gate", ["master", "extractor", "automatic"])
async def test_postgres_switch_rollback_keeps_existing_records_readable(release_database, gate):
    factory, principal, voices, settings = release_database
    turn = await new_turn(factory, principal, voices[0])
    original = await run_batch(release_database, voices[0], turn)
    async with factory() as db:
        reminder = Reminder(
            user_id=principal.user_id,
            title="Previously scheduled",
            trigger_at=datetime.now(UTC) + timedelta(days=1),
            timezone="UTC",
        )
        db.add(reminder)
        await db.commit()
        reminder_id = reminder.id
    if gate == "master":
        settings.plan_mode_enabled = False
    elif gate == "extractor":
        settings.plan_extraction_mode = "off"
    else:
        settings.plan_auto_actions_enabled = False
    next_turn = await new_turn(factory, principal, voices[0])
    receipt = await run_batch(release_database, voices[0], next_turn)
    assert not receipt.saved_actions
    async with factory() as db:
        assert (await db.get(Task, original.saved_actions[0]["id"])).title == "Report"
        assert (await db.get(Reminder, reminder_id)).status == "scheduled"
        assert await db.scalar(select(func.count()).select_from(Task)) == 1


async def test_postgres_source_purge_erases_receipts_without_deleting_saved_tasks(release_database):
    factory, principal, voices, _ = release_database
    turn = await new_turn(factory, principal, voices[0])
    receipt = await run_batch(release_database, voices[0], turn)
    async with factory() as db:
        await change_state(
            db, principal, voices[0], expected_version=1, enabled=True, mode="normal"
        )
        await erase_proposal_content(db, principal.user_id, voices[0])
        await db.commit()
    async with factory() as db:
        action = await db.scalar(select(PlanningAction))
        assert action.payload_json == {"redacted": True}
        assert action.source_spans_json == [] and action.result_json is None
        assert (await db.get(Task, receipt.saved_actions[0]["id"])).title == "Report"


@pytest.mark.parametrize("restart", [False, True])
async def test_postgres_worker_claims_and_restart_with_fake_delivery(release_database, restart):
    """Database worker evidence only: the delivery provider is explicitly synthetic."""
    factory, principal, _, settings = release_database
    now = datetime.now(UTC)
    async with factory() as db:
        device = await db.get(Device, principal.device_id)
        device.push_token = "synthetic-pm6-token-not-a-credential"
        reminder = Reminder(
            user_id=principal.user_id,
            title="Synthetic worker fixture",
            trigger_at=now,
            timezone="UTC",
            status="scheduled",
        )
        db.add(reminder)
        await db.commit()
        reminder_id = reminder.id
    provider = FakePushDeliveryProvider()
    first = ReminderWorker(settings, provider=provider, clock=FrozenClock(now), worker_id="first")
    if restart:
        async with factory() as db:
            claimed = await first._claim_one(db, now)
            assert claimed.id == reminder_id and claimed.status == "processing"
        now += timedelta(seconds=settings.reminder_lease_seconds + 1)

    async def poll(worker_id):
        worker = ReminderWorker(
            settings,
            provider=provider,
            clock=FrozenClock(now),
            worker_id=worker_id,
        )
        async with factory() as db:
            return await worker.run_once(db)

    processed = await asyncio.wait_for(asyncio.gather(poll("second"), poll("third")), 10)
    assert sum(processed) == 1
    assert len(provider.deliveries) == 1
    async with factory() as db:
        reminder = await db.get(Reminder, reminder_id)
        assert reminder.status == "sent"
        assert reminder.attempt_count == (2 if restart else 1)


@pytest.mark.parametrize("revoke", ["execution_switch", "privacy_purge"])
async def test_postgres_revocation_between_groups_prevents_later_writes_and_receipt_restore(
    release_database,
    revoke,
):
    factory, principal, voices, settings = release_database
    turn = await new_turn(factory, principal, voices[0], "I need Report and Memo.")
    hook_ran = False

    class RevokeAfterCommit(AsyncSession):
        async def commit(self):
            nonlocal hook_ran
            has_task = bool(await self.scalar(select(func.count()).select_from(Task)))
            await super().commit()
            if has_task and not hook_ran:
                hook_ran = True
                if revoke == "execution_switch":
                    settings.plan_auto_actions_enabled = False
                else:
                    async with factory() as other:
                        await change_state(
                            other,
                            principal,
                            voices[0],
                            expected_version=1,
                            enabled=True,
                            mode="normal",
                        )
                        await erase_proposal_content(other, principal.user_id, voices[0])
                        await other.commit()

    hooked_factory = async_sessionmaker(
        factory.kw["bind"],
        class_=RevokeAfterCommit,
        expire_on_commit=False,
    )
    decisions = tuple(
        make_decision(make_proposal("CREATE_TASK", title)) for title in ("Report", "Memo")
    )
    data = (hooked_factory, principal, voices, settings)
    if revoke == "privacy_purge":
        with pytest.raises(PlanningError, match="planning_consent_revoked"):
            await run_batch(data, voices[0], turn, decisions, transcript="I need Report and Memo.")
    else:
        receipt = await run_batch(
            data, voices[0], turn, decisions, transcript="I need Report and Memo."
        )
        assert len(receipt.saved_actions) == 1 and len(receipt.failed_actions) == 1
    assert hook_ran
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        assert await db.scalar(select(func.count()).select_from(ToolExecutionRecord)) == 1
        if revoke == "privacy_purge":
            for action in await db.scalars(select(PlanningAction)):
                assert action.payload_json == {"redacted": True}
                assert action.result_json is None


async def test_postgres_rescheduling_preserves_unmentioned_fields(release_database):
    factory, principal, voices, _ = release_database
    at = datetime.now(UTC) + timedelta(days=2)
    async with factory() as db:
        task = Task(
            user_id=principal.user_id, title="Prepare deck", description="Saved notes", due_at=at
        )
        reminder = Reminder(
            user_id=principal.user_id,
            title="Review",
            body="Saved body",
            trigger_at=at,
            timezone="UTC",
            recurrence_rule="FREQ=DAILY;INTERVAL=1",
        )
        db.add_all((task, reminder))
        await db.commit()
        task_id, reminder_id = task.id, reminder.id
    text_source = "Move that to Friday. Move the review to Friday."
    turn = await new_turn(factory, principal, voices[0], text_source)
    decisions = (
        make_decision(
            make_proposal("UPDATE_TASK", "Move that", text="Move that to Friday."),
            target_id=task_id,
            target_revision=1,
            scheduled_at=at + timedelta(days=1),
        ),
        make_decision(
            make_proposal("UPDATE_REMINDER", "Review", text="Move the review to Friday."),
            target_id=reminder_id,
            target_revision=1,
            scheduled_at=at + timedelta(days=1),
        ),
    )
    receipt = await run_batch(release_database, voices[0], turn, decisions, transcript=text_source)
    assert len(receipt.saved_actions) == 2 and not receipt.failed_actions
    assert all(item["status"] == "updated" for item in receipt.saved_actions)
    assert "Added task" not in receipt.text_summary
    async with factory() as db:
        task, reminder = await db.get(Task, task_id), await db.get(Reminder, reminder_id)
        assert (task.title, task.description) == ("Prepare deck", "Saved notes")
        assert (reminder.title, reminder.body) == ("Review", "Saved body")
        assert reminder.recurrence_rule == "FREQ=DAILY;INTERVAL=1"
        assert task.due_at == at + timedelta(days=1)
        assert reminder.trigger_at == at + timedelta(days=1)
