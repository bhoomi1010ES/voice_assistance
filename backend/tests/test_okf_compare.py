from __future__ import annotations

from pathlib import Path

from scripts.okf_compare import capture_comparison

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_okf4_operator_comparison_reports_metrics_without_invented_thresholds() -> None:
    report = capture_comparison(
        corpus_path=FIXTURE_DIR / "okf_corpus_v1.json",
        contract_path=FIXTURE_DIR / "okf_contract_v1.json",
    )

    assert report["status"] == "MEASURED_NOT_THRESHOLDED"
    assert report["scope"]["database_used"] is False
    assert report["scope"]["thresholds"].startswith("none;")
    assert set(report["metrics"]) == {"rag", "okf", "combined"}
    for metrics in report["metrics"].values():
        assert metrics["accuracy"] == 1.0
        assert metrics["no_result_correctness"] == 1.0
        assert metrics["provenance_coverage"] == 1.0
        assert metrics["cross_user_leaks"] == 0
        assert metrics["latency_ms"]["max"] >= metrics["latency_ms"]["p50"]
    assert len(report["cases"]) == report["corpus"]["cases"]


def test_operator_report_does_not_include_source_content() -> None:
    report = capture_comparison(
        corpus_path=FIXTURE_DIR / "okf_corpus_v1.json",
        contract_path=FIXTURE_DIR / "okf_contract_v1.json",
    )
    serialized = str(report)

    assert "I prefer concise responses." not in serialized
    assert "synthetic-credential-marker" not in serialized
