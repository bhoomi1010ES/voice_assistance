"""Capture the deterministic pre-OKF RAG decision baseline.

This OKF-0 harness deliberately starts after retrieval. It supplies labeled,
synthetic retrieval results to the existing query planner, memory evaluator,
and evidence context builder. This keeps the baseline reproducible without a
database or model provider while freezing the response dispositions and call
shape that ``KNOWLEDGE_MODE=rag`` must preserve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.memory.context import assemble_evidence_context
from app.memory.evaluation import MemoryEvaluationRoute, evaluate_memory_result
from app.memory.types import (
    FusedMemory,
    MemoryRetrievalResult,
    MemoryStatus,
    MemoryType,
    build_memory_query_plan,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_CORPUS_PATH = BACKEND_ROOT / "tests" / "fixtures" / "okf_corpus_v1.json"
DEFAULT_CONTRACT_PATH = BACKEND_ROOT / "tests" / "fixtures" / "okf_contract_v1.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "docs" / "evidence" / "okf" / "okf0_rag_baseline_20260929.json"
CONTEXT_MAX_CHARS = 12_000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((percentile / 100) * (len(ordered) - 1))
    return ordered[index]


def _duration_summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values, default=0.0), 6),
        "p50": round(_percentile(values, 50), 6),
        "p95": round(_percentile(values, 95), 6),
        "p99": round(_percentile(values, 99), 6),
        "max": round(max(values, default=0.0), 6),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _memory_from_source(
    source: dict[str, Any],
    *,
    rank: int,
    owner_id: uuid.UUID,
    other_owner_id: uuid.UUID,
    now: datetime,
) -> FusedMemory:
    return FusedMemory(
        memory_id=uuid.uuid5(uuid.NAMESPACE_URL, f"okf-corpus:{source['id']}"),
        user_id=other_owner_id if source.get("owner") == "other" else owner_id,
        content=source["content"],
        score=float(source.get("score", 1.0 / rank)),
        rank=rank,
        sources=tuple(source.get("sources", ())),
        created_at=now,
        memory_type=MemoryType(source["memory_type"]),
        subject=source.get("subject"),
        predicate=source.get("predicate"),
        object_json=source.get("object_json"),
        status=MemoryStatus(source.get("status", "active")),
        valid_from=(
            datetime.fromisoformat(source["valid_from"]) if source.get("valid_from") else None
        ),
        valid_to=(datetime.fromisoformat(source["valid_to"]) if source.get("valid_to") else None),
    )


def capture_baseline(
    *,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> dict[str, Any]:
    """Run the frozen offline corpus through the existing RAG decision boundary."""

    corpus = _load_json(corpus_path)
    contract = _load_json(contract_path)
    if corpus.get("policy_version") != contract.get("policy_version"):
        raise ValueError("corpus and contract policy versions differ")

    owner_id = uuid.UUID(corpus["owner_id"])
    other_owner_id = uuid.UUID(corpus["other_owner_id"])
    now = datetime.fromisoformat(corpus["now"]).astimezone(UTC)
    failures: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    routes: Counter[str] = Counter()
    planner_latencies: list[float] = []
    evaluation_latencies: list[float] = []
    context_latencies: list[float] = []
    total_latencies: list[float] = []
    context_calls = 0

    for case in corpus["cases"]:
        total_started = time.perf_counter_ns()
        rag = case["rag"]
        source_by_id = {source["id"]: source for source in case["sources"]}
        selected_sources = [source_by_id[source_id] for source_id in rag.get("records", [])]

        planner_started = time.perf_counter_ns()
        plan = build_memory_query_plan(rag["query"], now=now)
        planner_ms = (time.perf_counter_ns() - planner_started) / 1_000_000
        planner_latencies.append(planner_ms)

        memories = tuple(
            _memory_from_source(
                source,
                rank=index,
                owner_id=owner_id,
                other_owner_id=other_owner_id,
                now=now,
            )
            for index, source in enumerate(selected_sources, start=1)
        )
        retrieval = MemoryRetrievalResult(
            status=rag.get("retrieval_status", "ready"),
            plan=plan,
            memories=memories,
        )

        evaluation_started = time.perf_counter_ns()
        evaluation = evaluate_memory_result(
            retrieval,
            user_id=owner_id,
            query=rag["query"],
            now=now,
        )
        evaluation_ms = (time.perf_counter_ns() - evaluation_started) / 1_000_000
        evaluation_latencies.append(evaluation_ms)
        routes[evaluation.route.value] += 1

        context_ms = 0.0
        context_characters = 0
        if evaluation.route == MemoryEvaluationRoute.RAG_PLUS_LLM and evaluation.evidence_ids:
            context_started = time.perf_counter_ns()
            evidence_ids = set(evaluation.evidence_ids)
            context = assemble_evidence_context(
                tuple(memory for memory in memories if memory.memory_id in evidence_ids),
                max_chars=CONTEXT_MAX_CHARS,
                conflicts_detected=evaluation.reason == "conflicting_evidence",
            )
            context_ms = (time.perf_counter_ns() - context_started) / 1_000_000
            context_characters = len(context.text)
            context_calls += 1
        context_latencies.append(context_ms)

        id_by_uuid = {
            uuid.uuid5(uuid.NAMESPACE_URL, f"okf-corpus:{source['id']}"): source["id"]
            for source in selected_sources
        }
        evidence_ids = [id_by_uuid[value] for value in evaluation.evidence_ids]
        case_failures: list[str] = []
        if evaluation.route.value != rag["expected_route"]:
            case_failures.append(
                f"route expected {rag['expected_route']} got {evaluation.route.value}"
            )
        if evidence_ids != rag.get("expected_evidence_ids", []):
            case_failures.append(
                f"evidence expected {rag.get('expected_evidence_ids', [])} got {evidence_ids}"
            )
        if rag.get("expected_reason") and evaluation.reason != rag["expected_reason"]:
            case_failures.append(
                f"reason expected {rag['expected_reason']} got {evaluation.reason}"
            )
        if case_failures:
            failures.append({"case_id": case["id"], "failures": case_failures})

        total_ms = (time.perf_counter_ns() - total_started) / 1_000_000
        total_latencies.append(total_ms)
        outputs.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "query_plan": {
                    "intent": plan.intent.value,
                    "memory_types": [item.value for item in plan.memory_types],
                    "search_term_count": len(plan.search_terms),
                },
                "route": evaluation.route.value,
                "reason": evaluation.reason,
                "evidence_ids": evidence_ids,
                "context_characters": context_characters,
                "latency_ms": {
                    "query_plan": round(planner_ms, 6),
                    "evaluation": round(evaluation_ms, 6),
                    "context": round(context_ms, 6),
                    "total": round(total_ms, 6),
                },
            }
        )

    case_count = len(corpus["cases"])
    return {
        "baseline_id": "okf0-rag-decision-baseline-20260929",
        "status": "PASS" if not failures else "FAIL",
        "captured_at": datetime.now(UTC).isoformat(),
        "scope": {
            "kind": "offline_post_retrieval_rag_decision_baseline",
            "database_used": False,
            "embedding_provider_used": False,
            "reranker_used": False,
            "llm_used": False,
            "runtime_configuration_changed": False,
            "note": (
                "Provider/database retrieval quality remains covered by existing Phase 6 evidence; "
                "this baseline freezes deterministic planning, evaluation, and context behavior."
            ),
        },
        "corpus": {
            "id": corpus["corpus_id"],
            "version": corpus["version"],
            "sha256": _sha256(corpus_path),
            "case_count": case_count,
        },
        "contract": {
            "id": contract["contract_id"],
            "version": contract["version"],
            "sha256": _sha256(contract_path),
        },
        "call_counts": {
            "query_plan": case_count,
            "retrieval_fixture_input": case_count,
            "memory_evaluation": case_count,
            "context_builder": context_calls,
            "expected_llm_from_disposition": routes[MemoryEvaluationRoute.RAG_PLUS_LLM.value],
            "expected_direct_or_terminal_response": case_count
            - routes[MemoryEvaluationRoute.RAG_PLUS_LLM.value],
            "embedding_provider": 0,
            "reranker": 0,
        },
        "route_counts": dict(sorted(routes.items())),
        "latency_ms": {
            "query_plan": _duration_summary(planner_latencies),
            "evaluation": _duration_summary(evaluation_latencies),
            "context": _duration_summary(context_latencies),
            "total": _duration_summary(total_latencies),
        },
        "cases": outputs,
        "failures": failures,
    }


def write_baseline(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    arguments = parser.parse_args()
    result = capture_baseline(corpus_path=arguments.corpus, contract_path=arguments.contract)
    write_baseline(result, arguments.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "cases": result["corpus"]["case_count"],
                "output": str(arguments.output),
            },
            indent=2,
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
