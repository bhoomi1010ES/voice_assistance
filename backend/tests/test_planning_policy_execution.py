"""PM-3 Policy and existing tool integration tests.

Verifies:
1. Action-bound authorization grants and Normal-mode confirmation regression.
2. Bounded planner budget for executing several tasks/reminders per turn.
3. Plan/task/reminder/context writes routed through validated owned handlers/executor.
4. Stable replay identity, semantic duplicate resolution, target revisions, concurrent protection.
5. Transaction group atomic commits before success events, partial outcomes across independent groups.
6. Consequential proposals queued through existing confirmation machinery, preventing automatic execution.
7. PM-3 Gate: multi-record creation with correct per-action dates; consequential operations cannot auto-execute.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.clock import SystemClock
from app.core.config import Settings
from app.llm.errors import LLMToolError
from app.llm.plan_tools import register_plan_tools
from app.llm.reminder_tools import register_reminder_tools
from app.llm.task_tools import register_task_tools
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    PlannerBudget,
    PlanningAuthorization,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    compute_argument_digest,
)
from app.llm.types import LLMToolCall
from app.models import (
    AuthSession,
    ConversationTurn,
    Device,
    Message,
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
from app.planning.executor import PlanningExecutor
from app.planning.policy import PlanningConsent
from app.planning.types import (
    Decision,
    PlanningReceipt,
    PlanningSnapshot,
    Proposal,
    Span,
    Target,
)
from app.services.auth import AuthPrincipal
from app.services.voice_confirmation import InMemoryVoiceConfirmationStore


def make_proposal(
    operation: str,
    title: str,
    text: str = "",
    classification: str = "COMMITMENT",
    actor: str = "user",
    temporal_text: str | None = None,
    content: str | None = None,
) -> Proposal:
    src_text = text or title
    temporal_span = None
    if temporal_text:
        start = src_text.index(temporal_text) if temporal_text in src_text else 0
        temporal_span = Span(start=start, length=len(temporal_text), text=temporal_text)
    return Proposal(
        operation=operation,
        classification=classification,
        title=title,
        actor=actor,
        source=Span(start=0, length=len(src_text), text=src_text),
        temporal=temporal_span,
        content=content,
        confidence=1.0,
    )


def make_decision(
    proposal: Proposal,
    disposition: str = "AUTO",
    reason: str = "eligible",
    plan_id: uuid.UUID | None = None,
    plan_ordinal: int | None = None,
    target_id: uuid.UUID | None = None,
    target_revision: int | None = None,
    scheduled_at: datetime | None = None,
    timezone: str | None = None,
) -> Decision:
    return Decision(
        proposal=proposal,
        disposition=disposition,
        reason=reason,
        plan_id=plan_id,
        plan_ordinal=plan_ordinal,
        target_id=target_id,
        target_revision=target_revision,
        scheduled_at=scheduled_at,
        timezone=timezone,
    )


class AsyncDB:
    """Async adapter over sync SQLite Session for testing."""

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


async def create_turn(db: AsyncDB, principal: AuthPrincipal, session_id: uuid.UUID, turn_number: int | None = None) -> uuid.UUID:
    turn_id = uuid.uuid4()
    if turn_number is None:
        existing = db.sync.scalars(
            select(ConversationTurn.turn_number).where(
                ConversationTurn.session_id == session_id
            )
        ).all()
        turn_number = (max(existing) + 1) if existing else 1
    turn = ConversationTurn(
        id=turn_id,
        user_id=principal.user_id,
        session_id=session_id,
        turn_number=turn_number,
        status="committed",
    )
    db.add(turn)
    await db.flush()
    return turn_id


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
        Message,
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
                constraint.ondelete = "SET NULL"
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
            plan_session = PlanningSession(
                session_id=voice.id,
                user_id=user.id,
                mode="plan",
                state_version=1,
                policy_version="plan-v1",
            )
            db.add(plan_session)
            db.flush()
            principal = AuthPrincipal(user_id=user.id, device_id=device.id, session_id=auth.id)
            owners.append((principal, voice.id))
        db.commit()
    yield engine, owners
    engine.dispose()


@pytest.fixture
def tool_registry():
    registry = ToolRegistry()
    register_task_tools(registry)
    register_reminder_tools(registry)
    register_plan_tools(registry)
    return registry


@pytest.fixture
def tool_executor(tool_registry):
    return ToolExecutor(tool_registry, idempotency_store=InMemoryToolIdempotencyStore())


@pytest.fixture
def settings(storage):
    _, owners = storage
    test_user_ids = frozenset(principal.user_id for principal, _ in owners)
    return Settings(
        plan_mode_enabled=True,
        plan_extraction_mode="on",
        plan_auto_actions_enabled=True,
        plan_test_user_ids=test_user_ids,
        plan_max_actions_per_turn=8,
        plan_policy_version="plan-v1",
        voice_confirmation_ttl_seconds=300,
    )


# ---------------------------------------------------------------------------
# Item 1: Action-bound authorization grants & Normal-mode regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_action_bound_grant_authorizes_plan_execution(storage, tool_executor):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        action_id = uuid.uuid4()
        tool_name = "create_task"
        args = {"title": "Write unit tests", "due_at": "2026-10-07T23:59:00Z"}
        arg_digest = compute_argument_digest(args)

        grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            action_id=action_id,
            tool_name=tool_name,
            argument_digest=arg_digest,
            target_id=None,
            target_revision=None,
            mode_state_version=1,
            policy_version="plan-v1",
        )

        context = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write", "tasks:read"}),
            db=db,
            clock=SystemClock(),
            user_timezone="America/Los_Angeles",
            planning_grants=(grant,),
        )

        call = LLMToolCall(tool_call_id=f"plan_act_{action_id}", name=tool_name, arguments=args)
        result = await executor.execute(call, context=context)

        assert result.success is True
        assert result.executed is True
        res_data = json.loads(result.content)
        assert res_data["ok"] is True
        assert res_data["result"]["title"] == "Write unit tests"
        assert res_data["result"]["status"] == "pending"


@pytest.mark.asyncio
async def test_grant_verification_failures_fail_closed(storage, tool_executor):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        action_id = uuid.uuid4()
        tool_name = "create_task"
        args = {"title": "Write unit tests"}
        arg_digest = compute_argument_digest(args)

        # 1. Tampered / wrong argument digest
        tampered_grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            action_id=action_id,
            tool_name=tool_name,
            argument_digest="tampered_digest_value",
            mode_state_version=1,
            policy_version="plan-v1",
        )
        ctx = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(tampered_grant,),
        )
        call = LLMToolCall(tool_call_id=f"plan_act_{action_id}", name=tool_name, arguments=args)
        res = await executor.execute(call, context=ctx)
        assert res.success is False
        assert res.error_code == "llm_tool_confirmation_required"

        # 2. Expired grant
        expired_grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            action_id=action_id,
            tool_name=tool_name,
            argument_digest=arg_digest,
            mode_state_version=1,
            policy_version="plan-v1",
            expires_at_monotonic=0.0,  # in the past
        )
        ctx_exp = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(expired_grant,),
        )
        res_exp = await executor.execute(call, context=ctx_exp)
        assert res_exp.success is False
        assert res_exp.error_code == "llm_tool_confirmation_required"

        # 3. Mismatched session ID
        mismatched_session_grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=uuid.uuid4(),  # wrong session
            turn_id=turn_id,
            action_id=action_id,
            tool_name=tool_name,
            argument_digest=arg_digest,
            mode_state_version=1,
            policy_version="plan-v1",
        )
        ctx_sess = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(mismatched_session_grant,),
        )
        res_sess = await executor.execute(call, context=ctx_sess)
        assert res_sess.success is False
        assert res_sess.error_code == "llm_tool_confirmation_required"


@pytest.mark.asyncio
async def test_normal_mode_confirmation_regression(storage, tool_executor):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)

        # Normal mode execution context: NO planning_grants
        normal_context = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write", "tasks:read", "reminders:write", "plans:write"}),
            db=db,
            clock=SystemClock(),
            user_timezone="America/Los_Angeles",
            planning_grants=(),  # Normal mode
        )

        for tool_name, args in [
            ("create_task", {"title": "Normal task"}),
            ("create_reminder", {"title": "Normal reminder", "trigger_at": "2026-10-07T16:00:00Z"}),
            ("create_plan", {"name": "Normal plan"}),
            ("update_task", {"task_id": str(uuid.uuid4()), "title": "Updated"}),
            ("add_plan_context", {"plan_id": str(uuid.uuid4()), "content": "Note"}),
        ]:
            call = LLMToolCall(tool_call_id=f"call_{uuid.uuid4()}", name=tool_name, arguments=args)
            result = await executor.execute(call, context=normal_context)
            assert result.success is False
            assert result.error_code == "llm_tool_confirmation_required", f"{tool_name} must require confirmation"


# ---------------------------------------------------------------------------
# Item 2: Bounded planner budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_mode_turn_rate_limit_regression(storage, tool_executor):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)

        call1 = LLMToolCall(tool_call_id="call_1", name="create_task", arguments={"title": "Task 1"})
        call2 = LLMToolCall(tool_call_id="call_2", name="create_task", arguments={"title": "Task 2"})

        # Normal mode without planner_budget: create_task allows 1 call/turn
        normal_context = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            clock=SystemClock(),
            confirmed_tool_call_ids=frozenset({"call_1", "call_2"}),
            planner_budget=None,  # Normal mode
        )

        res1 = await executor.execute(call1, context=normal_context)
        assert res1.success is True

        res2 = await executor.execute(call2, context=normal_context)
        assert res2.success is False
        assert res2.error_code == "llm_tool_rate_limited"


@pytest.mark.asyncio
async def test_planner_budget_allows_multiple_actions_per_turn(storage, tool_executor):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        budget = PlannerBudget(max_total_actions=8)

        # Plan mode executes 6 tasks in one turn using grants and budget
        for i in range(6):
            action_id = uuid.uuid4()
            args = {"title": f"Task {i + 1}"}
            grant = PlanningAuthorization(
                user_id=principal.user_id,
                session_id=session_id,
                turn_id=turn_id,
                action_id=action_id,
                tool_name="create_task",
                argument_digest=compute_argument_digest(args),
                mode_state_version=1,
                policy_version="plan-v1",
            )
            ctx = ToolExecutionContext(
                user_id=principal.user_id,
                session_id=session_id,
                turn_id=turn_id,
                response_id=uuid.uuid4(),
                scopes=frozenset({"tasks:write"}),
                db=db,
                clock=SystemClock(),
                planning_grants=(grant,),
                planner_budget=budget,
            )
            call = LLMToolCall(tool_call_id=f"plan_act_{action_id}", name="create_task", arguments=args)
            res = await executor.execute(call, context=ctx)
            assert res.success is True, f"Action {i + 1} failed: {res.error_code}"

        assert budget.total_actions_executed == 6


# ---------------------------------------------------------------------------
# Item 3: Route plan/task/reminder/context writes through validated handlers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_tools_ownership_and_validation(storage, tool_executor):
    engine, owners = storage
    principal1, session_id1 = owners[0]
    principal2, session_id2 = owners[1]
    executor = tool_executor

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal1, session_id1)

        # 1. create_plan
        action_id = uuid.uuid4()
        plan_args = {"name": "XYZ Project", "goal": "Build MVP", "deadline_at": "2026-10-09T23:59:00Z"}
        grant = PlanningAuthorization(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            action_id=action_id,
            tool_name="create_plan",
            argument_digest=compute_argument_digest(plan_args),
            mode_state_version=1,
            policy_version="plan-v1",
        )
        ctx = ToolExecutionContext(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"plans:write", "plans:read"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(grant,),
        )
        call = LLMToolCall(tool_call_id=f"plan_act_{action_id}", name="create_plan", arguments=plan_args)
        res = await executor.execute(call, context=ctx)
        assert res.success is True
        res_data = json.loads(res.content)
        plan_id = uuid.UUID(res_data["result"]["plan_id"])
        await db.commit()

        # 2. add_plan_context (with deduplication)
        act2 = uuid.uuid4()
        ctx_args = {"plan_id": str(plan_id), "content": "FastAPI + PostgreSQL", "kind": "note"}
        grant2 = PlanningAuthorization(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            action_id=act2,
            tool_name="add_plan_context",
            argument_digest=compute_argument_digest(ctx_args),
            mode_state_version=1,
            policy_version="plan-v1",
            plan_id=plan_id,
        )
        ctx2 = ToolExecutionContext(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"plans:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(grant2,),
        )
        call2 = LLMToolCall(tool_call_id=f"plan_act_{act2}", name="add_plan_context", arguments=ctx_args)
        res2 = await executor.execute(call2, context=ctx2)
        assert res2.success is True
        assert json.loads(res2.content)["result"]["status"] == "created"
        await db.commit()

        # Duplicate context item returns status duplicate
        act3 = uuid.uuid4()
        grant3 = PlanningAuthorization(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            action_id=act3,
            tool_name="add_plan_context",
            argument_digest=compute_argument_digest(ctx_args),
            mode_state_version=1,
            policy_version="plan-v1",
            plan_id=plan_id,
        )
        ctx3 = ToolExecutionContext(
            user_id=principal1.user_id,
            session_id=session_id1,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"plans:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(grant3,),
        )
        call3 = LLMToolCall(tool_call_id=f"plan_act_{act3}", name="add_plan_context", arguments=ctx_args)
        res2_dup = await executor.execute(call3, context=ctx3)
        assert res2_dup.success is True
        assert json.loads(res2_dup.content)["result"]["status"] == "duplicate"

        # 3. Cross-user access rejected
        turn2 = await create_turn(db, principal2, session_id2)
        cross_ctx = ToolExecutionContext(
            user_id=principal2.user_id,  # Owner 2
            session_id=session_id2,
            turn_id=turn2,
            response_id=uuid.uuid4(),
            scopes=frozenset({"plans:read"}),
            db=db,
            clock=SystemClock(),
        )
        get_call = LLMToolCall(
            tool_call_id="get_call",
            name="get_plan",
            arguments={"plan_id": str(plan_id)},
        )
        cross_res = await executor.execute(get_call, context=cross_ctx)
        assert cross_res.success is False
        assert cross_res.error_code == "llm_tool_error"


# ---------------------------------------------------------------------------
# Item 4: Stable replay identity, semantic duplicate resolution, revisions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stable_replay_identity_returns_cached_result(storage, tool_registry):
    engine, owners = storage
    principal, session_id = owners[0]
    idempotency_store = InMemoryToolIdempotencyStore()
    executor = ToolExecutor(tool_registry, idempotency_store=idempotency_store)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        action_id = uuid.uuid4()
        stable_tool_call_id = f"plan_act_{action_id}"
        args = {"title": "Replay safe task"}
        grant = PlanningAuthorization(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            action_id=action_id,
            tool_name="create_task",
            argument_digest=compute_argument_digest(args),
            mode_state_version=1,
            policy_version="plan-v1",
        )
        ctx = ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            db=db,
            clock=SystemClock(),
            planning_grants=(grant,),
        )
        call = LLMToolCall(tool_call_id=stable_tool_call_id, name="create_task", arguments=args)

        # First execution
        res1 = await executor.execute(call, context=ctx)
        assert res1.success is True
        res1_data = json.loads(res1.content)
        task_id = res1_data["result"]["task_id"]
        await db.commit()

        # Replay with identical stable tool_call_id
        res2 = await executor.execute(call, context=ctx)
        assert res2.success is True
        res2_data = json.loads(res2.content)
        assert res2_data["result"]["task_id"] == task_id

        # Count tasks in DB: must be exactly 1!
        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        assert len(tasks) == 1


@pytest.mark.asyncio
async def test_semantic_duplicate_resolution(storage, tool_executor, settings):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    planning_executor = PlanningExecutor(settings, executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        # Create active plan and existing task in DB
        plan = Plan(user_id=principal.user_id, name="XYZ", status="active", revision=1)
        db.add(plan)
        await db.flush()
        existing_task = Task(
            user_id=principal.user_id,
            plan_id=plan.id,
            title="Setup FastAPI backend",
            status="pending",
            due_at=datetime(2026, 10, 7, 23, 59, tzinfo=UTC),
            revision=1,
        )
        db.add(existing_task)
        await db.commit()

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=plan.id,
            plans=(plan,),
            targets=(),
            context=(),
        )

        # Decision proposing duplicate task
        decision = make_decision(
            proposal=make_proposal("CREATE_TASK", "setup fastapi backend"),
            disposition="AUTO",
            reason="eligible",
            plan_id=plan.id,
            scheduled_at=datetime(2026, 10, 7, 23, 59, tzinfo=UTC),
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="setup fastapi backend",
            snapshot=snapshot,
            decisions=(decision,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.duplicate_actions) == 1
        assert len(receipt.saved_actions) == 0
        tasks = (await db.scalars(select(Task).where(Task.plan_id == plan.id))).all()
        assert len(tasks) == 1  # No second task created!


@pytest.mark.asyncio
async def test_target_revision_protection(storage, tool_executor, settings):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    planning_executor = PlanningExecutor(settings, executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        task = Task(user_id=principal.user_id, title="Original title", revision=5)
        db.add(task)
        await db.commit()

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        # Decision with stale revision (3 vs 5 in DB)
        decision = make_decision(
            proposal=make_proposal("UPDATE_TASK", "Stale update", classification="CORRECTION"),
            disposition="AUTO",
            reason="eligible",
            target_id=task.id,
            target_revision=3,  # Stale!
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Stale update",
            snapshot=snapshot,
            decisions=(decision,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.failed_actions) == 1
        assert "revision_conflict" in receipt.failed_actions[0]["error"]
        await db.refresh(task)
        assert task.title == "Original title"  # Unchanged!


# ---------------------------------------------------------------------------
# Item 5: Transaction group commits & partial outcomes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coupled_transaction_group_atomic_rollback(storage, tool_executor, settings):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    planning_executor = PlanningExecutor(settings, executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        # Coupled pair: Task + Reminder with matching titles
        # But reminder has trigger in the past, causing validation/execution failure
        dec_task = make_decision(
            proposal=make_proposal("CREATE_TASK", "Client presentation"),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=datetime(2026, 10, 9, 23, 59, tzinfo=UTC),
        )
        dec_reminder = make_decision(
            proposal=make_proposal("CREATE_REMINDER", "Client presentation", classification="REMINDER"),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=datetime(2020, 1, 1, 12, 0, tzinfo=UTC),  # Past trigger -> error!
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Client presentation",
            snapshot=snapshot,
            decisions=(dec_task, dec_reminder),
            user_timezone="America/Los_Angeles",
        )

        # Coupled group fails together
        assert len(receipt.failed_actions) == 2
        assert len(receipt.saved_actions) == 0

        # Neither task nor reminder was committed in DB
        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        reminders = (await db.scalars(select(Reminder).where(Reminder.user_id == principal.user_id))).all()
        assert len(tasks) == 0
        assert len(reminders) == 0


@pytest.mark.asyncio
async def test_independent_transaction_groups_partial_outcomes(storage, tool_executor, settings):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    planning_executor = PlanningExecutor(settings, executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        # Independent Action 1: Valid task
        dec1 = make_decision(
            proposal=make_proposal("CREATE_TASK", "Finish backend API"),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=datetime(2026, 10, 7, 23, 59, tzinfo=UTC),
        )
        # Independent Action 2: Failing reminder (past date)
        dec2 = make_decision(
            proposal=make_proposal("CREATE_REMINDER", "Unrelated reminder in past", classification="REMINDER"),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=datetime(2020, 1, 1, 12, 0, tzinfo=UTC),
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Finish backend API and unrelated reminder",
            snapshot=snapshot,
            decisions=(dec1, dec2),
            user_timezone="America/Los_Angeles",
        )

        # Independent group 1 succeeded; group 2 failed
        assert len(receipt.saved_actions) == 1
        assert receipt.saved_actions[0]["title"] == "Finish backend API"
        assert len(receipt.failed_actions) == 1
        assert receipt.failed_actions[0]["title"] == "Unrelated reminder in past"

        # Group 1 task is committed in DB
        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        assert len(tasks) == 1
        assert tasks[0].title == "Finish backend API"


# ---------------------------------------------------------------------------
# Item 6: Consequential operations queued through confirmation machinery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consequential_operations_never_execute_automatically(storage, tool_executor, settings):
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    confirmation_store = InMemoryVoiceConfirmationStore()
    planning_executor = PlanningExecutor(settings, executor, confirmation_service=confirmation_store)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        task = Task(user_id=principal.user_id, title="Critical task to delete", status="pending")
        db.add(task)
        await db.commit()

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        # Consequential proposal: DELETE_TASK
        dec_del = make_decision(
            proposal=make_proposal("DELETE_TASK", "Critical task to delete", classification="CORRECTION"),
            disposition="CONFIRM",
            reason="consequential",
            target_id=task.id,
            target_revision=1,
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Delete critical task",
            snapshot=snapshot,
            decisions=(dec_del,),
            user_timezone="America/Los_Angeles",
        )

        # Did NOT execute automatically!
        assert len(receipt.saved_actions) == 0
        assert len(receipt.pending_confirmations) == 1
        assert receipt.pending_confirmations[0]["operation"] == "DELETE_TASK"

        # Task in DB is NOT deleted!
        await db.refresh(task)
        assert task.status == "pending"

        # Confirmation queued in confirmation store
        scope = (principal.user_id, principal.device_id, session_id)
        pending = await confirmation_store.get(scope)
        assert pending is not None
        assert pending.tool_name == "delete_task"
        assert pending.validated_tool_arguments["task_id"] == str(task.id)


# ---------------------------------------------------------------------------
# PM-3 Gate: Enabled conversational turn multi-record execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pm3_gate_multi_record_conversational_turn(storage, tool_executor, settings):
    """GATE: An enabled conversational turn creates multiple intended records once,
    with correct per-action dates; consequential operations cannot execute automatically.
    """
    engine, owners = storage
    principal, session_id = owners[0]
    executor = tool_executor
    confirmation_store = InMemoryVoiceConfirmationStore()
    planning_executor = PlanningExecutor(settings, executor, confirmation_service=confirmation_store)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        # Multi-record turn:
        # 1. CREATE_PLAN "XYZ"
        # 2. CREATE_TASK "Initial backend" -> due tomorrow (2026-10-07 23:59:00 UTC)
        # 3. CREATE_TASK "Presentation and report" -> due Friday (2026-10-09 23:59:00 UTC)
        # 4. CREATE_REMINDER "Client meeting" -> Friday 4 PM (2026-10-09 23:00:00 UTC for 4 PM PDT)
        # 5. Consequential: DELETE_TASK (cannot execute automatically)

        dec_plan = make_decision(
            proposal=make_proposal("CREATE_PLAN", "XYZ", classification="PROJECT"),
            disposition="AUTO",
            reason="eligible",
            plan_ordinal=0,
        )
        dec_task1 = make_decision(
            proposal=make_proposal("CREATE_TASK", "Initial backend", classification="INTENTION"),
            disposition="AUTO",
            reason="eligible",
            plan_ordinal=0,
            scheduled_at=datetime(2026, 10, 7, 23, 59, tzinfo=UTC),
        )
        dec_task2 = make_decision(
            proposal=make_proposal("CREATE_TASK", "Presentation and report", classification="DEADLINE"),
            disposition="AUTO",
            reason="eligible",
            plan_ordinal=0,
            scheduled_at=datetime(2026, 10, 9, 23, 59, tzinfo=UTC),
        )
        dec_reminder = make_decision(
            proposal=make_proposal("CREATE_REMINDER", "Client meeting", classification="REMINDER"),
            disposition="AUTO",
            reason="eligible",
            plan_ordinal=0,
            scheduled_at=datetime(2026, 10, 9, 23, 0, tzinfo=UTC),
        )
        dec_delete = make_decision(
            proposal=make_proposal("DELETE_TASK", "Old unused task", classification="CORRECTION"),
            disposition="CONFIRM",
            reason="consequential",
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript=(
                "We're starting XYZ. I need the initial backend running tomorrow. "
                "The presentation and report must be ready Friday. "
                "We have a client meeting Friday at 4 PM. Delete old unused task."
            ),
            snapshot=snapshot,
            decisions=(dec_plan, dec_task1, dec_task2, dec_reminder, dec_delete),
            user_timezone="America/Los_Angeles",
        )

        # 1. 4 actions saved: 1 plan, 2 tasks, 1 reminder
        assert len(receipt.saved_actions) == 4
        assert receipt.plan_id is not None
        assert receipt.plan_name == "XYZ"

        # 2. 1 consequential action queued for confirmation, NOT executed
        assert len(receipt.pending_confirmations) == 1
        assert receipt.pending_confirmations[0]["operation"] == "DELETE_TASK"

        # 3. Check records in database
        saved_plan = await db.scalar(select(Plan).where(Plan.id == receipt.plan_id))
        assert saved_plan is not None
        assert saved_plan.name == "XYZ"

        tasks = (
            await db.scalars(
                select(Task).where(Task.plan_id == receipt.plan_id).order_by(Task.due_at)
            )
        ).all()
        assert len(tasks) == 2
        assert tasks[0].title == "Initial backend"
        assert tasks[0].due_at.replace(tzinfo=UTC) == datetime(2026, 10, 7, 23, 59, tzinfo=UTC)  # Tomorrow
        assert tasks[1].title == "Presentation and report"
        assert tasks[1].due_at.replace(tzinfo=UTC) == datetime(2026, 10, 9, 23, 59, tzinfo=UTC)  # Friday

        reminders = (
            await db.scalars(select(Reminder).where(Reminder.plan_id == receipt.plan_id))
        ).all()
        assert len(reminders) == 1
        assert reminders[0].title == "Client meeting"
        assert reminders[0].trigger_at.replace(tzinfo=UTC) == datetime(2026, 10, 9, 23, 0, tzinfo=UTC)  # Friday 4 PM

        # 4. Truthful grounded receipt text summary
        assert "Created the 'XYZ' plan" in receipt.text_summary
        assert "Added 2 tasks" in receipt.text_summary
        assert "Scheduled reminder 'Client meeting'" in receipt.text_summary
        assert "require confirmation" in receipt.text_summary


# ---------------------------------------------------------------------------
# Slice 3: Plan clock-timed obligations notify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_clock_timed_obligation_creates_task_and_linked_reminder(
    storage, tool_executor, settings
) -> None:
    engine, owners = storage
    principal, session_id = owners[0]
    planning_executor = PlanningExecutor(settings, tool_executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        clock_due = datetime(2026, 10, 6, 22, 13, tzinfo=UTC)
        dec_task = make_decision(
            proposal=make_proposal(
                "CREATE_TASK",
                "Call Harsh",
                text="I need to call Harsh at 3:13 PM",
                temporal_text="at 3:13 PM",
            ),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=clock_due,
        )
        dec_task = replace(dec_task, has_clock=True)

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="I need to call Harsh at 3:13 PM",
            snapshot=snapshot,
            decisions=(dec_task,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 1
        assert len(receipt.failed_actions) == 0

        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        assert len(tasks) == 1
        assert tasks[0].title == "Call Harsh"
        assert tasks[0].due_at.replace(tzinfo=UTC) == clock_due

        reminders = (
            await db.scalars(select(Reminder).where(Reminder.user_id == principal.user_id))
        ).all()
        assert len(reminders) == 1
        assert reminders[0].task_id == tasks[0].id
        assert reminders[0].title == "Call Harsh"
        assert reminders[0].trigger_at.replace(tzinfo=UTC) == clock_due
        assert reminders[0].status == "scheduled"
        assert reminders[0].delivery_channel == "push"


@pytest.mark.asyncio
async def test_plan_date_only_obligation_creates_task_only_no_reminder(
    storage, tool_executor, settings
) -> None:
    engine, owners = storage
    principal, session_id = owners[0]
    planning_executor = PlanningExecutor(settings, tool_executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        eod_due = datetime(2026, 10, 9, 23, 59, tzinfo=UTC)
        dec_task = make_decision(
            proposal=make_proposal(
                "CREATE_TASK",
                "Presentation and report",
                text="The presentation and report must be ready Friday",
                temporal_text="Friday",
            ),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=eod_due,
        )
        dec_task = replace(dec_task, has_clock=False)

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="The presentation and report must be ready Friday",
            snapshot=snapshot,
            decisions=(dec_task,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 1

        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        assert len(tasks) == 1
        assert tasks[0].title == "Presentation and report"

        reminders = (
            await db.scalars(select(Reminder).where(Reminder.user_id == principal.user_id))
        ).all()
        assert len(reminders) == 0


@pytest.mark.asyncio
async def test_plan_coupled_task_and_reminder_upserts_no_duplicate(
    storage, tool_executor, settings
) -> None:
    engine, owners = storage
    principal, session_id = owners[0]
    planning_executor = PlanningExecutor(settings, tool_executor)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        snapshot = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        meeting_time = datetime(2026, 10, 9, 23, 0, tzinfo=UTC)
        dec_task = make_decision(
            proposal=make_proposal(
                "CREATE_TASK",
                "Client meeting",
                text="Client meeting Friday at 4 PM",
                temporal_text="Friday at 4 PM",
            ),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=meeting_time,
        )
        dec_task = replace(dec_task, has_clock=True)

        dec_reminder = make_decision(
            proposal=make_proposal(
                "CREATE_REMINDER",
                "Client meeting",
                text="Client meeting Friday at 4 PM",
                classification="REMINDER",
                temporal_text="Friday at 4 PM",
            ),
            disposition="AUTO",
            reason="eligible",
            scheduled_at=meeting_time,
        )
        dec_reminder = replace(dec_reminder, has_clock=True)

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Client meeting Friday at 4 PM",
            snapshot=snapshot,
            decisions=(dec_task, dec_reminder),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 2
        assert len(receipt.duplicate_actions) == 0

        tasks = (await db.scalars(select(Task).where(Task.user_id == principal.user_id))).all()
        reminders = (
            await db.scalars(select(Reminder).where(Reminder.user_id == principal.user_id))
        ).all()
        assert len(tasks) == 1
        assert len(reminders) == 1
        assert reminders[0].task_id == tasks[0].id
        assert reminders[0].title == "Client meeting"
        assert reminders[0].trigger_at.replace(tzinfo=UTC) == meeting_time


def test_resolution_attaches_has_clock_correctly() -> None:
    from app.planning.resolution import validate
    from app.planning.types import Envelope

    snapshot = PlanningSnapshot(
        consent=PlanningConsent(uuid.uuid4(), uuid.uuid4(), True, True, "plan", 1, 1),
        now_utc=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        timezone="America/Los_Angeles",
    )

    # 1. "at 3:13 PM" -> has_clock is True
    p1 = make_proposal(
        "CREATE_TASK",
        "Call Harsh",
        text="I need to call Harsh at 3:13 PM",
        temporal_text="at 3:13 PM",
    )
    dec1 = validate(Envelope(actions=[p1]), "I need to call Harsh at 3:13 PM", snapshot)
    assert len(dec1) == 1
    assert dec1[0].has_clock is True

    # 2. "tomorrow" -> has_clock is False
    p2 = make_proposal(
        "CREATE_TASK",
        "Draft report",
        text="Draft report tomorrow",
        temporal_text="tomorrow",
    )
    dec2 = validate(Envelope(actions=[p2]), "Draft report tomorrow", snapshot)
    assert len(dec2) == 1
    assert dec2[0].has_clock is False

    # 3. "tomorrow at 23:59" -> has_clock is False (23:59 EOD is not treated as clock)
    p3 = make_proposal(
        "CREATE_TASK",
        "EOD deadline",
        text="EOD deadline tomorrow at 23:59",
        temporal_text="tomorrow at 23:59",
    )
    dec3 = validate(Envelope(actions=[p3]), "EOD deadline tomorrow at 23:59", snapshot)
    assert len(dec3) == 1
    assert dec3[0].has_clock is False

    # 4. "in 15 minutes" -> has_clock is True
    p4 = make_proposal(
        "CREATE_TASK",
        "Check oven",
        text="I will check oven in 15 minutes",
        temporal_text="in 15 minutes",
    )
    dec4 = validate(Envelope(actions=[p4]), "I will check oven in 15 minutes", snapshot)
    assert len(dec4) == 1
    assert dec4[0].has_clock is True
