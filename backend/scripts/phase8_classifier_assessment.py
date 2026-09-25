"""Measure whether the frozen utterance corpus justifies a semantic classifier."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.llm.context import classify_voice_tool_choice
from app.llm.tool_loop import create_default_tool_registry
from app.memory.tool_tools import register_memory_tools
from app.routing.models import RouteDecision, RouteName
from app.routing.rules import classify_transcript

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "docs" / "phase0_router_acceptance_corpus_v1.json"
PHASE7_CASES = (
    (
        "p7-forget-exact",
        "Forget that I prefer cappuccino.",
        "MEMORY_ACTION",
        "memory_forget",
        False,
    ),
    (
        "p7-forget-description",
        "Forget my saved coffee preference.",
        "MEMORY_ACTION",
        "memory_forget",
        False,
    ),
    (
        "p7-forget-ambiguous",
        "Forget my saved preferences.",
        "MEMORY_ACTION",
        "memory_forget",
        False,
    ),
    (
        "p7-forget-missing",
        "Forget my preference for a purple helicopter.",
        "MEMORY_ACTION",
        "memory_forget",
        False,
    ),
    ("p7-forget-information", "How do I forget a saved memory?", "GENERAL_LLM", None, False),
    ("p7-reminder-information", "How do I create a reminder?", "GENERAL_LLM", None, False),
    (
        "p7-joke-keyword-mention",
        "Tell me a joke about memory and tasks.",
        "GENERAL_LLM",
        None,
        False,
    ),
    ("p7-task-action", "Remind me to call Rahul tomorrow at 9 AM.", "TASK_ACTION", "task", False),
    ("p7-memory-save", "Remember that I prefer cappuccino.", "MEMORY_ACTION", "memory_save", False),
    (
        "p7-multi-memory-action",
        "Forget my coffee preference and remember my meeting.",
        "MIXED_AMBIGUOUS",
        None,
        True,
    ),
)
TOOL_ROUTE = {
    "get_current_time": RouteName.DIRECT_TOOL,
    "get_current_date": RouteName.DIRECT_TOOL,
    "list_tasks": RouteName.STRUCTURED_READ,
    "list_reminders": RouteName.STRUCTURED_READ,
    "memory_search": RouteName.MEMORY_QUERY,
    "memory_save": RouteName.MEMORY_ACTION,
    "memory_forget": RouteName.MEMORY_ACTION,
    "create_task": RouteName.TASK_ACTION,
    "create_reminder": RouteName.TASK_ACTION,
    "update_task": RouteName.TASK_ACTION,
    "delete_task": RouteName.TASK_ACTION,
    "complete_task": RouteName.TASK_ACTION,
    "update_reminder": RouteName.TASK_ACTION,
    "delete_reminder": RouteName.TASK_ACTION,
}


def assess_corpus() -> dict[str, Any]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cases = [case for case in corpus["cases"] if case.get("operation", "classify") == "classify"]
    cases.extend(
        {
            "id": case_id,
            "utterance": utterance,
            "expected_route": route,
            "expected_action_domain": domain,
            "requires_clarification": clarification,
        }
        for case_id, utterance, route, domain, clarification in PHASE7_CASES
    )
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=True)
    definitions = registry.definitions()
    route_counts: Counter[str] = Counter()
    matched = 0
    errors: list[dict[str, str]] = []
    legacy_named = 0
    legacy_auto = 0
    disagreements: list[dict[str, str]] = []
    action_false_positives = {"task": 0, "memory": 0, "direct_tool": 0}

    for case in cases:
        utterance = case.get("utterance", "")
        decision: RouteDecision = classify_transcript(utterance)
        route_counts[decision.route.value] += 1
        target = decision.target_tool or (
            decision.action_domain.value if decision.action_domain is not None else None
        )
        expected_target = case.get("expected_target_tool") or case.get("expected_action_domain")
        clarification = decision.route == RouteName.MIXED_AMBIGUOUS
        matches = (
            decision.route.value == case["expected_route"]
            and target == expected_target
            and clarification == bool(case.get("requires_clarification", False))
        )
        if matches:
            matched += 1
        else:
            errors.append(
                {
                    "id": case["id"],
                    "expected_route": case["expected_route"],
                    "actual_route": decision.route.value,
                    "expected_target": str(expected_target),
                    "actual_target": str(target),
                }
            )
            if decision.route == RouteName.TASK_ACTION:
                action_false_positives["task"] += 1
            elif decision.route == RouteName.MEMORY_ACTION:
                action_false_positives["memory"] += 1
            elif decision.route == RouteName.DIRECT_TOOL:
                action_false_positives["direct_tool"] += 1

        choice = classify_voice_tool_choice(utterance, definitions)
        tool_name = getattr(getattr(choice, "function", None), "name", None)
        if tool_name is None:
            legacy_auto += 1
            continue
        legacy_named += 1
        legacy_route = TOOL_ROUTE.get(tool_name)
        if legacy_route is not None and legacy_route != decision.route:
            disagreements.append(
                {
                    "id": case["id"],
                    "rule_route": decision.route.value,
                    "legacy_named_tool": tool_name,
                    "legacy_tool_route": legacy_route.value,
                }
            )

    return {
        "corpus_id": corpus["corpus_id"],
        "version": corpus["version"],
        "deterministic_case_count": len(cases),
        "decision_coverage": len(cases),
        "expected_decision_matches": matched,
        "route_counts": dict(sorted(route_counts.items())),
        "legacy_named_tool_choices": legacy_named,
        "legacy_auto_tool_choices_unresolved": legacy_auto,
        "named_tool_route_disagreements": len(disagreements),
        "legacy_false_executable_routes": len(disagreements),
        "disagreements": disagreements,
        "errors": errors,
        "phase7_cases": len(PHASE7_CASES),
        "false_executable_routes": sum(action_false_positives.values()),
        "false_executable_routes_by_kind": action_false_positives,
        "model_calls": 0,
        "retrieval_calls": 0,
        "writes": 0,
    }


def main() -> int:
    report = assess_corpus()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["expected_decision_matches"] == report["deterministic_case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
