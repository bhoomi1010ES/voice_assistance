"""Evaluate recorded Phase 6 retrieval results without fabricating provider evidence.

The production runner must write one result object per case in the versioned
corpus.  This evaluator only scores supplied observations; with no observations
it emits BLOCKED rather than treating an empty run as a passing result.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "backend/tests/fixtures/phase6_retrieval_corpus_v1.json"


def _ranked_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int))]


def _recall(expected: set[str], observed: list[str], limit: int) -> float | None:
    if not expected:
        return None
    return len(expected.intersection(observed[:limit])) / len(expected)


def _reciprocal_rank(expected: set[str], observed: list[str]) -> float | None:
    if not expected:
        return None
    for index, memory_id in enumerate(observed, start=1):
        if memory_id in expected:
            return 1.0 / index
    return 0.0


def _ndcg(expected: set[str], observed: list[str], limit: int) -> float | None:
    if not expected:
        return None
    relevance = [1 if item in expected else 0 for item in observed[:limit]]
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevance))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(len(expected), limit)))
    return dcg / ideal if ideal else 0.0


def evaluate(corpus: dict[str, Any], results: dict[str, Any] | None) -> dict[str, Any]:
    cases = corpus.get("cases", [])
    if results is None:
        return {
            "status": "BLOCKED",
            "reason": "No recorded production retrieval observations were supplied.",
            "corpus_id": corpus.get("corpus_id"),
            "corpus_version": corpus.get("version"),
            "case_count": len(cases),
            "observed_case_count": 0,
            "metrics": None,
        }

    observations = {
        str(item.get("id")): item
        for item in results.get("cases", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    scored: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case["id"])
        observation = observations.get(case_id)
        expected = {str(item) for item in case.get("expected_ids", [])}
        if observation is None:
            scored.append({"id": case_id, "status": "MISSING"})
            continue
        row: dict[str, Any] = {"id": case_id, "status": "OBSERVED"}
        for source in ("structured", "hnsw", "hybrid", "reranked"):
            ranked = _ranked_ids(observation.get(f"{source}_ids"))
            row[f"{source}_recall_at_8"] = _recall(expected, ranked, 8)
            row[f"{source}_mrr"] = _reciprocal_rank(expected, ranked)
            row[f"{source}_ndcg_at_8"] = _ndcg(expected, ranked, 8)
        row["latency_ms"] = observation.get("latency_ms")
        scored.append(row)

    metric_names = (
        "structured_recall_at_8",
        "hnsw_recall_at_8",
        "hybrid_recall_at_8",
        "reranked_recall_at_8",
        "structured_mrr",
        "hnsw_mrr",
        "hybrid_mrr",
        "reranked_mrr",
        "structured_ndcg_at_8",
        "hnsw_ndcg_at_8",
        "hybrid_ndcg_at_8",
        "reranked_ndcg_at_8",
    )
    metrics = {
        name: _mean(
            row[name] for row in scored if isinstance(row.get(name), (int, float))
        )
        for name in metric_names
    }
    latencies = sorted(
        float(row["latency_ms"])
        for row in scored
        if isinstance(row.get("latency_ms"), (int, float))
    )
    metrics.update(
        {
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_p99_ms": _percentile(latencies, 0.99),
        }
    )
    missing = [row["id"] for row in scored if row["status"] == "MISSING"]
    required_gates = corpus.get("hard_gates", {})
    observed_gates = results.get("hard_gates")
    missing_gates = [
        name
        for name in required_gates
        if not isinstance(observed_gates, dict) or name not in observed_gates
    ]
    nonzero_gates = {
        name: observed_gates[name]
        for name in required_gates
        if isinstance(observed_gates, dict)
        and name in observed_gates
        and observed_gates[name] != 0
    }
    metrics["reranked_mrr_improvement_over_hybrid"] = _mean(
        row["reranked_mrr"] - row["hybrid_mrr"]
        for row in scored
        if isinstance(row.get("reranked_mrr"), (int, float))
        and isinstance(row.get("hybrid_mrr"), (int, float))
    )
    metrics["reranked_recall_improvement_over_hybrid"] = _mean(
        row["reranked_recall_at_8"] - row["hybrid_recall_at_8"]
        for row in scored
        if isinstance(row.get("reranked_recall_at_8"), (int, float))
        and isinstance(row.get("hybrid_recall_at_8"), (int, float))
    )
    return {
        "status": "PASS"
        if not missing and not missing_gates and not nonzero_gates
        else "BLOCKED",
        "reason": (
            None
            if not missing and not missing_gates and not nonzero_gates
            else "Recorded observations or required zero-tolerance gates are incomplete/failed."
        ),
        "corpus_id": corpus.get("corpus_id"),
        "corpus_version": corpus.get("version"),
        "case_count": len(cases),
        "observed_case_count": len(observations),
        "missing_cases": missing,
        "missing_gates": missing_gates,
        "nonzero_gates": nonzero_gates,
        "metrics": metrics,
        "cases": scored,
    }


def _mean(values: Any) -> float | None:
    numbers = [float(value) for value in values]
    return sum(numbers) / len(numbers) if numbers else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, math.ceil(percentile * len(values)) - 1))
    return values[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    results = (
        json.loads(args.results.read_text(encoding="utf-8")) if args.results else None
    )
    report = json.dumps(evaluate(corpus, results), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
