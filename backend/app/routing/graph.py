from __future__ import annotations

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.routing.models import (
    RouteDecision,
    RouteName,
    RouterCancelledError,
    RouterOutcome,
    RouterRuntimeContext,
)


class RouterGraphState(TypedDict):
    decision: dict[str, object]
    outcome: NotRequired[dict[str, object]]


_NODE_BY_ROUTE = {route: f"route_{route.value.lower()}" for route in RouteName}


def _validate_decision(
    state: RouterGraphState,
    runtime: Runtime[RouterRuntimeContext],
) -> dict[str, object]:
    if runtime.context.cancellation_check():
        raise RouterCancelledError("The voice response was cancelled before routing.")
    decision = RouteDecision.model_validate(state["decision"])
    return {"decision": decision.model_dump(mode="json")}


def _select_route(state: RouterGraphState) -> str:
    decision = RouteDecision.model_validate(state["decision"])
    return decision.route.value


def _route_sink(route: RouteName):
    def route_node(
        _state: RouterGraphState,
        runtime: Runtime[RouterRuntimeContext],
    ) -> dict[str, object]:
        if runtime.context.cancellation_check():
            raise RouterCancelledError("The voice response was cancelled during routing.")
        outcome = RouterOutcome(
            route=route,
            needs_clarification=route == RouteName.MIXED_AMBIGUOUS,
        )
        return {"outcome": outcome.model_dump(mode="json")}

    return route_node


def build_router_graph():
    """Compile a route-dispatch skeleton with no model, tool, DB, or TTS effects."""

    builder = StateGraph(RouterGraphState, context_schema=RouterRuntimeContext)
    builder.add_node("validate_decision", _validate_decision)
    for route, node_name in _NODE_BY_ROUTE.items():
        builder.add_node(node_name, _route_sink(route))
        builder.add_edge(node_name, END)

    builder.add_edge(START, "validate_decision")
    builder.add_conditional_edges(
        "validate_decision",
        _select_route,
        {route.value: _NODE_BY_ROUTE[route] for route in RouteName},
    )
    return builder.compile()
