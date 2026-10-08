from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    MetaData,
    StaticPool,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.core.clock import SystemClock
from app.core.config import Settings
from app.llm.errors import LLMToolError
from app.llm.plan_tools import register_plan_tools
from app.llm.reminder_tools import register_reminder_tools
from app.llm.task_tools import register_task_tools
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from app.models import (
    AuthSession,
    ConversationTurn,
    Device,
    MemoryChunk,
    MemoryItem,
    MemoryJob,
    Message,
    OkfSyncJob,
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
from app.planning.context_promotion import (
    MemoryPromotionError,
    promote_plan_context_to_memory,
)
from app.planning.executor import PlanningExecutor
from app.planning.policy import PlanningConsent
from app.planning.resolution import validate
from app.planning.service import (
    PlanningError,
    cancel_pending,
    change_state,
    execution_barrier,
    recognize_plan_selection,
    resolve_plan_selection,
)
from app.planning.types import (
    Decision,
    Envelope,
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
    target_mention: str | None = None,
    content: str | None = None,
    start_offset: int = 0,
) -> Proposal:
    src_text = text or title
    temporal = None
    if temporal_text and temporal_text in src_text:
        rel_start = src_text.index(temporal_text)
        temporal = Span(
            start=start_offset + rel_start, length=len(temporal_text), text=temporal_text
        )
    elif temporal_text:
        temporal = Span(start=start_offset, length=len(temporal_text), text=temporal_text)
    return Proposal(
        operation=operation,
        classification=classification,
        title=title,
        actor=actor,
        source=Span(start=start_offset, length=len(src_text), text=src_text),
        temporal=temporal,
        target_mention=target_mention,
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

    async def delete(self, row):
        self.sync.delete(row)


async def create_turn(
    db: AsyncDB, principal: AuthPrincipal, session_id: uuid.UUID, turn_number: int | None = None
) -> uuid.UUID:
    turn_id = uuid.uuid4()
    if turn_number is None:
        existing = db.sync.scalars(
            select(ConversationTurn.turn_number).where(ConversationTurn.session_id == session_id)
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

    def on_connect(conn, _):
        conn.create_function("btrim", 1, lambda s: s.strip() if s is not None else "")
        conn.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", on_connect)
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
        MemoryItem,
        MemoryChunk,
        MemoryJob,
        OkfSyncJob,
    ]
    for model in models:
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
        # Adapt PostgreSQL TSVECTOR and computed fields for SQLite
        if "search_tsv" in table.c:
            table.c.search_tsv.type = Text()
            table.c.search_tsv.computed = None
            table.c.search_tsv.server_default = None
            table.c.search_tsv.nullable = True
        if "embedding" in table.c:
            table.c.embedding.type = JSON()
        for constraint in list(table.constraints):
            if isinstance(constraint, CheckConstraint) and "btrim" in str(constraint.sqltext):
                table.constraints.remove(constraint)
            elif (
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
                email=f"owner{number}@test.local",
                password_hash="test",
                memory_enabled=True,
            )
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
                client_metadata={},
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
            owners.append(
                (
                    AuthPrincipal(
                        user_id=user.id,
                        session_id=auth.id,
                        device_id=device.id,
                    ),
                    voice.id,
                )
            )
        db.commit()
    return engine, owners


@pytest.fixture
def tool_executor(storage):
    engine, _ = storage
    registry = ToolRegistry()
    register_task_tools(registry)
    register_reminder_tools(registry)
    register_plan_tools(registry)
    return ToolExecutor(registry=registry, idempotency_store=InMemoryToolIdempotencyStore())


@pytest.fixture
def settings(storage):
    _, owners = storage
    test_user_ids = frozenset(principal.user_id for principal, _ in owners)
    return Settings(
        plan_mode_enabled=True,
        plan_extraction_mode="on",
        plan_auto_actions_enabled=True,
        plan_test_user_ids=test_user_ids,
        plan_execution_mode="execute",
        plan_confirmation_mode="voice",
        memory_write_enabled=True,
        stt_api_key="audit-synthetic-memory-key",
        embedding_api_url="https://memory.invalid/v1/embeddings",
        voice_confirmation_ttl_seconds=300,
        voice_default_timezone="America/Los_Angeles",
    )


# ---------------------------------------------------------------------------
# 1. Pronoun Resolution Against Owned Structured State
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pronoun_resolution_against_recent_receipt(storage, tool_executor, settings):
    """Pronoun 'that' resolves to the task created in previous turn via recent_receipt."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn1_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        # Prior turn created a task
        task = Task(
            user_id=principal.user_id,
            title="prepare financial slides",
            due_at=now_utc + timedelta(days=1),
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        recent_receipt = PlanningReceipt(
            batch_id=uuid.uuid4(),
            saved_actions=({"id": task.id, "action": "CREATE_TASK", "title": task.title},),
            has_changes=True,
        )

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
            targets=(Target(task.id, "task", task.title, None, task.revision, task.due_at),),
            context=(),
            recent_receipt=recent_receipt,
        )

        # Turn 2: "move that to Friday at 3pm"
        transcript = "Move that to Friday at 3pm"
        envelope = Envelope(
            actions=[
                make_proposal(
                    operation="UPDATE_TASK",
                    title="Move that",
                    text=transcript,
                    temporal_text="Friday at 3pm",
                    target_mention="that",
                )
            ]
        )

        decisions = validate(envelope, transcript, snapshot)
        assert len(decisions) == 1
        d = decisions[0]
        assert d.disposition == "AUTO"
        assert d.target_id == task.id
        assert d.target_revision == task.revision

        planning_executor = PlanningExecutor(settings, tool_executor)
        turn2_id = await create_turn(db, principal, session_id)
        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn2_id,
            response_id=uuid.uuid4(),
            transcript=transcript,
            snapshot=snapshot,
            decisions=decisions,
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 1
        assert receipt.saved_actions[0]["action"] == "UPDATE_TASK"

        await db.refresh(task)
        # Task was rescheduled!
        assert task.due_at.replace(tzinfo=UTC) == d.scheduled_at


@pytest.mark.asyncio
async def test_pronoun_resolution_single_scoped_candidate(storage, tool_executor, settings):
    """Pronoun 'it' resolves to a single candidate task when unambiguous."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        task = Task(
            user_id=principal.user_id,
            title="review contract draft",
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

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
            targets=(Target(task.id, "task", task.title, None, task.revision, None),),
            context=(),
            recent_receipt=None,  # No recent receipt, but only 1 candidate
        )

        transcript = "Mark it done"
        envelope = Envelope(
            actions=[
                make_proposal(
                    operation="COMPLETE_TASK",
                    title="Mark it done",
                    text=transcript,
                    target_mention="it",
                )
            ]
        )

        decisions = validate(envelope, transcript, snapshot)
        assert len(decisions) == 1
        d = decisions[0]
        assert d.disposition == "AUTO"
        assert d.target_id == task.id

        planning_executor = PlanningExecutor(settings, tool_executor)
        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript=transcript,
            snapshot=snapshot,
            decisions=decisions,
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 1
        await db.refresh(task)
        assert task.status == "completed"


@pytest.mark.asyncio
async def test_pronoun_resolution_ambiguous_targets_clarifies(storage, settings):
    """Multiple candidate tasks without a distinct receipt result in CLARIFY(ambiguous_target)."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        task1 = Task(user_id=principal.user_id, title="task alpha", status="pending")
        task2 = Task(user_id=principal.user_id, title="task beta", status="pending")
        db.add(task1)
        db.add(task2)
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
            targets=(
                Target(task1.id, "task", task1.title, None, 1),
                Target(task2.id, "task", task2.title, None, 1),
            ),
            context=(),
            recent_receipt=None,
        )

        transcript = "Move that to Thursday at 2pm"
        envelope = Envelope(
            actions=[
                make_proposal(
                    operation="UPDATE_TASK",
                    title="Move that",
                    text=transcript,
                    temporal_text="Thursday at 2pm",
                    target_mention="that",
                )
            ]
        )

        decisions = validate(envelope, transcript, snapshot)
        assert len(decisions) == 1
        assert decisions[0].disposition == "CLARIFY"
        assert decisions[0].reason == "ambiguous_target"


# ---------------------------------------------------------------------------
# 2. Explicit Plan Switches Against Owned Structured State
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_plan_switches_and_ownership(storage):
    """Spoken plan switches recognize and select only owned active plans."""
    engine, owners = storage
    principal_a, session_a = owners[0]
    principal_b, session_b = owners[1]

    # Voice phrase recognition tests
    assert recognize_plan_selection("switch to project Beta") == "Beta"
    assert recognize_plan_selection("switch to plan Website Redesign") == "Website Redesign"
    assert recognize_plan_selection("focus on project Apollo") == "Apollo"
    assert recognize_plan_selection("select plan Launch") == "Launch"
    assert recognize_plan_selection("clear active plan") == ""
    assert recognize_plan_selection("deselect plan") == ""
    assert recognize_plan_selection("switch to plan mode") is None  # Mode control, not plan switch

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)

        # User A owns Plan Alpha and Beta
        plan_alpha = Plan(user_id=principal_a.user_id, name="Plan Alpha", status="active")
        plan_beta = Plan(user_id=principal_a.user_id, name="Project Beta", status="active")
        db.add(plan_alpha)
        db.add(plan_beta)

        # User B owns Plan Gamma
        plan_gamma = Plan(user_id=principal_b.user_id, name="Project Gamma", status="active")
        db.add(plan_gamma)
        await db.commit()

        # User A resolves Beta
        resolved_id = await resolve_plan_selection(db, principal_a.user_id, "Project Beta")
        assert resolved_id == plan_beta.id

        # User A switches state to Beta
        state_snap = await change_state(
            db,
            principal_a,
            session_a,
            expected_version=1,
            enabled=True,
            plan_id=plan_beta.id,
            select_plan=True,
        )
        assert state_snap["active_plan_id"] == plan_beta.id
        assert state_snap["active_plan_name"] == "Project Beta"

        # User A clearing plan
        state_snap2 = await change_state(
            db,
            principal_a,
            session_a,
            expected_version=2,
            enabled=True,
            plan_id=None,
            select_plan=True,
        )
        assert state_snap2["active_plan_id"] is None

        # User A CANNOT select User B's plan
        with pytest.raises(PlanningError, match="plan_not_found"):
            await resolve_plan_selection(db, principal_a.user_id, "Project Gamma")


# ---------------------------------------------------------------------------
# 3. Duplicate Paraphrases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_paraphrases(storage):
    """Paraphrases of existing or same-turn tasks resolve to duplicate (NO_ACTION)."""
    engine, owners = storage
    principal, session_id = owners[0]
    now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        # Resolve time for tomorrow at 12pm
        dummy_prop = make_proposal(
            operation="CREATE_TASK",
            title="prepare slides",
            text="I need to prepare the slides tomorrow at 12pm",
            temporal_text="tomorrow at 12pm",
        )
        dummy_snap = PlanningSnapshot(
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
        from app.planning.resolution import resolve_time

        target_due, _, _ = resolve_time(dummy_prop, dummy_snap)

        task = Task(
            user_id=principal.user_id,
            title="prepare slides",
            due_at=target_due,
            status="pending",
        )
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
            targets=(Target(task.id, "task", task.title, None, 1, task.due_at),),
            context=(),
        )

        # Paraphrase against existing target: "prepare the slides by tomorrow"
        transcript = "I need to prepare the slides tomorrow at 12pm"
        envelope = Envelope(
            actions=[
                make_proposal(
                    operation="CREATE_TASK",
                    title="prepare slides",
                    text=transcript,
                    temporal_text="tomorrow at 12pm",
                )
            ]
        )

        decisions = validate(envelope, transcript, snapshot)
        assert len(decisions) == 1
        assert decisions[0].disposition == "NO_ACTION"
        assert decisions[0].reason == "duplicate"
        assert decisions[0].target_id == task.id

        # Same-turn paraphrase
        part1 = "I will prepare slides tomorrow at 12pm"
        sep = " and "
        part2 = "I will prepare the slides tomorrow at 12pm"
        st_transcript = part1 + sep + part2
        st_envelope = Envelope(
            actions=[
                make_proposal(
                    operation="CREATE_TASK",
                    title="prepare slides",
                    text=part1,
                    temporal_text="tomorrow at 12pm",
                    start_offset=0,
                ),
                make_proposal(
                    operation="CREATE_TASK",
                    title="prepare the slides",
                    text=part2,
                    temporal_text="tomorrow at 12pm",
                    start_offset=len(part1) + len(sep),
                ),
            ]
        )
        st_decisions = validate(st_envelope, st_transcript, snapshot)
        # Both are recognized as duplicate
        for d in st_decisions:
            assert d.disposition == "NO_ACTION"
            assert d.reason == "duplicate"


# ---------------------------------------------------------------------------
# 4. Context Promotion Connected to Memory Consent and Privacy Policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_promotion_respects_memory_consent_and_write_policy(storage, settings):
    """Context promotion requires user consent, write toggle, and non-private session."""
    engine, owners = storage
    principal_a, session_a = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        plan = Plan(user_id=principal_a.user_id, name="Architecture Plan", status="active")
        db.add(plan)
        await db.commit()

        note = PlanContextItem(
            user_id=principal_a.user_id,
            plan_id=plan.id,
            content="Team uses Python 3.12 for microservices.",
            dedupe_key="python-312-note",
            status="active",
        )
        db.add(note)
        await db.commit()

        # 1. Allowed promotion when memory_write_enabled=True and user.memory_enabled=True
        mem_item, created = await promote_plan_context_to_memory(
            db=db,
            settings=settings,
            principal=principal_a,
            plan_id=plan.id,
            context_item_id=note.id,
            source_session_id=session_a,
        )
        assert created is True
        assert mem_item.content == note.content
        assert mem_item.user_id == principal_a.user_id

        # 2. Refused when settings.memory_write_enabled = False
        disabled_settings = settings.model_copy(update={"memory_write_enabled": False})
        with pytest.raises(MemoryPromotionError, match="memory_writes_disabled"):
            await promote_plan_context_to_memory(
                db=db,
                settings=disabled_settings,
                principal=principal_a,
                plan_id=plan.id,
                context_item_id=note.id,
                source_session_id=session_a,
            )

        # 3. Refused when user.memory_enabled = False
        user_a = await db.scalar(select(User).where(User.id == principal_a.user_id))
        user_a.memory_enabled = False
        await db.commit()

        with pytest.raises(MemoryPromotionError, match="user_memory_disabled"):
            await promote_plan_context_to_memory(
                db=db,
                settings=settings,
                principal=principal_a,
                plan_id=plan.id,
                context_item_id=note.id,
                source_session_id=session_a,
            )

        # Restore user memory setting
        user_a.memory_enabled = True
        await db.commit()

        # 4. Refused in private / memory_excluded session
        voice_sess = await db.scalar(select(VoiceSession).where(VoiceSession.id == session_a))
        voice_sess.client_metadata = {"memory_excluded": True}
        await db.commit()

        with pytest.raises(MemoryPromotionError, match="memory_excluded_session"):
            await promote_plan_context_to_memory(
                db=db,
                settings=settings,
                principal=principal_a,
                plan_id=plan.id,
                context_item_id=note.id,
                source_session_id=session_a,
            )

        # Plan note in DB remains intact throughout all checks
        await db.refresh(note)
        assert note.status == "active"


# ---------------------------------------------------------------------------
# 5. Private / Excluded Session Suppression & Mode Disable Race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_session_suppresses_plan_mode(storage):
    """A private session cannot enable Plan Mode."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        voice = await db.scalar(select(VoiceSession).where(VoiceSession.id == session_id))
        voice.client_metadata = {"memory_excluded": True}
        await db.commit()

        # Switching to plan mode in excluded session must fail
        with pytest.raises(PlanningError, match="planning_private_session"):
            await change_state(
                db,
                principal,
                session_id,
                expected_version=1,
                enabled=True,
                mode="plan",
            )


@pytest.mark.asyncio
async def test_mode_disable_race_barrier(storage):
    """Concurrent mode disable increments state_version and causes in-flight execution to fail closed."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)

        # Initial state version is 1, mode is plan
        async with execution_barrier(db, principal, session_id, expected_version=1) as state:
            assert state.mode == "plan"

        # Concurrently disable plan mode (increments version to 2, mode to normal)
        await change_state(
            db,
            principal,
            session_id,
            expected_version=1,
            enabled=True,
            mode="normal",
        )

        # An in-flight executor expecting version 1 fails closed!
        with pytest.raises(PlanningError, match="planning_consent_revoked"):
            async with execution_barrier(db, principal, session_id, expected_version=1):
                pass


# ---------------------------------------------------------------------------
# 6. Source Purge Preserves Structured Records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_purge_preserves_plans_and_tasks(storage):
    """Purging conversation turns sets source_turn_id to NULL without cascading deletion to plans/tasks."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)

        plan = Plan(user_id=principal.user_id, name="Project Alpha", source_turn_id=turn_id)
        db.add(plan)
        await db.flush()
        task = Task(user_id=principal.user_id, title="task 1", source_turn_id=turn_id)
        db.add(task)
        context_item = PlanContextItem(
            user_id=principal.user_id,
            plan_id=plan.id,
            content="Architecture note",
            dedupe_key="arch-note-1",
            source_turn_id=turn_id,
        )
        db.add(context_item)
        await db.commit()

        # Delete the turn (history purge)
        turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id))
        await db.delete(turn)
        await db.commit()

        # Plan, task, and context item are still in the database!
        await db.refresh(plan)
        await db.refresh(task)
        await db.refresh(context_item)

        assert plan.source_turn_id is None
        assert task.source_turn_id is None
        assert context_item.source_turn_id is None
        assert plan.name == "Project Alpha"
        assert task.title == "task 1"


# ---------------------------------------------------------------------------
# 7. Cancellation Before vs After Commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_before_and_after_commit(storage):
    """Rollback before commit aborts records; cancel_pending after commit preserves completed actions."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)

        # 1. Before commit: rollback
        task = Task(user_id=principal.user_id, title="Uncommitted Task")
        db.add(task)
        await db.rollback()

        found = await db.scalar(select(Task).where(Task.title == "Uncommitted Task"))
        assert found is None

        # 2. After commit: cancel_pending cancels pending batches/actions, but completed tasks/actions stay completed
        batch_turn_id = await create_turn(db, principal, session_id)
        batch = PlanningBatch(
            id=uuid.uuid4(),
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=batch_turn_id,
            state_version=1,
            extractor_version="v1",
            policy_version="plan-v1",
            source_digest="digest-123",
            status="pending",
        )
        db.add(batch)
        await db.flush()
        act_completed = PlanningAction(
            id=uuid.uuid4(),
            batch_id=batch.id,
            user_id=principal.user_id,
            ordinal=0,
            action_type="CREATE_TASK",
            payload_json={},
            payload_digest="d1",
            source_spans_json=[],
            disposition="AUTO",
            status="completed",
        )
        act_pending = PlanningAction(
            id=uuid.uuid4(),
            batch_id=batch.id,
            user_id=principal.user_id,
            ordinal=1,
            action_type="CREATE_TASK",
            payload_json={},
            payload_digest="d2",
            source_spans_json=[],
            disposition="AUTO",
            status="pending",
        )
        db.add(act_completed)
        db.add(act_pending)
        await db.commit()

        await cancel_pending(db, principal.user_id, session_id)
        await db.commit()

        await db.refresh(act_completed)
        await db.refresh(act_pending)
        assert act_completed.status == "completed"
        assert act_pending.status == "cancelled"
        assert act_pending.reason == "consent_revoked"


# ---------------------------------------------------------------------------
# 8. Replay After Midnight Preserves Stable Scheduled Dates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_after_midnight_preserves_scheduled_time(storage, tool_executor, settings):
    """Replaying an action after midnight uses the frozen scheduled_at, preventing calendar drift."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)

        now_utc = datetime.now(UTC)
        target_due = now_utc + timedelta(days=2)

        dec = make_decision(
            proposal=make_proposal(
                "CREATE_TASK", "deploy release", text="deploy release tomorrow at 5pm"
            ),
            disposition="AUTO",
            scheduled_at=target_due,
        )

        snapshot_tuesday_morning = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal.user_id,
                session_id=session_id,
                authenticated=True,
                session_active=True,
                mode="plan",
                state_version=1,
                expected_state_version=1,
            ),
            now_utc=now_utc + timedelta(hours=2),
            timezone="America/Los_Angeles",
            active_plan_id=None,
            plans=(),
            targets=(),
            context=(),
        )

        planning_executor = PlanningExecutor(settings, tool_executor)
        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="deploy release tomorrow at 5pm",
            snapshot=snapshot_tuesday_morning,
            decisions=(dec,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.saved_actions) == 1
        created_task = await db.scalar(
            select(Task).where(Task.id == receipt.saved_actions[0]["id"])
        )
        # Due at is preserved as Tuesday 17:00, not shifted to Wednesday!
        assert created_task.due_at.replace(tzinfo=UTC) == target_due


# ---------------------------------------------------------------------------
# 9. Reminder Occurrence / Delivery Identity Preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_update_preserves_delivery_and_occurrence_identity(storage, tool_executor):
    """Updating reminder title/body preserves reminder.id, delivery_id, and occurrence_count."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        trigger_time = datetime(2026, 10, 7, 15, 0, tzinfo=UTC)

        orig_delivery_id = str(uuid.uuid4())
        reminder = Reminder(
            user_id=principal.user_id,
            title="Old reminder title",
            trigger_at=trigger_time,
            local_trigger_at=trigger_time,
            timezone="America/Los_Angeles",
            delivery_id=orig_delivery_id,
            occurrence_count=5,  # Has already fired 5 occurrences
            status="scheduled",
        )
        db.add(reminder)
        await db.commit()
        await db.refresh(reminder)

        reminder_id = reminder.id

        context = ToolExecutionContext(
            db=db,
            user_id=principal.user_id,
            turn_id=turn_id,
            session_id=session_id,
            response_id=uuid.uuid4(),
            user_timezone="America/Los_Angeles",
            clock=SystemClock(),
            source_transcript="Update reminder title",
        )

        from app.llm.reminder_tools import UpdateReminderArguments, update_reminder_handler

        result = await update_reminder_handler(
            context,
            UpdateReminderArguments(
                reminder_id=reminder_id,
                title="New reminder title",
                expected_revision=reminder.revision,
            ),
        )

        await db.refresh(reminder)
        # ID is intact
        assert reminder.id == reminder_id
        # Delivery ID and occurrence count are intact!
        assert reminder.delivery_id == orig_delivery_id
        assert reminder.occurrence_count == 5
        assert reminder.title == "New reminder title"


# ---------------------------------------------------------------------------
# 10. Confirmed Archive Plan & Stale Undo Protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_archive_plan_semantics(storage, tool_executor, settings):
    """ARCHIVE_PLAN queues confirmation and when confirmed archives plan without deleting tasks."""
    engine, owners = storage
    principal, session_id = owners[0]
    confirmation_store = InMemoryVoiceConfirmationStore()
    planning_executor = PlanningExecutor(
        settings, tool_executor, confirmation_service=confirmation_store
    )

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        plan = Plan(user_id=principal.user_id, name="Project to Archive", status="active")
        db.add(plan)
        await db.flush()

        task = Task(
            user_id=principal.user_id, plan_id=plan.id, title="Linked task", status="pending"
        )
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
            active_plan_id=plan.id,
            plans=(Target(plan.id, "plan", plan.name, plan.id, plan.revision),),
            targets=(),
            context=(),
        )

        dec_archive = make_decision(
            proposal=make_proposal(
                "ARCHIVE_PLAN", "Archive project to archive", classification="CORRECTION"
            ),
            disposition="CONFIRM",
            reason="consequential",
            target_id=plan.id,
            target_revision=plan.revision,
        )

        receipt = await planning_executor.execute_batch(
            db=db,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            transcript="Archive project to archive",
            snapshot=snapshot,
            decisions=(dec_archive,),
            user_timezone="America/Los_Angeles",
        )

        assert len(receipt.pending_confirmations) == 1
        assert receipt.pending_confirmations[0]["operation"] == "ARCHIVE_PLAN"

        scope = (principal.user_id, principal.device_id, session_id)
        pending = await confirmation_store.get(scope)
        assert pending is not None
        assert pending.tool_name == "update_plan"
        assert pending.validated_tool_arguments["status"] == "archived"
        assert pending.validated_tool_arguments["plan_id"] == str(plan.id)

        # Execute confirmed tool
        from app.llm.plan_tools import UpdatePlanArguments, update_plan_handler

        ctx = ToolExecutionContext(
            db=db,
            user_id=principal.user_id,
            turn_id=turn_id,
            session_id=session_id,
            response_id=uuid.uuid4(),
            user_timezone="America/Los_Angeles",
            clock=SystemClock(),
            source_transcript="yes confirm",
        )
        await update_plan_handler(
            ctx,
            UpdatePlanArguments(
                plan_id=plan.id,
                status="archived",
                expected_revision=plan.revision,
            ),
        )
        await db.commit()

        await db.refresh(plan)
        await db.refresh(task)
        # Plan is archived, but linked task is intact!
        assert plan.status == "archived"
        assert task.status == "pending"
        assert task.plan_id == plan.id


@pytest.mark.asyncio
async def test_stale_undo_fails_with_revision_conflict(storage):
    """An undo operation carrying a stale target_revision is blocked from overwriting user edits."""
    engine, owners = storage
    principal, session_id = owners[0]

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        turn_id = await create_turn(db, principal, session_id)

        # Task starts at revision 1
        task = Task(user_id=principal.user_id, title="Initial title", status="pending")
        db.add(task)
        await db.commit()
        await db.refresh(task)
        assert task.revision == 1

        # User edits task -> revision becomes 2
        task.title = "User edited title"
        await db.commit()
        await db.refresh(task)
        assert task.revision == 2

        ctx = ToolExecutionContext(
            db=db,
            user_id=principal.user_id,
            turn_id=turn_id,
            session_id=session_id,
            response_id=uuid.uuid4(),
            user_timezone="America/Los_Angeles",
            clock=SystemClock(),
            source_transcript="undo that",
        )

        from app.llm.task_tools import UpdateTaskArguments, update_task_handler

        # A stale undo operation generated against revision 1
        with pytest.raises(LLMToolError, match="revision_conflict"):
            await update_task_handler(
                ctx,
                UpdateTaskArguments(
                    task_id=task.id,
                    title="Initial title",
                    expected_revision=1,  # Stale revision!
                ),
            )

        # Verify the user edit was NOT overwritten
        await db.refresh(task)
        assert task.title == "User edited title"
        assert task.revision == 2


# ---------------------------------------------------------------------------
# 11. PM-4 Gate Test: Multi-Turn Conversation & Privacy Boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm4_gate_realistic_multi_turn_continuity_and_privacy(
    storage, tool_executor, settings
):
    """PM-4 Gate: Multi-turn continuity preserves state across turns and cannot cross privacy boundaries."""
    engine, owners = storage
    principal_a, session_a = owners[0]
    principal_b, session_b = owners[1]
    confirmation_store = InMemoryVoiceConfirmationStore()
    planning_executor = PlanningExecutor(
        settings, tool_executor, confirmation_service=confirmation_store
    )

    with Session(engine, expire_on_commit=False) as sync_db:
        db = AsyncDB(sync_db)
        now_utc = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

        # Turn 1: Create Plan Apollo and Task "prepare deck"
        turn1_id = await create_turn(db, principal_a, session_a)
        snap1 = PlanningSnapshot(
            consent=PlanningConsent(
                user_id=principal_a.user_id,
                session_id=session_a,
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

        dec_plan = make_decision(
            proposal=make_proposal(
                "CREATE_PLAN", "Apollo", text="start project Apollo", classification="PROJECT"
            ),
            disposition="AUTO",
            plan_ordinal=0,
        )
        dec_task = make_decision(
            proposal=make_proposal(
                "CREATE_TASK",
                "prepare deck",
                text="prepare deck tomorrow at 5pm",
                temporal_text="tomorrow at 5pm",
            ),
            disposition="AUTO",
            plan_ordinal=0,
            scheduled_at=now_utc + timedelta(days=1),
        )

        receipt1 = await planning_executor.execute_batch(
            db=db,
            principal=principal_a,
            session_id=session_a,
            turn_id=turn1_id,
            response_id=uuid.uuid4(),
            transcript="start project Apollo and prepare deck tomorrow at 5pm",
            snapshot=snap1,
            decisions=(dec_plan, dec_task),
            user_timezone="America/Los_Angeles",
        )
        assert len(receipt1.saved_actions) == 2
        apollo_id = receipt1.plan_id
        deck_task_id = receipt1.saved_actions[1]["id"]
        # Creating/selecting a plan advances durable consent; later turns read fresh state.
        state = await db.scalar(
            select(PlanningSession).where(
                PlanningSession.session_id == session_a,
                PlanningSession.user_id == principal_a.user_id,
            )
        )
        assert state.active_plan_id == apollo_id
        assert state.state_version == 2
        snap1 = replace(
            snap1,
            consent=replace(
                snap1.consent,
                state_version=state.state_version,
                expected_state_version=state.state_version,
            ),
        )

        # Turn 2: Pronoun Rescheduling: "Move that to Friday at 3pm"
        turn2_id = await create_turn(db, principal_a, session_a)
        deck_task = await db.scalar(select(Task).where(Task.id == deck_task_id))
        snap2 = PlanningSnapshot(
            consent=snap1.consent,
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=apollo_id,
            plans=(Target(apollo_id, "plan", "Apollo", apollo_id, 1),),
            targets=(
                Target(
                    deck_task_id,
                    "task",
                    deck_task.title,
                    apollo_id,
                    deck_task.revision,
                    deck_task.due_at,
                ),
            ),
            context=(),
            recent_receipt=receipt1,
        )

        t2_transcript = "Move that to Friday at 3pm"
        t2_env = Envelope(
            actions=[
                make_proposal(
                    operation="UPDATE_TASK",
                    title="Move that",
                    text=t2_transcript,
                    temporal_text="Friday at 3pm",
                    target_mention="that",
                )
            ]
        )
        t2_decisions = validate(t2_env, t2_transcript, snap2)
        assert len(t2_decisions) == 1
        assert t2_decisions[0].target_id == deck_task_id

        receipt2 = await planning_executor.execute_batch(
            db=db,
            principal=principal_a,
            session_id=session_a,
            turn_id=turn2_id,
            response_id=uuid.uuid4(),
            transcript=t2_transcript,
            snapshot=snap2,
            decisions=t2_decisions,
            user_timezone="America/Los_Angeles",
        )
        assert len(receipt2.saved_actions) == 1
        await db.refresh(deck_task)
        assert deck_task.due_at.replace(tzinfo=UTC) == t2_decisions[0].scheduled_at

        # Turn 3: Add Context Note
        turn3_id = await create_turn(db, principal_a, session_a)
        dec_note = make_decision(
            proposal=make_proposal(
                "ADD_PLAN_CONTEXT",
                "Deployment on GCP",
                text="Deployment on GCP",
                content="Deployment on GCP",
            ),
            disposition="AUTO",
            plan_id=apollo_id,
        )
        receipt3 = await planning_executor.execute_batch(
            db=db,
            principal=principal_a,
            session_id=session_a,
            turn_id=turn3_id,
            response_id=uuid.uuid4(),
            transcript="Deployment on GCP",
            snapshot=snap2,
            decisions=(dec_note,),
            user_timezone="America/Los_Angeles",
        )
        assert len(receipt3.saved_actions) == 1
        note_id = receipt3.saved_actions[0]["id"]

        # Turn 4: Pronoun Completion: "Mark it done"
        turn4_id = await create_turn(db, principal_a, session_a)
        t4_transcript = "Mark it done"
        t4_env = Envelope(
            actions=[
                make_proposal(
                    operation="COMPLETE_TASK",
                    title="Mark it done",
                    text=t4_transcript,
                    target_mention="it",
                )
            ]
        )
        snap4 = PlanningSnapshot(
            consent=snap1.consent,
            now_utc=now_utc,
            timezone="America/Los_Angeles",
            active_plan_id=apollo_id,
            plans=(Target(apollo_id, "plan", "Apollo", apollo_id, 1),),
            targets=(
                Target(
                    deck_task_id,
                    "task",
                    deck_task.title,
                    apollo_id,
                    deck_task.revision,
                    deck_task.due_at,
                ),
            ),
            context=(),
            recent_receipt=receipt2,
        )
        t4_decisions = validate(t4_env, t4_transcript, snap4)
        assert len(t4_decisions) == 1
        assert t4_decisions[0].target_id == deck_task_id

        receipt4 = await planning_executor.execute_batch(
            db=db,
            principal=principal_a,
            session_id=session_a,
            turn_id=turn4_id,
            response_id=uuid.uuid4(),
            transcript=t4_transcript,
            snapshot=snap4,
            decisions=t4_decisions,
            user_timezone="America/Los_Angeles",
        )
        assert len(receipt4.saved_actions) == 1
        await db.refresh(deck_task)
        assert deck_task.status == "completed"

        # Turn 5: Context Promotion under Memory Consent
        mem_item, created = await promote_plan_context_to_memory(
            db=db,
            settings=settings,
            principal=principal_a,
            plan_id=apollo_id,
            context_item_id=note_id,
            source_session_id=session_a,
        )
        assert created is True
        assert mem_item.content == "Deployment on GCP"

        # Privacy Boundaries Check:
        # User B cannot access User A's plan or context
        with pytest.raises(PlanningError, match="plan_not_found"):
            await resolve_plan_selection(db, principal_b.user_id, "Apollo")

        with pytest.raises(MemoryPromotionError, match="plan_context_not_found"):
            await promote_plan_context_to_memory(
                db=db,
                settings=settings,
                principal=principal_b,
                plan_id=apollo_id,
                context_item_id=note_id,
            )
