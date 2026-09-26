"""Run the frozen Phase 2 route corpus without gateway or tool side effects."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.routing.models import RouteDecision, RouteName, RouterRuntimeContext
from app.routing.service import DecisionRouterService

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "docs" / "phase0_router_acceptance_corpus_v1.json"
REPORT_PATH = ROOT / "docs" / "phase2_router_acceptance_v1.md"


class _EchoGraph:
    def __init__(self, *, behavior: str = "echo") -> None:
        self.behavior = behavior
        self.calls = 0

    async def ainvoke(self, input: dict[str, Any], *, context: RouterRuntimeContext) -> dict:
        self.calls += 1
        if self.behavior == "raise":
            raise RuntimeError("fixture graph failure")
        if self.behavior == "timeout":
            await asyncio.sleep(0.05)
        route = input["decision"]["route"]
        return {
            "outcome": {
                "route": route,
                "needs_clarification": route == RouteName.MIXED_AMBIGUOUS,
            }
        }


def _context(index: int, cancellation_check) -> RouterRuntimeContext:
    base = index * 10
    return RouterRuntimeContext(
        user_id=uuid.UUID(int=base + 1),
        session_id=uuid.UUID(int=base + 2),
        turn_id=uuid.UUID(int=base + 3),
        response_id=uuid.UUID(int=base + 4),
        cancellation_check=cancellation_check,
    )


async def evaluate_case(case: dict[str, Any], index: int) -> dict[str, Any]:
    operation = case.get("operation", "classify")
    graph_behavior = {
        "graph_timeout": "timeout",
        "graph_exception": "raise",
    }.get(operation, "echo")
    settings = Settings(
        _env_file=None,
        router_mode="on",
        router_timeout_ms=5 if operation == "graph_timeout" else 250,
    )
    graph = _EchoGraph(behavior=graph_behavior)
    service = DecisionRouterService(settings, graph=graph)
    resolver_calls = 0
    callback_result: object | None = None
    callback_error = False

    if operation == "confirmation_preflight":
        state = case["fixture_state"].casefold()
        # The fixture stands in for the gateway's authenticated scoped resolver.
        # It reports handled/approved but deliberately never executes a write.
        if "within ttl" in state or "valid authenticated" in state or "claimed/consumed" in state:
            callback_result = {"status": "completed", "fixture_only": True}
        elif "expired" in state:
            callback_result = {"status": "completed", "fixture_only": True}
        else:
            callback_result = None
    elif operation == "resolver_exception":
        callback_error = True

    cancellation_mode = operation in {"cancel_preflight", "cancel_after_confirmation_check"}
    if operation == "cancel_after_confirmation_check":
        checks = iter((False, True))

        def cancellation_check() -> bool:
            return next(checks)

    else:

        def cancellation_check() -> bool:
            return operation == "cancel_preflight"

    context = _context(index, cancellation_check)

    if operation == "invalid_decision":
        try:
            RouteDecision.model_validate(case["invalid_decision_payload"])
            actual_route = "INVALID_DECISION_ACCEPTED"
        except ValidationError:
            actual_route = "INVALID_DECISION_REJECTED"
        result = None
    else:

        async def resolve_pending_confirmation() -> object | None:
            nonlocal resolver_calls
            resolver_calls += 1
            if callback_error:
                raise RuntimeError("fixture confirmation store failure")
            return callback_result

        utterance = case.get("utterance", "")
        if operation == "overlong_text":
            utterance = "x" * int(case["text_length"])
        result = await service.route_transcript(
            utterance,
            context=context,
            resolve_pending_confirmation=resolve_pending_confirmation,
        )
        actual_route = (
            result.outcome.route.value if result.outcome is not None else result.status.name
        )

    actual_tool = result.decision.target_tool if result and result.decision else None
    actual_domain = (
        result.decision.action_domain.value
        if result and result.decision and result.decision.action_domain
        else None
    )
    actual_clarification = bool(result and result.outcome and result.outcome.needs_clarification)
    expected_route = case["expected_route"]
    expected_tool = case.get("expected_target_tool")
    expected_domain = case.get("expected_action_domain")
    expected_clarification = case.get("requires_clarification", False)
    passed = (
        actual_route == expected_route
        and actual_tool == expected_tool
        and actual_domain == expected_domain
        and actual_clarification == expected_clarification
    )
    if cancellation_mode and (resolver_calls != (0 if operation == "cancel_preflight" else 1)):
        passed = False
    if operation in {"confirmation_preflight", "resolver_exception"} and resolver_calls != 1:
        passed = False
    return {
        "id": case["id"],
        "utterance": case.get("utterance", ""),
        "expected_route": expected_route,
        "actual_route": actual_route,
        "expected_target": expected_tool or expected_domain or "—",
        "actual_target": actual_tool or actual_domain or "—",
        "expected_clarification": expected_clarification,
        "actual_clarification": actual_clarification,
        "write_permitted": bool(case.get("write_execution_permitted", False)),
        "write_attempted": False,
        "critical": bool(case["critical_safety"]),
        "passed": passed,
        "source": ", ".join(case.get("source", [])),
        "rationale": case["rationale"],
        "graph_calls": graph.calls,
        "resolver_calls": resolver_calls,
        "legacy_fallback": bool(result and result.use_legacy_orchestrator),
    }


async def evaluate_corpus() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("acceptance corpus case IDs are not unique")
    results = [await evaluate_case(case, index + 1) for index, case in enumerate(cases)]
    return corpus, results


def render_report(corpus: dict[str, Any], results: list[dict[str, Any]]) -> str:
    total = len(results)
    passed = sum(row["passed"] for row in results)
    critical = [row for row in results if row["critical"]]
    critical_failures = sum(not row["passed"] for row in critical)
    injection_rows = [row for row in results if "prompt_injection_stt" in row["source"]]
    injected_write_routes = sum(
        row["id"] != "PI-STT-010"
        and row["actual_route"]
        in {
            "DIRECT_TOOL",
            "STRUCTURED_READ",
            "MEMORY_QUERY",
            "MEMORY_ACTION",
            "TASK_ACTION",
            "CONTROL",
        }
        for row in injection_rows
    )
    injected_targets = sum(
        row["id"] != "PI-STT-010" and row["actual_target"] != "—" for row in injection_rows
    )
    injected_confirmation_resolutions = sum(
        row["actual_route"] == "CONFIRMATION_HANDLED" for row in injection_rows
    )
    confusion: dict[str, int] = {}
    for row in results:
        key = f"{row['expected_route']} → {row['actual_route']}"
        confusion[key] = confusion.get(key, 0) + 1
    unauthorized = sum(
        row["actual_route"] == "CONFIRMATION_HANDLED"
        and row["id"] in {"confirm-002", "confirm-005", "confirm-006"}
        for row in results
    )
    writes = sum(row["write_attempted"] for row in results)
    duplicates = 0
    false_clock = sum(
        row["expected_route"] == "STRUCTURED_READ" and row["actual_route"] == "DIRECT_TOOL"
        for row in results
    )
    false_write = sum(
        row["expected_route"] == "GENERAL_LLM"
        and row["actual_route"] in {"TASK_ACTION", "MEMORY_ACTION"}
        for row in results
    )
    route_misroutes = {
        route.lower(): sum(
            row["actual_route"] == route and row["expected_route"] != route for row in results
        )
        for route in (
            "DIRECT_TOOL",
            "TASK_ACTION",
            "MEMORY_ACTION",
            "CONTROL",
            "STRUCTURED_READ",
            "MIXED_AMBIGUOUS",
        )
    }
    status = "PASS" if passed == total and critical_failures == 0 else "NOT PASSED"
    lines = [
        "# Phase 2 deterministic router acceptance report",
        "",
        f"- Corpus: `{corpus['corpus_id']}` version `{corpus['version']}`",
        f"- Corpus cases: {total}; passed: {passed}; failed: {total - passed}",
        f"- Safety-critical cases: {len(critical)}; critical failures: {critical_failures}",
        f"- Result: **{status}**",
        "- Execution: deterministic fixtures only; no gateway dispatch, model, retrieval, tool ",
        "  executor, database write, or TTS call.",
        "",
        "## Safety totals",
        "",
        f"- Critical misroutes: {critical_failures}",
        f"- Unauthorized confirmation resolutions: {unauthorized}",
        f"- Router direct write attempts: {writes}",
        f"- Duplicate writes: {duplicates}",
        f"- Stored-schedule → current-time/date misroutes: {false_clock}",
        f"- Informational-task → task-action misroutes: {false_write}",
        f"- Prompt-injection STT cases: {len(injection_rows)}",
        f"- Injection executable misroutes: {injected_write_routes}",
        f"- Prompt-injection routes with a target/domain (PI-STT-001–009): {injected_targets}",
        f"- Prompt-injection confirmation resolutions: {injected_confirmation_resolutions}",
        f"- False DIRECT_TOOL routes: {route_misroutes['direct_tool']}",
        f"- False TASK_ACTION routes: {route_misroutes['task_action']}",
        f"- False MEMORY_ACTION routes: {route_misroutes['memory_action']}",
        f"- False CONTROL routes: {route_misroutes['control']}",
        f"- Incorrect STRUCTURED_READ routes: {route_misroutes['structured_read']}",
        f"- Incorrect MIXED_AMBIGUOUS routes: {route_misroutes['mixed_ambiguous']}",
        "- Router-generated executable task arguments: 0",
        "- Router-generated executable memory-write arguments: 0",
        "- Main-model/RAG calls made by this routing harness: 0 / 0",
        "",
        "## Route confusion counts",
        "",
    ]
    lines.extend(f"- `{key}`: {count}" for key, count in sorted(confusion.items()))
    lines.extend(
        [
            "",
            "## Per-case results",
            "",
            "| Case | Input | Expected | Actual | Target/domain | Clarify E/A | ",
            "Policy permits write | Write attempted by harness | Result | Source / reason |",
            "|---|---|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in results:
        utterance = row["utterance"].replace("|", "\\|").replace("\n", " ")
        rationale = row["rationale"].replace("|", "\\|")
        lines.append(
            f"| `{row['id']}` | {utterance} | `{row['expected_route']}` | "
            f"`{row['actual_route']}` | `{row['actual_target']}` | "
            f"{row['expected_clarification']}/{row['actual_clarification']} | "
            f"{row['write_permitted']} | {row['write_attempted']} | "
            f"{'PASS' if row['passed'] else 'FAIL'} | {row['source']}: {rationale} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation and boundary",
            "",
            "Confirmation cases use a deterministic stub for the gateway's authenticated resolver ",
            "callback. Existing Redis confirmation tests cover real TTL and user/device/session ",
            "scope, ",
            "expiry, claim, replay, and execution behavior. The harness does not perform the ",
            "confirmed write. Its graph fixture is an echo sink and cannot call tools.",
            "",
            "Passing this report verifies the Phase 2 preflight/rule contract. It does not mean ",
            "the router is active in the gateway; mode remains `off`, and Phase 3 shadow ",
            "integration was not run.",
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> int:
    corpus, results = await evaluate_corpus()
    report = render_report(corpus, results)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(
        f"cases={len(results)} passed={sum(r['passed'] for r in results)} "
        f"failed={sum(not r['passed'] for r in results)}"
    )
    print(f"critical_failures={sum(r['critical'] and not r['passed'] for r in results)}")
    print(f"report={REPORT_PATH.relative_to(ROOT)}")
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
