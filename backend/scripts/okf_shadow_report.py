"""Aggregate content-free OKF shadow observations into an unthresholded report."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "evidence" / "okf" / "okf6_shadow_report_latest.json"
_FORBIDDEN_KEYS = frozenset(
    {"query", "transcript", "content", "display_text", "canonical_key", "evidence_ids", "user_id"}
)


def _percentiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"p50": None, "p95": None, "p99": None, "max": None}

    def percentile(value: int) -> float:
        index = round((value / 100) * (len(ordered) - 1))
        return round(ordered[index], 3)

    return {
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "max": round(max(ordered), 3),
    }


def _metric_summary(
    values: list[float], *, timeout_count: int, error_count: int
) -> dict[str, float | int | None]:
    return {
        **_percentiles(values),
        "sample_count": len(values),
        "timeout_count": timeout_count,
        "error_count": error_count,
    }


def _check_content_free(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_KEYS & value.keys()
        if forbidden:
            fields = ",".join(sorted(forbidden))
            raise ValueError(f"shadow_event_contains_forbidden_fields:{fields}")
        for item in value.values():
            _check_content_free(item)
    elif isinstance(value, list):
        for item in value:
            _check_content_free(item)


def build_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [event for event in events if event.get("event") == "okf.shadow.read"]
    sync_events = [event for event in events if event.get("event") == "okf.sync.metric"]
    for event in [*selected, *sync_events]:
        _check_content_free(event)
    completed = [event for event in selected if event.get("shadow_status") == "completed"]
    timeouts = [event for event in selected if event.get("shadow_status") == "timeout"]
    errors = [event for event in selected if event.get("shadow_status") == "failed"]
    timeout_count = len(timeouts) + sum(
        event.get("okf_reason") == "retrieval_timeout" for event in completed
    )
    error_count = len(errors) + sum(
        event.get("okf_reason") == "database_unavailable" for event in completed
    )
    def latency(field: str) -> list[float]:
        return [
            float(event[field])
            for event in selected
            if _number(event.get(field))
        ]
    overlap_ratios = [
        float(item["overlap_ratio"])
        for item in completed
        if _number(item.get("overlap_ratio"))
    ]
    provenance = [
        float(item["okf_provenance_coverage"])
        for item in completed
        if _number(item.get("okf_provenance_coverage"))
    ]
    sync_lag_samples = [
        float(event["sync_lag_ms"])
        for event in sync_events
        if event.get("event_type") == "upsert_memory"
        and event.get("status") == "completed"
        and _number(event.get("sync_lag_ms"))
    ]
    return {
        "report_id": "okf6-shadow-observations-v1",
        "status": "MEASURED_NOT_THRESHOLDED",
        "captured_at": datetime.now(UTC).isoformat(),
        "scope": {
            "event_count": len(selected),
            "content_free_schema_validated": True,
            "acceptance_thresholds": "none; approve against the frozen baseline before rollout",
            "latency_source": "background OKF shadow read, independent session",
            "sync_lag_definition": "completed upsert job time minus enqueue time",
        },
        "counts": {
            "completed": len(completed),
            "skipped": sum(item.get("shadow_status") == "skipped" for item in selected),
            "timed_out": sum(item.get("shadow_status") == "timeout" for item in selected),
            "failed": sum(item.get("shadow_status") == "failed" for item in selected),
            "conflicts": sum(int(item.get("conflict_count", 0)) for item in completed),
            "okf_retrieval_timeouts": timeout_count,
            "okf_retrieval_errors": error_count,
            "no_result_agreements": sum(
                item.get("no_result_agreement") is True for item in completed
            ),
            "no_result_comparisons": sum(
                item.get("no_result_agreement") is not None for item in completed
            ),
        },
        "metrics": {
            "retrieval_latency_ms": {
                "rag": _metric_summary(
                    latency("rag_latency_ms"), timeout_count=0, error_count=0
                ),
                "okf": _metric_summary(
                    latency("okf_latency_ms"),
                    timeout_count=timeout_count,
                    error_count=error_count,
                ),
                "combined": _metric_summary(
                    latency("combined_latency_ms"),
                    timeout_count=timeout_count,
                    error_count=error_count,
                ),
            },
            "shadow_schedule_latency_ms": _metric_summary(
                latency("schedule_to_start_ms"),
                timeout_count=len(timeouts),
                error_count=len(errors),
            ),
            "shadow_elapsed_ms": _metric_summary(
                latency("latency_ms"),
                timeout_count=len(timeouts),
                error_count=len(errors),
            ),
            "sync_lag_ms": _metric_summary(
                sync_lag_samples, timeout_count=0, error_count=0
            ),
            "overlap_ratio_mean": (
                round(sum(overlap_ratios) / len(overlap_ratios), 4) if overlap_ratios else None
            ),
            "okf_provenance_coverage_mean": (
                round(sum(provenance) / len(provenance), 4) if provenance else None
            ),
        },
        "offline_labeled_corpus": (
            "See the OKF-4 comparison report; this live shadow log has no labels."
        ),
    }


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "events", type=Path, help="JSONL structured log containing OKF shadow events"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    events = [
        json.loads(line)
        for line in args.events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = build_report(events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "counts": report["counts"], "output": str(args.output)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
