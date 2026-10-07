"""PM-6 release checks; failures must prevent execution rollout.

SQLite fixtures are disposable and run by default. Real PostgreSQL acceptance
is checked separately; these tests
must never be described as PostgreSQL race or physical-device evidence.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    PlanningAuthorization,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    compute_argument_digest,
)
from app.llm.types import LLMToolCall
from app.models import Message, PlanningAction, PlanningBatch, PlanningSession, Reminder, Task
from app.planning.executor import PlanningExecutor
from app.planning.policy import PlanningConsent
from app.planning.runtime import execute_turn
from app.planning.service import PlanningError, change_state
from app.planning.types import PlanningSnapshot
from app.websocket.gateway import VoiceGateway

from . import test_planning as planning_tests
from . import test_planning_extraction as extraction_tests
from . import test_planning_policy_execution as execution_tests
from .test_planning_policy_execution import (
    AsyncDB,
    create_turn,
    make_decision,
    make_proposal,
)


@pytest.fixture
def planning_storage():
    yield from planning_tests.storage.__wrapped__()


@pytest.fixture
def execution_storage():
    yield from execution_tests.storage.__wrapped__()


@pytest.fixture
def tool_registry():
    return execution_tests.tool_registry.__wrapped__()


@pytest.fixture
def tool_executor(tool_registry):
    return execution_tests.tool_executor.__wrapped__(tool_registry)


@pytest.fixture
def observed_storage(request):
    return extraction_tests.observed_storage.__wrapped__(request)


def setup_execution(data):
    engine, owners = data
    principal, session_id = owners[0]
    settings = Settings(
        _env_file=None,
        plan_mode_enabled=True,
        plan_extraction_mode="on",
        plan_auto_actions_enabled=True,
        plan_test_user_ids=(principal.user_id,),
    )
    snapshot = PlanningSnapshot(
        PlanningConsent(principal.user_id, session_id, True, True, "plan", 1, 1),
        datetime.now(UTC),
        "UTC",
    )
    return engine, principal, session_id, settings, snapshot


async def execute(executor, db, principal, session_id, turn_id, snapshot, titles=("Report",)):
    text = "I need " + " and ".join(titles) + "."
    decisions = tuple(
        make_decision(make_proposal("CREATE_TASK", title, text=text)) for title in titles
    )
    return await executor.execute_batch(
        db=db,
        principal=principal,
        session_id=session_id,
        turn_id=turn_id,
        response_id=uuid.uuid4(),
        transcript=text,
        snapshot=snapshot,
        decisions=decisions,
        user_timezone="UTC",
    )


@pytest.mark.parametrize("gate", ["master", "off", "shadow", "owner", "private"])
async def test_ineligible_executor_creates_no_proposal_or_organizational_rows(
    execution_storage, tool_executor, gate
):
    engine, principal, session_id, settings, snapshot = setup_execution(execution_storage)
    if gate == "master":
        settings.plan_mode_enabled = False
    elif gate in {"off", "shadow"}:
        settings.plan_extraction_mode = gate
    elif gate == "owner":
        settings.plan_test_user_ids = ()
    else:
        snapshot = replace(snapshot, consent=replace(snapshot.consent, memory_excluded=True))
    with Session(engine, expire_on_commit=False) as session:
        db = AsyncDB(session)
        turn_id = await create_turn(db, principal, session_id)
        await db.commit()
        await execute(
            PlanningExecutor(settings, tool_executor), db, principal, session_id, turn_id, snapshot
        )
        for model in (PlanningBatch, PlanningAction, Task):
            assert session.scalar(select(func.count()).select_from(model)) == 0, (
                f"{gate}: ineligible execution retained {model.__tablename__}"
            )


async def test_execution_switch_revokes_remaining_groups_and_keeps_scheduled_reminder(
    execution_storage, tool_registry
):
    engine, principal, session_id, settings, snapshot = setup_execution(execution_storage)
    original = tool_registry.get("create_task")
    calls = 0

    async def disable_after_first(context, arguments):
        nonlocal calls
        value = await original.handler(context, arguments)
        calls += 1
        return value

    registry = ToolRegistry()
    registry.register(
        name="create_task",
        description=original.description,
        arguments_model=original.arguments_model,
        handler=disable_after_first,
        argument_normalizer=original.argument_normalizer,
        required_scopes=original.required_scopes,
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=original.max_calls_per_turn,
    )
    executor = PlanningExecutor(
        settings, ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
    )
    with Session(engine, expire_on_commit=False) as session:

        class DisableAfterCommit(AsyncDB):
            async def commit(self):
                await super().commit()
                if self.sync.scalar(select(func.count()).select_from(Task)):
                    settings.plan_auto_actions_enabled = False

        db = DisableAfterCommit(session)
        turn_id = await create_turn(db, principal, session_id)
        reminder = Reminder(
            user_id=principal.user_id,
            title="Previously scheduled",
            trigger_at=datetime.now(UTC) + timedelta(days=1),
            timezone="UTC",
            status="scheduled",
        )
        session.add(reminder)
        await db.commit()
        await execute(executor, db, principal, session_id, turn_id, snapshot, ("Report", "Memo"))
        assert calls == 1, "execution switch failed to revoke the second transaction group"
        assert session.scalar(select(func.count()).select_from(Task)) == 1
        assert session.get(Reminder, reminder.id).status == "scheduled"


async def test_same_turn_replay_after_restart_returns_stable_receipt(
    execution_storage, tool_registry
):
    engine, principal, session_id, settings, snapshot = setup_execution(execution_storage)
    with Session(engine, expire_on_commit=False) as session:
        db = AsyncDB(session)
        turn_id = await create_turn(db, principal, session_id)
        executor = PlanningExecutor(
            settings, ToolExecutor(tool_registry, idempotency_store=InMemoryToolIdempotencyStore())
        )
        original = await execute(executor, db, principal, session_id, turn_id, snapshot)
        restarted = PlanningExecutor(
            settings, ToolExecutor(tool_registry, idempotency_store=InMemoryToolIdempotencyStore())
        )
        replay = await execute(restarted, db, principal, session_id, turn_id, snapshot)
        assert replay.batch_id == original.batch_id
        assert replay.saved_actions == original.saved_actions
        assert session.scalar(select(func.count()).select_from(Task)) == 1


async def test_grant_cannot_authorize_a_different_action_identity(execution_storage, tool_executor):
    engine, principal, session_id, _, _ = setup_execution(execution_storage)
    with Session(engine, expire_on_commit=False) as session:
        db = AsyncDB(session)
        turn_id = await create_turn(db, principal, session_id)
        arguments = {"title": "Report"}
        grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            action_id=uuid.uuid4(),
            tool_name="create_task",
            argument_digest=compute_argument_digest(arguments),
        )
        context = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            planning_grants=(grant,),
        )
        call = LLMToolCall(
            tool_call_id=f"plan_act_{uuid.uuid4()}", name="create_task", arguments=arguments
        )
        result = await tool_executor.execute(call, context=context)
        assert not result.executed, "a grant authorized a different action ID"
        assert session.scalar(select(func.count()).select_from(Task)) == 0


async def test_opted_in_voice_turn_persists_then_emits_planning_receipt(
    observed_storage, tool_registry
):
    observer, llm, kwargs = extraction_tests.observer_parts(observed_storage, "on")
    observer.settings.plan_auto_actions_enabled = True
    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.settings = observer.settings
    gateway.principal = kwargs["principal"]
    gateway.session_factory = observed_storage[-1]
    gateway.clock = FrozenClock(kwargs["now_utc"])
    gateway.llm_service = llm
    gateway.planning_observer = observer
    gateway.planning_executor = PlanningExecutor(
        observer.settings,
        ToolExecutor(tool_registry, idempotency_store=InMemoryToolIdempotencyStore()),
    )
    gateway._resolve_planning_voice = AsyncMock(return_value=None)
    gateway._dispatch_structured_route = AsyncMock(return_value={"status": "completed"})
    gateway._user_timezone = lambda: kwargs["timezone"]

    async def check_committed(event):
        if event["type"] == "server.planning.actions":
            with Session(observed_storage[0]) as session:
                assert session.scalar(select(func.count()).select_from(Task)) == 1

    gateway._send = AsyncMock(side_effect=check_committed)
    gateway._emit_routed_final_text = AsyncMock()
    await gateway._stream_llm_response(
        session_id=kwargs["session_id"],
        turn_id=kwargs["turn_id"],
        response_id=uuid.uuid4(),
        transcript=kwargs["transcript"],
    )
    await asyncio.gather(*tuple(observer.tasks))
    with Session(observed_storage[0]) as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1, (
            "the opted-in voice path did not execute the grounded proposal"
        )
    receipts = [
        call.args[0]
        for call in gateway._send.await_args_list
        if call.args[0].get("type") == "server.planning.actions"
    ]
    assert receipts, "the opted-in voice path did not emit a committed planning receipt"
    assert not gateway._dispatch_structured_route.await_count
    request_count = len(llm.requests)
    await gateway._stream_llm_response(
        session_id=kwargs["session_id"],
        turn_id=kwargs["turn_id"],
        response_id=uuid.uuid4(),
        transcript=kwargs["transcript"],
    )
    assert len(llm.requests) == request_count
    assert gateway._send.await_args_list[-1].args[0]["receipt"] == receipts[0]["receipt"]


async def run_observed(data, registry, *, llm_options=None):
    observer, llm, kwargs = extraction_tests.observer_parts(data, "on", **(llm_options or {}))
    observer.settings.plan_auto_actions_enabled = True
    executor = PlanningExecutor(
        observer.settings,
        ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore()),
    )

    async def run(**changes):
        return await execute_turn(
            observer, executor, response_id=uuid.uuid4(), **{**kwargs, **changes}
        )

    return observer, llm, kwargs, run


@pytest.mark.parametrize(
    "gate", ["master", "off", "shadow", "owner", "automatic", "private", "normal"]
)
async def test_foreground_gates_preserve_normal_path_and_make_no_requests(
    observed_storage,
    tool_registry,
    gate,
):
    observer, llm, kwargs, run = await run_observed(observed_storage, tool_registry)
    if gate == "master":
        observer.settings.plan_mode_enabled = False
    elif gate in {"off", "shadow"}:
        observer.settings.plan_extraction_mode = gate
    elif gate == "owner":
        observer.settings.plan_test_user_ids = ()
    elif gate == "automatic":
        observer.settings.plan_auto_actions_enabled = False
    else:
        with Session(observed_storage[0]) as session:
            if gate == "normal":
                session.get(PlanningSession, kwargs["session_id"]).mode = "normal"
            else:
                from app.models import VoiceSession

                session.get(VoiceSession, kwargs["session_id"]).client_metadata = {
                    "memory_excluded": True
                }
            session.commit()
    assert await run() is None
    assert len(llm.requests) == 0
    with Session(observed_storage[0]) as session:
        for model in (PlanningBatch, PlanningAction, Task):
            assert session.scalar(select(func.count()).select_from(model)) == 0


async def test_foreground_disable_during_extraction_discards_results(
    observed_storage, tool_registry
):
    entered, release = asyncio.Event(), asyncio.Event()
    observer, llm, kwargs, run = await run_observed(
        observed_storage,
        tool_registry,
        llm_options={"entered": entered, "release": release},
    )
    observer.settings.plan_extraction_timeout_ms = 1000
    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 1)
    async with observed_storage[-1]() as db:
        await change_state(
            db,
            kwargs["principal"],
            kwargs["session_id"],
            expected_version=2,
            enabled=True,
            mode="normal",
        )
        await db.commit()
    release.set()
    with pytest.raises(PlanningError, match="planning_consent_revoked"):
        await task
    with Session(observed_storage[0]) as session:
        for model in (PlanningBatch, PlanningAction, Task):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert observer.foreground_count == 0


async def test_foreground_timeout_never_creates_proposals_or_actions(
    observed_storage, tool_registry
):
    observer, _, _, run = await run_observed(
        observed_storage,
        tool_registry,
        llm_options={"delay": 10},
    )
    with pytest.raises(PlanningError, match="planning_timeout"):
        await run()
    with Session(observed_storage[0]) as session:
        for model in (PlanningBatch, PlanningAction, Task):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert observer.foreground_count == 0


async def test_foreground_replay_rejects_changed_persisted_source(observed_storage, tool_registry):
    _, llm, kwargs, run = await run_observed(observed_storage, tool_registry)
    receipt = await run()
    assert receipt.saved_actions
    changed = "I need the memo Friday."
    with Session(observed_storage[0]) as session:
        message = session.scalar(select(Message).where(Message.turn_id == kwargs["turn_id"]))
        message.content = changed
        session.commit()
    with pytest.raises(PlanningError, match="planning_replay_source_changed"):
        await run(transcript=changed)
    assert len(llm.requests) == 1
