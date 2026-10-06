from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import JSON, MetaData, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    ConversationTurn,
    MemoryItem,
    Message,
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    PlanningSession,
    Reminder,
    Task,
    VoiceSession,
)
from app.planning.extraction import ExtractionError, extract, parse_envelope
from app.planning.observer import PlanningObserver
from app.planning.policy import PlanningConsent
from app.planning.resolution import validate
from app.planning.service import revoke_session_state
from app.planning.types import Envelope, PlanningSnapshot, Proposal, Span, Target

from .test_planning import AsyncDB
from .test_planning import storage as planning_storage  # noqa: F401

NOW = datetime(2026, 10, 5, 16, tzinfo=UTC)
OWNER, SESSION, PLAN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SNAPSHOT = PlanningSnapshot(
    PlanningConsent(OWNER, SESSION, True, True, "plan", 2, 2),
    NOW,
    "America/Los_Angeles",
    PLAN,
    (Target(PLAN, "plan", "XYZ", PLAN, 1),),
)


def span(text, evidence):
    return Span(start=text.index(evidence), length=len(evidence), text=evidence)


def proposal(text, title="Report", temporal="Friday", operation="CREATE_TASK", **changes):
    values = dict(
        operation=operation,
        classification="COMMITMENT",
        title=title,
        actor="user",
        source=span(text, text),
        temporal=span(text, temporal) if temporal else None,
    )
    values.update(changes)
    return Proposal(**values)


def decisions(text, *actions, snapshot=SNAPSHOT):
    return validate(Envelope(actions=list(actions)), text, snapshot)


def test_candidate_dates_are_independent_and_deadlines_remain_local_end_of_day():
    text = "I need the API Monday. I need the report Friday. We have a review Tuesday at 4 PM."
    actions = [
        proposal(text, "API", "Monday", source=span(text, "I need the API Monday.")),
        proposal(text, "Report", "Friday", source=span(text, "I need the report Friday.")),
        proposal(
            text,
            "Review",
            "Tuesday at 4 PM",
            operation="CREATE_REMINDER",
            actor="team",
            source=span(text, "We have a review Tuesday at 4 PM."),
        ),
    ]
    resolved = decisions(text, *actions)
    assert [d.disposition for d in resolved] == ["AUTO"] * 3
    assert [d.scheduled_at.isoformat() for d in resolved] == [
        "2026-10-06T06:59:00+00:00",
        "2026-10-10T06:59:00+00:00",
        "2026-10-06T23:00:00+00:00",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "I don't need to finish the report Friday.",
        'Alice said "I need to finish the report Friday".',
        "If we started, we would finish the report Friday.",
        "Should I finish the report Friday?",
        "The tutorial says to finish the report Friday.",
        "I finished the report yesterday.",
    ],
)
def test_crop_cannot_remove_negation_quote_reference_hypothetical_or_history(text):
    evidence = (
        "finish the report Friday" if "finish the report Friday" in text else "finished the report"
    )
    p = proposal(text, temporal=None, source=span(text, evidence))
    assert decisions(text, p)[0].disposition == "NO_ACTION"


def test_technology_and_false_actor_are_not_personal_tasks():
    text = "We will use FastAPI."
    assert decisions(text, proposal(text, "FastAPI", None))[0].disposition == "NO_ACTION"
    text = "Alice will finish the report Friday."
    assert decisions(text, proposal(text))[0].reason == "actor_ambiguity"
    text = "alice and bob need the report Friday."
    assert decisions(text, proposal(text, actor="team"))[0].reason == "actor_ambiguity"


def test_blank_titles_and_historical_completion_do_not_become_actions():
    text = "I need the report Friday."
    assert decisions(text, proposal(text, title=" "))[0].disposition == "DENY"
    text = "I finished the report in 2025."
    p = proposal(text, temporal=None, operation="COMPLETE_TASK", classification="PROGRESS")
    assert decisions(text, p)[0].reason == "historical_evidence"


@pytest.mark.parametrize("operation", ["CREATE_TASK", "CREATE_PLAN", "CREATE_REMINDER"])
def test_external_request_cannot_be_rewritten_as_automatic_organization(operation):
    text = "Book a flight Friday."
    assert decisions(text, proposal(text, "Flight", operation=operation))[0].disposition == "DENY"


@pytest.mark.parametrize(
    "expression,reason",
    [
        ("after lunch", "unsupported_anchor"),
        ("before the meeting", "unsupported_anchor"),
        ("three days before Friday", "unsupported_offset"),
        ("next week", "date_range"),
        ("Friday at 4", "ambiguous_time"),
        ("today at 8 AM", "past_time"),
        ("March 14 2027 at 2:30 AM", "nonexistent_local_time"),
        ("November 1 2026 at 1:30 AM", "ambiguous_local_time"),
    ],
)
def test_unsupported_or_unsafe_temporal_meaning_clarifies(expression, reason):
    text = f"I need the report {expression}."
    result = decisions(text, proposal(text, temporal=expression))[0]
    assert (result.disposition, result.reason, result.scheduled_at) == ("CLARIFY", reason, None)


def test_offset_cannot_be_removed_from_temporal_span_and_dates_cannot_be_swapped():
    text = "I need the report three days before Friday."
    assert decisions(text, proposal(text))[0].reason == "missing_temporal_modifier"
    text = "I need the API Monday. I need the report Friday."
    p = proposal(text, "API", "Friday", source=span(text, "I need the API Monday."))
    assert decisions(text, p)[0].disposition == "DENY"
    broad = proposal(text, "API", "Friday")
    assert decisions(text, broad)[0].reason == "temporal_action_mismatch"


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("Friday 16:00", "2026-10-09T23:00:00+00:00"),
        ("in one hour", "2026-10-05T17:00:00+00:00"),
    ],
)
def test_supported_clock_and_relative_duration_are_not_silently_dropped(expression, expected):
    text = f"Remind me to write the report {expression}."
    result = decisions(text, proposal(text, temporal=expression, operation="CREATE_REMINDER"))[0]
    assert result.disposition == "AUTO" and result.scheduled_at.isoformat() == expected


def test_grounding_schema_actor_and_ownership_cannot_be_model_supplied():
    text = "I need the report Friday."
    assert decisions(text, proposal(text, "Buy car"))[0].reason == "ungrounded_action"
    p = proposal(text, source=Span(start=200, length=len(text), text=text))
    assert decisions(text, p)[0].reason == "invalid_evidence"
    raw = {"actions": [proposal(text).model_dump()]}
    raw["actions"][0]["user_id"] = str(OWNER)
    with pytest.raises(ExtractionError, match="malformed_output"):
        parse_envelope(json.dumps(raw), 8)
    with pytest.raises(ExtractionError, match="duplicate_json_key"):
        parse_envelope('{"actions":[],"actions":[]}', 8)
    with pytest.raises(ExtractionError, match="action_overflow"):
        parse_envelope(json.dumps({"actions": [proposal(text).model_dump()] * 9}), 8)


def test_owned_corrections_require_unique_targets_and_preserve_revision():
    text = "Actually, make the report Thursday."
    p = proposal(text, temporal="Thursday", operation="UPDATE_TASK")
    target = Target(uuid.uuid4(), "task", "Report", PLAN, 3)
    result = decisions(text, p, snapshot=replace(SNAPSHOT, targets=(target,)))[0]
    assert result.disposition == "AUTO" and result.target_revision == 3
    assert result.target_id == target.id
    other_plan = replace(target, plan_id=uuid.uuid4())
    assert (
        decisions(text, p, snapshot=replace(SNAPSHOT, targets=(other_plan,)))[0].disposition
        == "CLARIFY"
    )
    second = replace(target, id=uuid.uuid4())
    assert (
        decisions(text, p, snapshot=replace(SNAPSHOT, targets=(target, second)))[0].reason
        == "ambiguous_target"
    )


def test_context_requires_plan_and_duplicates_do_not_change_schedule():
    text = "The backend uses FastAPI."
    p = proposal(
        text, "FastAPI", None, "ADD_PLAN_CONTEXT", actor="context", classification="INFORMATION"
    )
    assert (
        decisions(text, p, snapshot=replace(SNAPSHOT, active_plan_id=None))[0].reason
        == "missing_plan"
    )
    text = "I need the report Friday."
    p = proposal(text)
    at = datetime(2026, 10, 10, 6, 59, tzinfo=UTC)
    target = Target(uuid.uuid4(), "task", "Report", PLAN, 1, at)
    assert (
        decisions(text, p, snapshot=replace(SNAPSHOT, targets=(target,)))[0].reason == "duplicate"
    )
    assert decisions(text, p, p)[1].reason == "duplicate"
    assert (
        decisions(text, p, snapshot=replace(SNAPSHOT, complete=False))[0].reason == "context_bound"
    )


class FakeLLM:
    enabled = True

    def __init__(self, output, *, delay=0, entered=None, release=None):
        self.output, self.delay, self.entered, self.release = output, delay, entered, release
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        if self.entered:
            self.entered.set()
        if self.release:
            await self.release.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        yield SimpleNamespace(event_type="text_delta", delta=self.output)
        yield SimpleNamespace(event_type="response_completed", finish_reason="stop", text=None)


async def test_extraction_uses_shared_service_once_without_mutation_tools():
    llm = FakeLLM('{"actions":[]}')
    await extract(
        llm,
        SNAPSHOT,
        uuid.uuid4(),
        "What tasks do I have?",
        timeout_ms=100,
        max_actions=8,
        max_tokens=1024,
    )
    request = llm.requests[0]
    assert len(llm.requests) == 1 and request.allowed_tools == () and request.tool_choice == "none"
    assert request.max_output_tokens == 1024
    context = json.loads(request.messages[0].content)
    assert context["source_clauses"] == [
        {"start": 0, "length": 21, "text": "What tasks do I have?"}
    ]
    for word in context["word_offsets"]:
        assert (
            context["final_transcript"][word["start"] : word["start"] + word["length"]]
            == word["text"]
        )


@pytest.fixture
def observed_storage(request):
    engine, owners = request.getfixturevalue("planning_storage")
    metadata = MetaData()
    metadata.reflect(bind=engine)
    # Reuse existing table ownership metadata, adding the final-message source table.
    table = Message.__table__.to_metadata(metadata)
    table.c.content_json.type = JSON()
    table.create(engine)
    memories = MemoryItem.__table__.to_metadata(metadata)
    for column in memories.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    memories.c.search_tsv.type = Text()
    memories.c.search_tsv.computed = None
    memories.c.search_tsv.server_default = None
    memories.create(engine)
    principal, session_id = owners[0]
    text = "I need the report Friday."
    turn_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(
            PlanningSession(
                session_id=session_id, user_id=principal.user_id, mode="plan", state_version=2
            )
        )
        db.add(
            ConversationTurn(
                id=turn_id,
                session_id=session_id,
                user_id=principal.user_id,
                turn_number=1,
                status="committed",
            )
        )
        db.flush()
        db.add(Message(turn_id=turn_id, user_id=principal.user_id, role="user", content=text))
        db.commit()

    @asynccontextmanager
    async def factory():
        with Session(engine, expire_on_commit=True) as db:
            try:
                yield AsyncDB(db)
            finally:
                db.rollback()

    return engine, principal, session_id, turn_id, text, factory


def observer_parts(data, mode="shadow", **llm_options):
    engine, principal, session_id, turn_id, text, factory = data
    config = Settings(
        _env_file=None,
        plan_mode_enabled=True,
        plan_extraction_mode=mode,
        plan_test_user_ids=(principal.user_id,),
        plan_extraction_timeout_ms=100,
    )
    llm = FakeLLM(json.dumps({"actions": [proposal(text).model_dump()]}), **llm_options)
    observer = PlanningObserver(config, llm, factory)
    kwargs = dict(
        principal=principal,
        session_id=session_id,
        turn_id=turn_id,
        transcript=text,
        now_utc=NOW,
        timezone="America/Los_Angeles",
    )
    return observer, llm, kwargs


@pytest.mark.parametrize("mode", ["shadow", "on"])
async def test_shadow_zero_organizational_writes_on_proposals_only_and_retry_once(
    observed_storage, mode
):
    observer, llm, kwargs = observer_parts(observed_storage, mode)
    await observer.observe(**kwargs)
    await observer.observe(**{**kwargs, "now_utc": datetime(2026, 10, 7, 8, tzinfo=UTC)})
    assert len(llm.requests) == 1
    with Session(observed_storage[0]) as db:
        for model in (Task, Reminder, Plan, PlanContextItem, MemoryItem):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(select(func.count()).select_from(PlanningBatch)) == (mode == "on")
        assert db.scalar(select(func.count()).select_from(PlanningAction)) == (mode == "on")
        marker = db.get(ConversationTurn, kwargs["turn_id"]).metadata_json["planning_observation"]
        assert marker["status"] == "validated" and "Report" not in json.dumps(marker)
        if mode == "on":
            row = db.scalar(select(PlanningAction))
            assert row.committed_at is None and row.status == "pending"
            assert row.payload_json["scheduled_at"] == "2026-10-10T06:59:00+00:00"


@pytest.mark.parametrize("change", ["private", "revoke", "purge"])
async def test_privacy_revocation_or_source_removal_during_model_call_discards_proposals(
    observed_storage, change
):
    entered, release = asyncio.Event(), asyncio.Event()
    observer, llm, kwargs = observer_parts(observed_storage, "on", entered=entered, release=release)
    task = observer.schedule(**kwargs)
    await asyncio.wait_for(entered.wait(), 1)
    with Session(observed_storage[0]) as db:
        if change == "private":
            voice = db.get(VoiceSession, kwargs["session_id"])
            voice.client_metadata = {"memory_excluded": True}
        elif change == "revoke":
            await revoke_session_state(
                AsyncDB(db), kwargs["principal"].user_id, kwargs["session_id"]
            )
        else:
            db.delete(db.scalar(select(Message)))
        db.commit()
    release.set()
    await task
    with Session(observed_storage[0]) as db:
        assert db.scalar(select(func.count()).select_from(PlanningAction)) == 0
        assert db.scalar(select(func.count()).select_from(PlanningBatch)) == 0


async def test_capacity_timeout_cancellation_and_metrics_do_not_leak_content(
    observed_storage, caplog
):
    observer, llm, kwargs = observer_parts(observed_storage, delay=10)
    observer.settings.plan_extraction_max_concurrent = 1
    caplog.set_level("INFO", logger="voice-assistance-backend")
    task = observer.schedule(**kwargs)
    assert observer.schedule(**kwargs) is None
    await task
    assert len(llm.requests) == 1
    with Session(observed_storage[0]) as db:
        marker = db.get(ConversationTurn, kwargs["turn_id"]).metadata_json["planning_observation"]
        assert marker["reason"] == "timeout"
    events = [r for r in caplog.records if getattr(r, "event", "") == "planning.observation"]
    assert events and all(
        "Report" not in str(r.__dict__) and "Friday" not in str(r.__dict__) for r in events
    )
    await observer.close()
    assert not observer.tasks


async def test_ineligible_controls_and_private_sessions_make_no_model_request(observed_storage):
    observer, llm, kwargs = observer_parts(observed_storage)
    assert observer.schedule(**{**kwargs, "transcript": "Stop planning."}) is None
    assert observer.schedule(**{**kwargs, "transcript": "yes"}) is None
    with Session(observed_storage[0]) as db:
        db.get(VoiceSession, kwargs["session_id"]).client_metadata = {"memory_excluded": True}
        db.commit()
    await observer.observe(**kwargs)
    assert not llm.requests


async def test_proposal_privacy_erasure_is_owned_and_preserves_saved_resources(observed_storage):
    from app.planning.repository import erase_proposal_content

    observer, llm, kwargs = observer_parts(observed_storage, "on")
    await observer.observe(**kwargs)
    with Session(observed_storage[0]) as db:
        row = db.scalar(select(PlanningAction))
        db.add(
            Task(
                user_id=kwargs["principal"].user_id, title="Saved report", planning_action_id=row.id
            )
        )
        await erase_proposal_content(AsyncDB(db), uuid.uuid4(), kwargs["session_id"])
        db.commit()
        assert row.payload_json.get("proposal")
        await revoke_session_state(AsyncDB(db), kwargs["principal"].user_id, kwargs["session_id"])
        await erase_proposal_content(AsyncDB(db), kwargs["principal"].user_id, kwargs["session_id"])
        db.commit()
        db.refresh(row)
        assert row.payload_json == {"redacted": True} and row.source_spans_json == []
        assert db.scalar(select(PlanningBatch)).source_digest == "0" * 64
        assert db.scalar(select(Task)).title == "Saved report"


async def test_generic_session_metadata_private_flag_revokes_and_erases_proposals(observed_storage):
    from app.api.sessions import update_session
    from app.schemas import SessionUpdateRequest

    observer, llm, kwargs = observer_parts(observed_storage, "on")
    await observer.observe(**kwargs)
    with Session(observed_storage[0]) as db:
        await update_session(
            kwargs["session_id"],
            SessionUpdateRequest(client_metadata={"memory_excluded": True}),
            SimpleNamespace(),
            AsyncDB(db),
            kwargs["principal"],
        )
        assert db.get(PlanningSession, kwargs["session_id"]).mode == "normal"
        assert db.scalar(select(PlanningAction)).payload_json == {"redacted": True}


async def test_shared_capacity_reserves_foreground_and_session_cancel_stops_work(observed_storage):
    entered = asyncio.Event()
    observer, llm, kwargs = observer_parts(observed_storage, entered=entered, delay=10)
    llm.background_capacity = 0
    assert observer.schedule(**kwargs) is None
    llm.background_capacity = 1
    task = observer.schedule(**kwargs)
    await asyncio.wait_for(entered.wait(), 1)
    await observer.cancel_session(kwargs["principal"].user_id, kwargs["session_id"])
    assert task.cancelled() and not observer.tasks


async def test_gateway_schedules_before_early_routes_and_retry_does_not_schedule_twice():
    from app.websocket.gateway import VoiceGateway

    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway._resolve_planning_voice = AsyncMock(return_value=None)
    gateway._dispatch_structured_route = AsyncMock(return_value={"status": "completed"})
    gateway.principal = SimpleNamespace(user_id=OWNER)
    gateway.clock = SimpleNamespace(now_utc=lambda: NOW)
    gateway._user_timezone = lambda: "America/Los_Angeles"
    scheduled = []
    gateway.planning_observer = SimpleNamespace(schedule=lambda **kwargs: scheduled.append(kwargs))
    kwargs = dict(
        session_id=SESSION,
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        transcript="I need the report Friday.",
    )
    for _ in range(2):
        assert await gateway._stream_llm_response(**kwargs) == {"status": "completed"}
    assert len(scheduled) == 1 and gateway._dispatch_structured_route.await_count == 2


async def test_evaluation_replay_is_not_live_quality_and_metrics_count_false_actions():
    from app.planning.evaluation import DEFAULT_CORPUS, evaluate, summarize

    corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
    report = await evaluate(corpus)
    assert report["evaluation_kind"] == "labeled_proposal_replay"
    assert report["overall"]["candidate_precision"] == 1
    assert report["overall"]["candidate_recall"] == 1
    assert not report["overall"]["quality_gate_met"]
    assert report["overall"]["duplicate_decisions"] == {"expected": 1, "correct": 1}
    row = {
        "counts": {
            "expected_auto": 2,
            "correct_auto": 1,
            "proposed_auto": 2,
            "requests": 1,
            "timeouts": 1,
            "failures": 1,
        },
        "requested": True,
        "duration_ms": 100,
    }
    summary = summarize([row], kind="live_provider")
    assert summary["candidate_precision"] == 0.5 and summary["candidate_recall"] == 0.5
    assert summary["false_actions"] == 1 and summary["timeout_rate"] == 1
    assert not summary["quality_gate_met"]
