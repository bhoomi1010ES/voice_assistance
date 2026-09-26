from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.reminder_tools import (
    CreateReminderArguments,
    normalize_create_reminder_arguments,
)
from app.llm.task_tools import CreateTaskArguments, normalize_create_task_arguments
from app.llm.tool_loop import ToolExecutionContext
from app.routing.models import RouteName, RouterRunStatus, RouterRuntimeContext
from app.routing.rules import classify_transcript
from app.routing.service import DecisionRouterService


@pytest.mark.parametrize(
    ("text", "route", "tool"),
    [
        ("What time is it?", RouteName.DIRECT_TOOL, "get_current_time"),
        ("What's the current time", RouteName.DIRECT_TOOL, "get_current_time"),
        ("What's the time in Tokyo?", RouteName.DIRECT_TOOL, "get_current_time"),
        ("What's today's date?", RouteName.DIRECT_TOOL, "get_current_date"),
        ("What's today's date in New York?", RouteName.DIRECT_TOOL, "get_current_date"),
        ("What is the date today?", RouteName.DIRECT_TOOL, "get_current_date"),
        ("What time do I take my medicine?", RouteName.STRUCTURED_READ, "list_reminders"),
        ("What date is my meeting?", RouteName.STRUCTURED_READ, "list_tasks"),
        ("What tasks do I have today?", RouteName.STRUCTURED_READ, "list_tasks"),
        ("When is my meeting?", RouteName.STRUCTURED_READ, "list_tasks"),
        ("What date is my appointment?", RouteName.STRUCTURED_READ, "list_tasks"),
        ("What time is Rahul's reminder?", RouteName.STRUCTURED_READ, "list_reminders"),
        ("How do I create a task?", RouteName.GENERAL_LLM, None),
        ("Do I need to create a task?", RouteName.GENERAL_LLM, None),
        ("Can you explain how reminders work?", RouteName.GENERAL_LLM, None),
        ("How does recurring scheduling work?", RouteName.GENERAL_LLM, None),
        ("How can I delete a reminder?", RouteName.GENERAL_LLM, None),
        ("Create a task to call Rahul tomorrow at 9 AM.", RouteName.TASK_ACTION, None),
        ("Remind me to call Rahul tomorrow at 9 AM.", RouteName.TASK_ACTION, None),
        ("Delete my Rahul reminder.", RouteName.TASK_ACTION, None),
        ("Change my medicine reminder to 2 PM.", RouteName.TASK_ACTION, None),
        ("Remember that I prefer Phoenix Mall.", RouteName.MEMORY_ACTION, None),
        ("Save that my preferred coffee is cappuccino.", RouteName.MEMORY_ACTION, None),
        ("Forget that I prefer Phoenix Mall.", RouteName.MEMORY_ACTION, None),
        ("Delete my saved coffee preference.", RouteName.MEMORY_ACTION, None),
        ("Which shopping mall do I prefer?", RouteName.MEMORY_QUERY, None),
        ("What is my name?", RouteName.MEMORY_QUERY, None),
        ("What is my favorite spaceship?", RouteName.MEMORY_QUERY, None),
        ("What's my dog's name?", RouteName.MEMORY_QUERY, None),
        ("Where do I work now?", RouteName.MEMORY_QUERY, None),
        ("What happened yesterday?", RouteName.MEMORY_QUERY, None),
        ("What do you remember about me?", RouteName.MEMORY_QUERY, None),
        ("What did I tell you about my preferred coffee?", RouteName.MEMORY_QUERY, None),
        ("What time did I say I take my medicine?", RouteName.MEMORY_QUERY, None),
        ("What time is my meeting?", RouteName.STRUCTURED_READ, "list_tasks"),
        ("Can you remind me what time it is?", RouteName.MIXED_AMBIGUOUS, None),
        ("Stop listening", RouteName.CONTROL, None),
        ("Stop that", RouteName.MIXED_AMBIGUOUS, None),
        ("Cancel my reminder", RouteName.MIXED_AMBIGUOUS, None),
        ("Create one", RouteName.MIXED_AMBIGUOUS, None),
        ("Update it", RouteName.MIXED_AMBIGUOUS, None),
        ("Yes", RouteName.MIXED_AMBIGUOUS, None),
        ("Create a task and remind me tomorrow", RouteName.MIXED_AMBIGUOUS, None),
        ("What is the current date and time?", RouteName.MIXED_AMBIGUOUS, None),
        (
            "What time is it and remind me to call Rahul at 6 PM.",
            RouteName.MIXED_AMBIGUOUS,
            None,
        ),
        (
            "Remember that I like cappuccino and tell me today's date.",
            RouteName.MIXED_AMBIGUOUS,
            None,
        ),
        ("Delete my reminder and tell me a joke.", RouteName.MIXED_AMBIGUOUS, None),
        ("Remember that I prefer tea", RouteName.MEMORY_ACTION, None),
        ("Forget that I prefer tea", RouteName.MEMORY_ACTION, None),
        ("Remind me about the clinic tomorrow.", RouteName.MIXED_AMBIGUOUS, None),
        ("Remind me to call", RouteName.MIXED_AMBIGUOUS, None),
        ("Remind remind me to call Rahul tomorrow at 9 AM", RouteName.MIXED_AMBIGUOUS, None),
        ("[unintelligible] medicine meeting tomorrow yes", RouteName.MIXED_AMBIGUOUS, None),
        ("remind me to", RouteName.MIXED_AMBIGUOUS, None),
        ("Aaj medicine ka time kya hai?", RouteName.MIXED_AMBIGUOUS, None),
        (
            "\u0906\u091c \u0915\u094c\u0928 \u0938\u0940 \u0924\u093e\u0930\u0940\u0916 "
            "\u0939\u0948?",
            RouteName.MIXED_AMBIGUOUS,
            None,
        ),
    ],
)
def test_narrow_rules_and_known_false_positives(
    text: str,
    route: RouteName,
    tool: str | None,
) -> None:
    decision = classify_transcript(text)

    assert decision.route == route
    assert decision.target_tool == tool
    assert decision.decision_source == "rule"
    assert 0.0 <= decision.confidence <= 1.0
    if route in {RouteName.TASK_ACTION, RouteName.MEMORY_ACTION}:
        assert decision.target_tool is None
        assert decision.read_arguments is None
        assert "title" not in decision.model_dump()
        assert "content" not in decision.model_dump()


def test_structured_reads_have_only_bounded_schema_arguments() -> None:
    medicine = classify_transcript("What time do I take my medicine?")

    assert medicine.read_arguments == {
        "status": "scheduled",
        "upcoming": True,
        "next_only": True,
        "search_terms": ["medicine"],
    }


@pytest.mark.parametrize(
    ("transcript", "tool", "expected"),
    [
        (
            "What tasks do I have today?",
            "list_tasks",
            {"date_window": "today", "active_only": True},
        ),
        (
            "What reminders do I have tomorrow?",
            "list_reminders",
            {"status": "scheduled", "date_window": "tomorrow"},
        ),
        (
            "What is my next task?",
            "list_tasks",
            {"next_only": True, "active_only": True},
        ),
        (
            "What's my next reminder?",
            "list_reminders",
            {"status": "scheduled", "upcoming": True, "next_only": True},
        ),
    ],
)
def test_schedule_read_routes_encode_bounded_window_and_ordering(
    transcript: str, tool: str, expected: dict[str, object]
) -> None:
    decision = classify_transcript(transcript)

    assert decision.route == RouteName.STRUCTURED_READ
    assert decision.target_tool == tool
    assert decision.read_arguments == expected

    from app.routing.models import RouteDecision

    for arguments in (
        {"limit": 51},
        {"title": "invented write data"},
        {"status": "made_up"},
        {"limit": "20"},
    ):
        with pytest.raises(ValidationError):
            RouteDecision(
                route=RouteName.STRUCTURED_READ,
                target_tool="list_tasks",
                read_arguments=arguments,
            )


def test_reminder_creation_follows_existing_confirmation_required_task_semantics() -> None:
    from app.routing.models import ActionDomain

    create = classify_transcript("Remind me to call Rahul tomorrow at 9 AM.")
    manage = classify_transcript("Delete my Rahul reminder.")

    assert create.route == RouteName.TASK_ACTION
    assert create.action_domain == ActionDomain.TASK
    assert create.target_tool is None
    assert create.read_arguments is None
    assert manage.route == RouteName.TASK_ACTION
    assert manage.action_domain == ActionDomain.REMINDER
    assert manage.target_tool is None
    assert manage.read_arguments is None


def test_router_decision_rejects_bad_confidence_and_excessive_transcript() -> None:
    from app.routing.models import RouteDecision

    with pytest.raises(ValidationError):
        RouteDecision(route=RouteName.GENERAL_LLM, confidence=1.01)
    with pytest.raises(ValidationError):
        RouteDecision(route=RouteName.GENERAL_LLM, confidence=True)
    with pytest.raises(ValidationError):
        RouteDecision(route=RouteName.GENERAL_LLM, decision_source="invented")
    with pytest.raises(ValidationError):
        RouteDecision(route=RouteName.DIRECT_TOOL, target_tool=42)
    with pytest.raises(ValueError):
        classify_transcript("x" * 513)


def _context(*, cancellation_check=lambda: False) -> RouterRuntimeContext:
    return RouterRuntimeContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        turn_id=uuid.UUID("00000000-0000-0000-0000-000000000103"),
        response_id=uuid.UUID("00000000-0000-0000-0000-000000000104"),
        cancellation_check=cancellation_check,
    )


class _EchoGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.route: str | None = None

    async def ainvoke(self, input: dict, *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        self.route = input["decision"]["route"]
        return {
            "outcome": {
                "route": self.route,
                "needs_clarification": self.route == RouteName.MIXED_AMBIGUOUS,
            }
        }


def _service(graph: _EchoGraph) -> DecisionRouterService:
    return DecisionRouterService(
        Settings(_env_file=None, router_mode="on", router_timeout_ms=500),
        graph=graph,
    )


@pytest.mark.asyncio
async def test_pending_confirmation_is_resolved_before_yes_is_classified() -> None:
    graph = _EchoGraph()
    service = _service(graph)
    resolver_calls = 0

    async def resolve_pending():
        nonlocal resolver_calls
        resolver_calls += 1
        return {"status": "completed", "confirmation": "approved"}

    result = await service.route_transcript(
        "yes", context=_context(), resolve_pending_confirmation=resolve_pending
    )

    assert resolver_calls == 1
    assert result.status == RouterRunStatus.CONFIRMATION_HANDLED
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_yes_without_pending_confirmation_needs_clarification() -> None:
    graph = _EchoGraph()

    async def no_pending():
        return None

    result = await _service(graph).route_transcript(
        "yes", context=_context(), resolve_pending_confirmation=no_pending
    )

    assert result.status == RouterRunStatus.DECIDED
    assert result.outcome is not None
    assert result.outcome.route == RouteName.MIXED_AMBIGUOUS
    assert result.outcome.needs_clarification is True


@pytest.mark.asyncio
async def test_cancellation_precedes_pending_confirmation_lookup() -> None:
    graph = _EchoGraph()
    called = False

    async def resolver():
        nonlocal called
        called = True
        return None

    result = await _service(graph).route_transcript(
        "what time is it",
        context=_context(cancellation_check=lambda: True),
        resolve_pending_confirmation=resolver,
    )

    assert result.status == RouterRunStatus.CANCELLED
    assert result.use_legacy_orchestrator is False
    assert called is False
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_cancellation_after_confirmation_lookup_precedes_classification(monkeypatch) -> None:
    from app.routing import rules

    graph = _EchoGraph()
    checks = iter((False, True))
    classified = False
    original = rules.classify_transcript

    def classify(text: str):
        nonlocal classified
        classified = True
        return original(text)

    monkeypatch.setattr(rules, "classify_transcript", classify)

    async def no_pending():
        return None

    result = await _service(graph).route_transcript(
        "What time is it?",
        context=_context(cancellation_check=lambda: next(checks)),
        resolve_pending_confirmation=no_pending,
    )

    assert result.status == RouterRunStatus.CANCELLED
    assert result.use_legacy_orchestrator is False
    assert classified is False
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_confirmation_callback_runs_before_transcript_validation(monkeypatch) -> None:
    from app.routing import rules

    order: list[str] = []
    graph = _EchoGraph()
    original = rules.classify_transcript

    def classify(text: str):
        order.append("classify")
        return original(text)

    monkeypatch.setattr(rules, "classify_transcript", classify)

    async def resolver():
        order.append("confirmation")
        return None

    result = await _service(graph).route_transcript(
        "What time is it?", context=_context(), resolve_pending_confirmation=resolver
    )

    assert result.status == RouterRunStatus.DECIDED
    assert order == ["confirmation", "classify"]
    assert graph.route == RouteName.DIRECT_TOOL


@pytest.mark.asyncio
async def test_confirmation_store_failure_fails_closed_without_graph_or_legacy_fallback() -> None:
    graph = _EchoGraph()

    async def broken_resolver():
        raise RuntimeError("confirmation store unavailable")

    result = await _service(graph).route_transcript(
        "create a task", context=_context(), resolve_pending_confirmation=broken_resolver
    )

    assert result.status == RouterRunStatus.FAILED
    assert result.use_legacy_orchestrator is False
    assert graph.calls == 0


def test_datetime_resolution_logs_do_not_include_user_content(caplog) -> None:
    import logging
    from datetime import UTC, datetime

    private_text = "Call Rahul about the confidential Orchid account tomorrow at 9 AM."
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        clock=FrozenClock(datetime(2026, 9, 24, 7, 0, tzinfo=UTC)),
        user_timezone="Asia/Kolkata",
        timezone_source="device",
        source_transcript=private_text,
    )
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")

    normalize_create_task_arguments(
        context,
        CreateTaskArguments(title="Confidential Orchid account", due_expression="tomorrow at 9 AM"),
    )
    normalize_create_reminder_arguments(
        context,
        CreateReminderArguments(
            title="Confidential Orchid account",
            trigger_expression="tomorrow at 9 AM",
        ),
    )

    resolution_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "datetime.resolution"
    ]
    assert len(resolution_records) == 2
    assert private_text not in caplog.text
    assert "Confidential Orchid account" not in caplog.text
    for record in resolution_records:
        assert not hasattr(record, "input")
        assert not hasattr(record, "resolved_local")
        assert not hasattr(record, "resolved_utc")


@pytest.mark.asyncio
async def test_full_frozen_acceptance_corpus_passes_without_side_effects() -> None:
    from scripts.router_acceptance import evaluate_corpus

    corpus, results = await evaluate_corpus()

    assert corpus["version"] == "1.1.0"
    assert corpus["status"] == "frozen"
    assert len(results) == 85
    assert sum(row["critical"] for row in results) == 81
    assert all(row["passed"] for row in results)
    assert all(row["write_attempted"] is False for row in results)
    assert sum(row["critical"] and not row["passed"] for row in results) == 0


def test_prompt_injection_acceptance_cases_are_non_executable() -> None:
    import json
    from pathlib import Path

    corpus_path = (
        Path(__file__).resolve().parents[2] / "docs" / "phase0_router_acceptance_corpus_v1.json"
    )
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = [case for case in corpus["cases"] if "prompt_injection_stt" in case["source"]]

    assert len(cases) == 10
    for case in cases[:9]:
        assert case["expected_route"] == "MIXED_AMBIGUOUS"
        assert case["requires_clarification"] is True
        assert case["expected_target_tool"] is None
        assert case["expected_action_domain"] is None
        assert case["write_execution_permitted"] is False
    assert cases[9]["expected_route"] == "GENERAL_LLM"
