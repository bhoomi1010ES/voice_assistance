from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.routing.models import (
    ActionDomain,
    RouteDecision,
    RouteName,
    RouterMode,
    RouterRunStatus,
    RouterRuntimeContext,
)
from app.routing.service import DecisionRouterService
from app.websocket.gateway import VoiceGateway


def _context(*, cancellation_check=lambda: False) -> RouterRuntimeContext:
    return RouterRuntimeContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        turn_id=uuid.UUID("00000000-0000-0000-0000-000000000103"),
        response_id=uuid.UUID("00000000-0000-0000-0000-000000000104"),
        cancellation_check=cancellation_check,
    )


def test_router_flags_default_to_safe_off_values() -> None:
    settings = Settings(_env_file=None)

    assert settings.router_mode == "off"
    assert settings.router_cohort_percent == 0
    assert settings.router_timeout_ms == 250
    assert settings.router_shadow_max_concurrent == 4


@pytest.mark.parametrize(
    ("name", "value", "attribute", "expected"),
    [
        ("ROUTER_MODE", "shadow", "router_mode", "shadow"),
        ("ROUTER_COHORT_PERCENT", "25", "router_cohort_percent", 25),
        ("ROUTER_TIMEOUT_MS", "400", "router_timeout_ms", 400),
        ("ROUTER_SHADOW_MAX_CONCURRENT", "3", "router_shadow_max_concurrent", 3),
    ],
)
def test_router_flags_load_from_environment(
    monkeypatch,
    name: str,
    value: str,
    attribute: str,
    expected: str | int,
) -> None:
    monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert getattr(settings, attribute) == expected


@pytest.mark.parametrize("percent", [-1, 101])
def test_router_cohort_percent_is_bounded(percent: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, router_cohort_percent=percent)


def test_router_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, router_mode="enabled")


@pytest.mark.parametrize(
    "payload",
    [
        {"route": "UNKNOWN"},
        {"route": "DIRECT_TOOL", "target_tool": "create_task"},
        {"route": "STRUCTURED_READ", "target_tool": "memory_search"},
        {"route": "TASK_ACTION", "action_domain": "memory_save"},
        {"route": "MEMORY_ACTION", "action_domain": "task"},
        {"route": "TASK_ACTION", "action_domain": "task", "title": "write this"},
        {"route": "MEMORY_ACTION", "action_domain": "memory_save", "content": "secret"},
        {"route": "GENERAL_LLM", "target_tool": "get_current_time"},
    ],
)
def test_route_decision_rejects_invalid_or_executable_write_data(payload: dict) -> None:
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(payload)


def test_route_decision_accepts_write_domain_without_arguments() -> None:
    decision = RouteDecision(
        route=RouteName.MEMORY_ACTION,
        action_domain=ActionDomain.MEMORY_SAVE,
    )

    assert decision.target_tool is None
    assert "content" not in decision.model_dump()


class _CountingGraph:
    def __init__(self, result: dict | None = None) -> None:
        self.calls = 0
        self.result = result or {"outcome": {"route": "GENERAL_LLM", "needs_clarification": False}}

    async def ainvoke(self, input: dict, *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        return self.result


class _DecisionEchoGraph(_CountingGraph):
    async def ainvoke(self, input: dict, *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        return {
            "outcome": {
                "route": input["decision"]["route"],
                "needs_clarification": input["decision"]["route"] == "MIXED_AMBIGUOUS",
            }
        }


class _SlowGraph(_CountingGraph):
    async def ainvoke(self, input: dict, *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        await asyncio.sleep(1)
        return self.result


class _FailingGraph(_CountingGraph):
    async def ainvoke(self, input: dict, *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        raise ValueError("Invalid route result")


@pytest.mark.asyncio
async def test_off_mode_does_not_compile_or_invoke_graph() -> None:
    graph = _CountingGraph()
    service = DecisionRouterService(Settings(_env_file=None), graph=graph)

    result = await service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=_context(),
    )

    assert result.status == RouterRunStatus.DISABLED
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_cancellation_before_graph_falls_back_without_invoking() -> None:
    graph = _CountingGraph()
    settings = Settings(
        _env_file=None,
        router_mode="on",
        router_timeout_ms=500,
    )
    service = DecisionRouterService(settings, graph=graph)

    result = await service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=_context(cancellation_check=lambda: True),
    )

    assert result.status == RouterRunStatus.CANCELLED
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_timeout_returns_safe_legacy_fallback() -> None:
    graph = _SlowGraph()
    settings = Settings(
        _env_file=None,
        router_mode="on",
        router_timeout_ms=1,
    )
    service = DecisionRouterService(settings, graph=graph)

    result = await service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=_context(),
    )

    assert result.status == RouterRunStatus.TIMED_OUT
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 1


@pytest.mark.asyncio
async def test_graph_failure_returns_safe_legacy_fallback() -> None:
    graph = _FailingGraph()
    settings = Settings(_env_file=None, router_mode="on", router_timeout_ms=500)
    service = DecisionRouterService(settings, graph=graph)

    result = await service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=_context(),
    )

    assert result.status == RouterRunStatus.FAILED
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 1


@pytest.mark.asyncio
async def test_shadow_decision_keeps_legacy_orchestrator_authoritative() -> None:
    graph = _CountingGraph()
    settings = Settings(
        _env_file=None,
        router_mode="shadow",
        router_cohort_percent=100,
    )
    service = DecisionRouterService(settings, graph=graph)

    result = await service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=_context(),
    )

    assert result.status == RouterRunStatus.DECIDED
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 1


@pytest.mark.asyncio
async def test_shadow_observation_logs_safe_disagreement_without_transcript(caplog) -> None:
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    graph = _DecisionEchoGraph()
    service = DecisionRouterService(
        Settings(_env_file=None, router_mode="shadow", router_cohort_percent=100),
        graph=graph,
    )
    transcript = "What time is it?"

    result = await service.observe_shadow_transcript(
        transcript,
        context=_context(),
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )

    assert result.status == RouterRunStatus.DECIDED
    assert result.use_legacy_orchestrator is True
    assert result.outcome is not None and result.outcome.route == RouteName.DIRECT_TOOL
    assert graph.calls == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "router.shadow.observation"
    )
    assert record.shadow_route == "DIRECT_TOOL"
    assert record.target_tool == "get_current_time"
    assert record.decision_source == "rule"
    assert record.legacy_route == "GENERAL_LLM"
    assert record.disagreement is True
    assert record.disagreement_category == "GENERAL_LLM->DIRECT_TOOL"
    assert transcript not in repr(record.__dict__)


@pytest.mark.asyncio
async def test_shadow_memory_query_is_skipped_when_retrieval_is_disabled_or_opted_out(
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    graph = _DecisionEchoGraph()
    service = DecisionRouterService(
        Settings(_env_file=None, router_mode="shadow", router_cohort_percent=100),
        graph=graph,
    )

    result = await service.observe_shadow_transcript(
        "What is my favorite color?",
        context=_context(),
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )

    assert result.status == RouterRunStatus.SKIPPED_MEMORY_POLICY
    assert result.decision is not None and result.decision.route == RouteName.MEMORY_QUERY
    assert graph.calls == 0
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "router.shadow.observation"
    )
    assert record.fallback_reason == "memory_retrieval_disabled_or_opted_out"


@pytest.mark.asyncio
async def test_shadow_observation_stays_off_by_default() -> None:
    graph = _DecisionEchoGraph()
    service = DecisionRouterService(Settings(_env_file=None), graph=graph)

    result = await service.observe_shadow_transcript(
        "What time is it?",
        context=_context(),
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )

    assert result.status == RouterRunStatus.DISABLED
    assert graph.calls == 0


@pytest.mark.asyncio
async def test_shadow_observation_skips_when_shared_capacity_is_full(caplog) -> None:
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    graph = _DecisionEchoGraph()
    semaphore = asyncio.Semaphore(0)
    service = DecisionRouterService(
        Settings(_env_file=None, router_mode="shadow", router_cohort_percent=100),
        graph=graph,
        shadow_semaphore=semaphore,
    )

    result = await service.observe_shadow_transcript(
        "What time is it?",
        context=_context(),
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )

    assert result.status == RouterRunStatus.SKIPPED_CAPACITY
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 0
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "router.shadow.observation"
    )
    assert record.fallback_reason == "shadow_concurrency_limit_reached"


@pytest.mark.asyncio
async def test_shadow_observation_rejects_graph_route_mismatch(caplog) -> None:
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    graph = _CountingGraph(
        result={"outcome": {"route": "DIRECT_TOOL", "needs_clarification": False}}
    )
    service = DecisionRouterService(
        Settings(_env_file=None, router_mode="shadow", router_cohort_percent=100),
        graph=graph,
    )

    result = await service.observe_shadow_transcript(
        "Explain how reminders work.",
        context=_context(),
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )

    assert result.status == RouterRunStatus.FAILED
    assert result.use_legacy_orchestrator is True
    assert graph.calls == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "router.shadow.observation"
    )
    assert record.shadow_route == "GENERAL_LLM"
    assert record.fallback_reason == "failed"
    assert record.disagreement is None


def test_legacy_shadow_comparison_uses_observed_tool_route() -> None:
    assert VoiceGateway._legacy_route_from_result(
        {"status": "completed", "executed_tool_names": ["list_reminders"]}
    ) == RouteName.STRUCTURED_READ
    assert VoiceGateway._legacy_route_from_result(
        {"status": "confirmation_required", "proposed_tool_names": ["create_task"]}
    ) == RouteName.TASK_ACTION
    assert VoiceGateway._legacy_route_from_result(
        {"status": "completed", "executed_tool_names": ["get_current_date", "list_tasks"]}
    ) == RouteName.MIXED_AMBIGUOUS


@pytest.mark.asyncio
async def test_gateway_shadow_observation_emits_no_websocket_events() -> None:
    class _RecordingService:
        def __init__(self) -> None:
            self.arguments = None

        async def observe_shadow_transcript(self, transcript: str, **kwargs) -> None:
            self.arguments = (transcript, kwargs)

    user_id, session_id, turn_id, response_id = (uuid.uuid4() for _ in range(4))
    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.principal = SimpleNamespace(user_id=user_id)
    gateway._closing = asyncio.Event()
    gateway._response_was_cancelled = lambda _response_id: False
    gateway.router_service = _RecordingService()
    gateway._router_shadow_tasks = set()
    gateway._send = AsyncMock()

    gateway._schedule_router_shadow_observation(
        transcript="What time is it?",
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        legacy_route=RouteName.GENERAL_LLM,
        memory_route_allowed=False,
    )
    await asyncio.gather(*list(gateway._router_shadow_tasks))

    assert gateway.router_service.arguments is not None
    transcript, arguments = gateway.router_service.arguments
    assert transcript == "What time is it?"
    assert arguments["context"].user_id == user_id
    assert arguments["context"].session_id == session_id
    assert arguments["context"].turn_id == turn_id
    assert arguments["context"].response_id == response_id
    gateway._send.assert_not_awaited()


def test_cohort_selection_is_stable_and_bounded() -> None:
    service = DecisionRouterService(
        Settings(_env_file=None, router_mode="canary", router_cohort_percent=37)
    )
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000101")

    selection = service._in_cohort(RouterMode.CANARY, user_id)

    assert service._in_cohort(RouterMode.CANARY, user_id) is selection
    assert not DecisionRouterService(
        Settings(_env_file=None, router_mode="canary", router_cohort_percent=0)
    )._in_cohort(RouterMode.CANARY, user_id)
    assert DecisionRouterService(
        Settings(_env_file=None, router_mode="canary", router_cohort_percent=100)
    )._in_cohort(RouterMode.CANARY, user_id)


def test_graph_compiles_and_routes_only_to_allowed_terminal_nodes() -> None:
    pytest.importorskip("langgraph")
    from app.routing.graph import build_router_graph

    graph = build_router_graph()
    graph_view = graph.get_graph()
    edge_pairs = {(edge.source, edge.target) for edge in graph_view.edges}
    route_nodes = {f"route_{route.value.lower()}" for route in RouteName}

    assert {"__start__", "validate_decision", "__end__"}.issubset(graph_view.nodes)
    assert route_nodes.issubset(graph_view.nodes)
    assert {("validate_decision", name) for name in route_nodes}.issubset(edge_pairs)
    assert {(name, "__end__") for name in route_nodes}.issubset(edge_pairs)


@pytest.mark.asyncio
async def test_graph_route_outcomes_are_non_executable_and_mixed_requires_clarification() -> None:
    pytest.importorskip("langgraph")
    from app.routing.graph import build_router_graph

    graph = build_router_graph()
    result = await graph.ainvoke(
        {"decision": {"route": "MIXED_AMBIGUOUS"}},
        context=_context(),
    )

    assert result["outcome"] == {
        "route": "MIXED_AMBIGUOUS",
        "needs_clarification": True,
    }


@pytest.mark.asyncio
async def test_graph_checks_cancellation_in_runtime_context() -> None:
    pytest.importorskip("langgraph")
    from app.routing.graph import build_router_graph

    graph = build_router_graph()
    checks = iter((False, True))
    context = _context(cancellation_check=lambda: next(checks))
    settings = Settings(
        _env_file=None,
        router_mode="on",
        router_timeout_ms=500,
    )
    result_service = DecisionRouterService(settings, graph=graph)

    result = await result_service.decide(
        RouteDecision(route=RouteName.GENERAL_LLM),
        context=context,
    )

    assert result.status == RouterRunStatus.CANCELLED
    assert result.use_legacy_orchestrator is True
