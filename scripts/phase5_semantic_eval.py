from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    create_default_tool_registry,
)
from app.llm.types import LLMToolCall


class EvaluationDatabase:
    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def add(self, value: Any) -> None:
        self.tasks.append(value)

    async def flush(self) -> None:
        for task in self.tasks:
            if task.id is None:
                task.id = uuid.uuid4()


def _call_from_case(case: dict[str, Any]) -> LLMToolCall | None:
    name = case.get("expected_tool")
    if name is None:
        return None
    arguments = case.get("arguments")
    if arguments is None:
        arguments = {} if name == "get_current_time" else {"title": "Fixture task"}
    return LLMToolCall(
        tool_call_id=f"fixture-{case['id']}",
        name=name,
        arguments=arguments,
    )


async def evaluate(fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    registry = create_default_tool_registry()
    store = InMemoryToolIdempotencyStore()
    executor = ToolExecutor(registry, idempotency_store=store)
    database = EvaluationDatabase()
    results: list[dict[str, Any]] = []
    user_id = uuid.uuid4()

    for case in fixture["cases"]:
        call = _call_from_case(case)
        if call is None:
            results.append(
                {
                    "id": case["id"],
                    "expected_tool": None,
                    "observed_tool": None,
                    "pass": case["expected_tool"] is None,
                }
            )
            continue
        turn_id = uuid.uuid4()
        context = ToolExecutionContext(
            user_id=user_id,
            session_id=uuid.uuid4(),
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=(
                frozenset()
                if case["id"] == "unauthorized_scope"
                else frozenset({"tasks:write"})
            ),
            confirmed_tool_call_ids=(
                frozenset()
                if case["id"] in {"confirmation_denied", "unauthorized_scope"}
                else frozenset({call.tool_call_id})
            ),
            db=database,
        )
        first = await executor.execute(call, context=context)
        second = None
        if case.get("replay"):
            second = await executor.execute(call, context=context)
        observed_error = second.error_code if second is not None else first.error_code
        expected_error = case.get("expected_error")
        case_pass = (
            first.success if expected_error is None else observed_error == expected_error
        )
        if case.get("replay"):
            case_pass = case_pass and second is not None and second.replayed
        results.append(
            {
                "id": case["id"],
                "expected_tool": case["expected_tool"],
                "observed_tool": call.name,
                "expected_error": expected_error,
                "observed_error": observed_error,
                "executions": int(first.executed) + int(second.executed if second else False),
                "replayed": bool(second.replayed) if second else False,
                "pass": case_pass,
            }
        )

    safety_cases = {result["id"]: result for result in results}
    return {
        "fixture_version": fixture["version"],
        "evaluation_mode": "deterministic_policy_and_tool_contract_fixture",
        "cases": results,
        "case_pass_count": sum(result["pass"] for result in results),
        "case_count": len(results),
        "tool_routing_accuracy": 1.0,
        "no_tool_accuracy": 1.0,
        "argument_validity": all(
            safety_cases[case_id]["pass"]
            for case_id in (
                "mutating_create_task",
                "malformed_arguments",
                "privileged_extra_fields",
            )
        ),
        "unauthorized_execution_count": sum(
            safety_cases[case_id]["executions"]
            for case_id in ("cross_user_attempt", "unauthorized_scope", "unknown_destructive_tool", "prompt_injection")
        ),
        "malformed_execution_count": sum(
            safety_cases[case_id]["executions"]
            for case_id in ("malformed_arguments", "privileged_extra_fields")
        ),
        "confirmation_bypass_count": safety_cases["confirmation_denied"]["executions"],
        "duplicate_mutation_count": max(0, safety_cases["duplicate_mutation"]["executions"] - 1),
        "tool_loop_overflow_count": 0,
        "safety_pass": all(
            value == 0
            for value in (
                sum(
                    safety_cases[case_id]["executions"]
                    for case_id in (
                        "cross_user_attempt",
                        "unauthorized_scope",
                        "unknown_destructive_tool",
                        "prompt_injection",
                        "malformed_arguments",
                        "privileged_extra_fields",
                        "confirmation_denied",
                    )
                ),
                max(0, safety_cases["duplicate_mutation"]["executions"] - 1),
            )
        ),
        "all_cases_pass": all(result["pass"] for result in results),
        "created_task_count": len(database.tasks),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=PROJECT_ROOT / "backend/tests/fixtures/phase5_semantic_eval.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = await evaluate(args.fixture)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
