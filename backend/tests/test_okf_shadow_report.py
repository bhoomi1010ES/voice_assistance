from __future__ import annotations

import pytest

from scripts.okf_shadow_report import build_report


def test_shadow_report_aggregates_only_content_free_events() -> None:
    events = [
        {
            "event": "okf.shadow.read",
            "shadow_status": "completed",
            "latency_ms": 20.0,
            "overlap_ratio": 0.5,
            "okf_provenance_coverage": 1.0,
            "no_result_case": True,
            "no_result_agreement": False,
            "conflict_count": 1,
            "rag_latency_ms": 6.0,
            "okf_latency_ms": 12.0,
            "combined_latency_ms": 19.0,
        },
        {
            "event": "okf.shadow.read",
            "shadow_status": "timeout",
            "latency_ms": 75.0,
            "rag_latency_ms": 20.0,
        },
        {"event": "other.event", "query": "not selected"},
    ]

    report = build_report(events)

    assert report["status"] == "MEASURED_NOT_THRESHOLDED"
    assert report["scope"]["event_count"] == 2
    assert report["scope"]["content_free_schema_validated"] is True
    assert report["counts"]["completed"] == 1
    assert report["counts"]["timed_out"] == 1
    assert report["counts"]["conflicts"] == 1
    assert report["counts"]["no_result_comparisons"] == 1
    assert report["counts"]["no_result_agreements"] == 0
    assert report["metrics"]["shadow_elapsed_ms"]["p50"] == 20.0
    assert report["metrics"]["retrieval_latency_ms"]["rag"]["p50"] == 6.0
    assert report["metrics"]["okf_provenance_coverage_mean"] == 1.0


def test_shadow_report_rejects_content_or_source_identity_fields() -> None:
    with pytest.raises(ValueError, match="forbidden_fields"):
        build_report(
            [
                {
                    "event": "okf.shadow.read",
                    "shadow_status": "completed",
                    "query": "private query",
                }
            ]
        )
