"""Evaluate the production automatic memory-extraction decision boundary."""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import quantiles
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.memory.extraction import extract_explicit_candidates
from app.memory.types import normalize_memory_text

FIXTURE = BACKEND / "tests" / "fixtures" / "phase6_extraction_corpus_v1.json"
EVIDENCE = ROOT / "docs" / "evidence" / "phase6"

THRESHOLDS = {
    "cases": 40,
    "precision": 0.95,
    "recall": 0.90,
    "f1": 0.92,
    "explicit_opt_out_errors": 0,
    "forget_command_errors": 0,
    "tool_command_errors": 0,
    "sensitive_policy_violations": 0,
    "duplicate_memory_errors": 0,
}


def percentile(values: list[float], value: int) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return float(quantiles(values, n=100, method="inclusive")[value - 1])


def candidate_json(candidate: Any) -> dict[str, Any]:
    return {
        "memory_type": str(candidate.memory_type),
        "content": candidate.content,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "confidence": candidate.confidence,
        "salience": candidate.salience,
        "dedupe_key": candidate.dedupe_key,
    }


def evaluate() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    outputs: list[dict[str, Any]] = []
    latencies: list[float] = []
    duplicate_groups: defaultdict[str, list[str]] = defaultdict(list)
    for case in cases:
        started = time.perf_counter()
        candidates = extract_explicit_candidates(case["utterance"])
        latency = (time.perf_counter() - started) * 1000
        latencies.append(latency)
        predicted = bool(candidates)
        candidate = candidates[0] if candidates else None
        expected_type = case.get("memory_type")
        expected_content = case.get("content")
        type_correct = bool(
            not case["should_extract"]
            or candidate is not None
            and str(candidate.memory_type) == expected_type
        )
        content_correct = bool(
            not case["should_extract"]
            or candidate is not None
            and expected_content is not None
            and normalize_memory_text(candidate.content)
            == normalize_memory_text(expected_content)
        )
        subject_correct = bool(
            not case["should_extract"]
            or candidate is not None
            and candidate.subject == case.get("subject")
        )
        predicate_correct = bool(
            not case["should_extract"]
            or candidate is not None
            and candidate.predicate == case.get("predicate")
        )
        if candidate is not None and case.get("duplicate_group"):
            duplicate_groups[case["duplicate_group"]].append(candidate.dedupe_key)
        outputs.append(
            {
                "case_id": case["id"],
                "utterance": case["utterance"],
                "category": case["category"],
                "notes": case.get("notes"),
                "should_extract": case["should_extract"],
                "predicted_extract": predicted,
                "predicted_candidate": candidate_json(candidate) if candidate else None,
                "predicted_memory_type": str(candidate.memory_type)
                if candidate
                else None,
                "extracted_content": candidate.content if candidate else None,
                "confidence": candidate.confidence if candidate else None,
                "confirmation_required": False,
                "final_decision": "extract" if predicted else "reject",
                "type_correct": type_correct,
                "subject_correct": subject_correct,
                "predicate_correct": predicate_correct,
                "normalized_content_correct": content_correct,
                "latency_ms": round(latency, 6),
            }
        )

    tp = sum(item["should_extract"] and item["predicted_extract"] for item in outputs)
    tn = sum(
        not item["should_extract"] and not item["predicted_extract"] for item in outputs
    )
    fp = sum(
        not item["should_extract"] and item["predicted_extract"] for item in outputs
    )
    fn = sum(
        item["should_extract"] and not item["predicted_extract"] for item in outputs
    )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(outputs) if outputs else 0.0
    positives = [item for item in outputs if item["should_extract"]]
    category_stats: dict[str, dict[str, int]] = {}
    for category, category_cases in _group(outputs, "category").items():
        category_stats[category] = {
            "cases": len(category_cases),
            "expected_extract": sum(item["should_extract"] for item in category_cases),
            "predicted_extract": sum(
                item["predicted_extract"] for item in category_cases
            ),
            "false_positives": sum(
                not item["should_extract"] and item["predicted_extract"]
                for item in category_cases
            ),
            "false_negatives": sum(
                item["should_extract"] and not item["predicted_extract"]
                for item in category_cases
            ),
        }

    def errors_for(category: str) -> int:
        return sum(
            item["category"] == category and item["predicted_extract"]
            for item in outputs
        )

    duplicate_errors = sum(
        len(set(keys)) > 1 for keys in duplicate_groups.values() if len(keys) > 1
    )
    metrics = {
        "cases": len(outputs),
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "memory_type_accuracy": sum(item["type_correct"] for item in positives)
        / len(positives),
        "normalization_accuracy": sum(
            item["normalized_content_correct"] for item in positives
        )
        / len(positives),
        "subject_accuracy": sum(item["subject_correct"] for item in positives)
        / len(positives),
        "predicate_accuracy": sum(item["predicate_correct"] for item in positives)
        / len(positives),
        "malformed_extraction_count": sum(
            item["predicted_extract"] and not item["type_correct"] for item in outputs
        ),
        "explicit_opt_out_errors": sum(
            item["category"] == "explicit_opt_out" and item["predicted_extract"]
            for item in outputs
        ),
        "forget_command_errors": errors_for("forget_command"),
        "tool_command_errors": errors_for("tool_command"),
        "sensitive_policy_violations": errors_for("sensitive_candidate"),
        "duplicate_memory_errors": duplicate_errors,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "min": min(latencies) if latencies else 0.0,
            "max": max(latencies) if latencies else 0.0,
        },
    }
    threshold_status = {
        "cases": metrics["cases"] >= THRESHOLDS["cases"],
        "precision": metrics["precision"] >= THRESHOLDS["precision"],
        "recall": metrics["recall"] >= THRESHOLDS["recall"],
        "f1": metrics["f1"] >= THRESHOLDS["f1"],
        **{
            key: metrics[key] == threshold
            for key, threshold in THRESHOLDS.items()
            if key not in {"cases", "precision", "recall", "f1"}
        },
    }
    return {
        "evaluation_id": f"phase6-extraction-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
        "corpus_id": fixture["corpus_id"],
        "corpus_version": fixture["version"],
        "entry_point": "app.memory.extraction.extract_explicit_candidates",
        "production_caller": "app.memory.jobs.MemoryJobWorker._extract_turn",
        "metrics": metrics,
        "thresholds": THRESHOLDS,
        "threshold_status": threshold_status,
        "category_metrics": category_stats,
        "false_positives": [
            {
                "case_id": item["case_id"],
                "utterance": item["utterance"],
                "extracted_memory": item["predicted_candidate"],
                "reason": item["notes"],
            }
            for item in outputs
            if not item["should_extract"] and item["predicted_extract"]
        ],
        "false_negatives": [
            {
                "case_id": item["case_id"],
                "utterance": item["utterance"],
                "reason": item["notes"],
            }
            for item in outputs
            if item["should_extract"] and not item["predicted_extract"]
        ],
        "duplicate_groups": dict(duplicate_groups),
        "cases": outputs,
        "status": "PASS" if all(threshold_status.values()) else "FAIL",
    }


def _group(items: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[item[key]].append(item)
    return dict(groups)


def write_evidence(result: dict[str, Any]) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "phase6_extraction_cases.json").write_text(
        json.dumps(
            {"corpus_id": result["corpus_id"], "cases": result["cases"]}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    (EVIDENCE / "phase6_extraction_results.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    (EVIDENCE / "phase6_extraction_metrics.json").write_text(
        json.dumps(
            {
                "evaluation_id": result["evaluation_id"],
                "metrics": result["metrics"],
                "thresholds": result["thresholds"],
                "threshold_status": result["threshold_status"],
                "category_metrics": result["category_metrics"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (EVIDENCE / "phase6_extraction_failures.json").write_text(
        json.dumps(
            {
                "evaluation_id": result["evaluation_id"],
                "false_positives": result["false_positives"],
                "false_negatives": result["false_negatives"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (EVIDENCE / "phase6_extraction_latency.json").write_text(
        json.dumps(
            {
                "evaluation_id": result["evaluation_id"],
                "case_latency_ms": [
                    {"case_id": item["case_id"], "latency_ms": item["latency_ms"]}
                    for item in result["cases"]
                ],
                "aggregate": result["metrics"]["latency_ms"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    baseline_path = EVIDENCE / "phase6_extraction_baseline_metrics.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        comparison_keys = (
            "cases",
            "true_positives",
            "true_negatives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "f1",
            "accuracy",
            "memory_type_accuracy",
            "normalization_accuracy",
            "subject_accuracy",
            "predicate_accuracy",
            "malformed_extraction_count",
            "explicit_opt_out_errors",
            "forget_command_errors",
            "tool_command_errors",
            "sensitive_policy_violations",
            "duplicate_memory_errors",
        )
        before = baseline["metrics"]
        after = result["metrics"]
        (EVIDENCE / "phase6_extraction_before_after.json").write_text(
            json.dumps(
                {
                    "baseline_evaluation_id": baseline["evaluation_id"],
                    "final_evaluation_id": result["evaluation_id"],
                    "metrics": {
                        key: {"before": before[key], "after": after[key]}
                        for key in comparison_keys
                    },
                    "baseline_failures": {
                        "false_positives": json.loads(
                            (
                                EVIDENCE / "phase6_extraction_baseline_failures.json"
                            ).read_text(encoding="utf-8")
                        )["false_positives"],
                        "false_negatives": json.loads(
                            (
                                EVIDENCE / "phase6_extraction_baseline_failures.json"
                            ).read_text(encoding="utf-8")
                        )["false_negatives"],
                    },
                    "final_failures": {
                        "false_positives": result["false_positives"],
                        "false_negatives": result["false_negatives"],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report = ROOT / "docs" / f"{timestamp}_phase6_extraction_evaluation.md"
    metrics = result["metrics"]
    rows = [
        ("Cases", metrics["cases"], ">= 40", "cases"),
        ("TP", metrics["true_positives"], "record", None),
        ("TN", metrics["true_negatives"], "record", None),
        ("FP", metrics["false_positives"], "record", None),
        ("FN", metrics["false_negatives"], "record", None),
        ("Precision", f"{metrics['precision']:.3f}", ">= 0.950", "precision"),
        ("Recall", f"{metrics['recall']:.3f}", ">= 0.900", "recall"),
        ("F1", f"{metrics['f1']:.3f}", ">= 0.920", "f1"),
        ("Accuracy", f"{metrics['accuracy']:.3f}", "record", None),
        (
            "Memory-type accuracy",
            f"{metrics['memory_type_accuracy']:.3f}",
            "record",
            None,
        ),
        (
            "Normalization accuracy",
            f"{metrics['normalization_accuracy']:.3f}",
            "record",
            None,
        ),
        (
            "Explicit opt-out errors",
            metrics["explicit_opt_out_errors"],
            "0",
            "explicit_opt_out_errors",
        ),
        (
            "Forget-command errors",
            metrics["forget_command_errors"],
            "0",
            "forget_command_errors",
        ),
        (
            "Tool-command errors",
            metrics["tool_command_errors"],
            "0",
            "tool_command_errors",
        ),
        (
            "Duplicate-memory errors",
            metrics["duplicate_memory_errors"],
            "0",
            "duplicate_memory_errors",
        ),
        (
            "Sensitive-policy violations",
            metrics["sensitive_policy_violations"],
            "0",
            "sensitive_policy_violations",
        ),
        ("P50 latency", f"{metrics['latency_ms']['p50']:.3f} ms", "record", None),
        ("P95 latency", f"{metrics['latency_ms']['p95']:.3f} ms", "record", None),
        ("P99 latency", f"{metrics['latency_ms']['p99']:.3f} ms", "record", None),
    ]
    table = ["| Metric | Result | Threshold | Status |", "|---|---:|---:|---|"]
    for label, value, threshold, status_key in rows:
        status = (
            "recorded"
            if status_key is None
            else "PASS"
            if result["threshold_status"].get(status_key)
            else "FAIL"
        )
        table.append(f"| {label} | {value} | {threshold} | {status} |")
    report.write_text(
        "# Phase 6 automatic extraction evaluation\n\n"
        f"Run: `{result['evaluation_id']}`  \n"
        f"Entry point: `{result['entry_point']}` via `{result['production_caller']}`\n\n"
        + "\n".join(table)
        + "\n\n## False positives\n\n"
        + (
            "\n".join(
                f"- `{item['case_id']}`: {item['utterance']} — {item['reason']}"
                for item in result["false_positives"]
            )
            or "- None."
        )
        + "\n\n## False negatives\n\n"
        + (
            "\n".join(
                f"- `{item['case_id']}`: {item['utterance']} — {item['reason']}"
                for item in result["false_negatives"]
            )
            or "- None."
        )
        + f"\n\n## Verdict\n\n`PHASE 6 EXTRACTION EVALUATION: {result['status']}`\n",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    outcome = evaluate()
    report = write_evidence(outcome)
    print(
        json.dumps(
            {
                "status": outcome["status"],
                "report": str(report),
                "evidence": str(EVIDENCE),
            },
            indent=2,
        )
    )
    if outcome["status"] != "PASS":
        raise SystemExit(1)
