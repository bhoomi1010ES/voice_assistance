from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.okf_live_evaluate import (
    EvaluationCase,
    _assess_approved_gates,
    _read_cases,
    _validate_output_path,
)


def test_evaluation_case_normalizes_query_and_rejects_duplicate_modes() -> None:
    source_id = uuid.uuid4()
    case = EvaluationCase(
        case_id="case-1",
        run_id="unit-test-run",
        owner_id=uuid.uuid4(),
        query="  Which   framework do I use?  ",
        expected_result="The labeled framework fact.",
        expected_lifecycle_behavior="Source remains active.",
        expected_privacy_behavior="Only this owner source is eligible.",
        requested_modes=("rag", "okf", "combined"),
        expected_disposition="direct_answer",
        expected_source_memory_ids=(source_id,),
        allowed_source_memory_ids=(source_id,),
    )
    assert case.query == "Which framework do I use?"

    with pytest.raises(ValidationError, match="requested_modes must not contain duplicates"):
        EvaluationCase(
            case_id="case-2",
            run_id="unit-test-run",
            owner_id=uuid.uuid4(),
            query="What do I prefer?",
            expected_result="A preference fact.",
            expected_lifecycle_behavior="Source remains active.",
            expected_privacy_behavior="Only this owner source is eligible.",
            requested_modes=("rag", "rag"),
        )


def test_case_manifest_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    owner_id = uuid.uuid4()
    source_id = uuid.uuid4()
    payload = {
        "case_id": "duplicate",
        "run_id": "unit-test-run",
        "owner_id": str(owner_id),
        "query": "What do I prefer?",
        "expected_result": "A preference fact.",
        "expected_lifecycle_behavior": "Source remains active.",
        "expected_privacy_behavior": "Only this owner source is eligible.",
        "requested_modes": ["rag"],
        "expected_disposition": "direct_answer",
        "expected_source_memory_ids": [str(source_id)],
        "allowed_source_memory_ids": [str(source_id)],
    }
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(
        json.dumps(payload) + "\n" + json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case_id"):
        _read_cases(manifest)


def test_scored_cases_require_expected_source_ids_for_positive_evidence() -> None:
    with pytest.raises(ValidationError, match="expected source memory IDs are required"):
        EvaluationCase(
            case_id="missing-source-label",
            run_id="unit-test-run",
            owner_id=uuid.uuid4(),
            query="Which framework is selected?",
            expected_result="The expected framework.",
            expected_lifecycle_behavior="Source remains active.",
            expected_privacy_behavior="Only this owner source is eligible.",
            requested_modes=("rag", "okf", "combined"),
            expected_disposition="direct_answer",
        )


def test_allowed_evidence_must_cover_expected_sources() -> None:
    source_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="allowed source memory IDs must include"):
        EvaluationCase(
            case_id="source-not-allowed",
            run_id="unit-test-run",
            owner_id=uuid.uuid4(),
            query="Which framework is selected?",
            expected_result="The expected framework.",
            expected_lifecycle_behavior="Source remains active.",
            expected_privacy_behavior="Only this owner source is eligible.",
            requested_modes=("rag", "okf", "combined"),
            expected_disposition="direct_answer",
            expected_source_memory_ids=(source_id,),
        )


def test_mode_specific_labels_must_cover_each_expected_source() -> None:
    owner_id = uuid.uuid4()
    rag_source = uuid.uuid4()
    okf_source = uuid.uuid4()
    case = EvaluationCase(
        case_id="mode-specific-sources",
        run_id="unit-test-run",
        owner_id=owner_id,
        query="What is my current structured fact?",
        expected_result="The active current fact.",
        expected_lifecycle_behavior="Superseded versions remain inactive.",
        expected_privacy_behavior="Only this owner source is eligible.",
        requested_modes=("rag", "okf", "combined"),
        expected_by_mode={
            "rag": "continue_with_evidence",
            "okf": "direct_answer",
            "combined": "continue_with_evidence",
        },
        expected_sources_by_mode={
            "rag": (rag_source,),
            "okf": (okf_source,),
            "combined": (rag_source, okf_source),
        },
        allowed_sources_by_mode={
            "rag": (rag_source,),
            "okf": (okf_source,),
            "combined": (rag_source, okf_source),
        },
    )
    assert case.expected_sources_by_mode["okf"] == (okf_source,)

    with pytest.raises(ValidationError, match="expected_by_mode must label every requested mode"):
        EvaluationCase(
            case_id="missing-mode-disposition",
            run_id="unit-test-run",
            owner_id=owner_id,
            query="What is my current structured fact?",
            expected_result="The active current fact.",
            expected_lifecycle_behavior="Source remains active.",
            expected_privacy_behavior="Only this owner source is eligible.",
            requested_modes=("rag", "okf"),
            expected_by_mode={"rag": "continue_with_evidence"},
            expected_sources_by_mode={"rag": (rag_source,)},
            allowed_sources_by_mode={"rag": (rag_source,)},
        )

    with pytest.raises(ValidationError, match="expected source memory IDs are required"):
        EvaluationCase(
            case_id="missing-mode-source",
            run_id="unit-test-run",
            owner_id=owner_id,
            query="What is my current structured fact?",
            expected_result="The active current fact.",
            expected_lifecycle_behavior="Source remains active.",
            expected_privacy_behavior="Only this owner source is eligible.",
            requested_modes=("rag", "okf"),
            expected_by_mode={"rag": "continue_with_evidence", "okf": "no_result"},
            expected_sources_by_mode={"rag": ()},
            allowed_sources_by_mode={"rag": ()},
        )


def test_no_result_label_rejects_allowed_sources_for_mode() -> None:
    source_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="no-result cases must not expect or allow"):
        EvaluationCase(
            case_id="no-result-with-source",
            run_id="unit-test-run",
            owner_id=uuid.uuid4(),
            query="What unrelated item is stored?",
            expected_result="No matching memory exists.",
            expected_lifecycle_behavior="No lifecycle transition applies.",
            expected_privacy_behavior="No unrelated owner source may be returned.",
            requested_modes=("okf",),
            expected_by_mode={"okf": "no_result"},
            allowed_sources_by_mode={"okf": (source_id,)},
        )


def test_approved_gate_assessment_uses_hard_safety_and_latency_thresholds() -> None:
    mode_metrics = {
        mode: {
            "correctness": correctness,
            "no_result_correctness": 1.0,
            "expected_no_result_cases": 1,
            "provenance_coverage": 1.0,
            "privacy_return_counts": {
                "cross_user": 0,
                "deleted": 0,
                "excluded": 0,
                "superseded": 0,
            },
            "latency_ms": {"p95": 600.0},
        }
        for mode, correctness in {"rag": 0.90, "okf": 0.90, "combined": 0.95}.items()
    }
    assessment = _assess_approved_gates(
        mode_metrics=mode_metrics,
        sync_lag_ms={"p95": 5_000.0, "max": 5_000.0},
        timeout_count=0,
        error_count=0,
        unresolved_sync_job_count=0,
        rag_baseline_p95_ms=431.5948,
    )
    assert assessment["status"] == "PASS"
    assert assessment["thresholds"]["retrieval_p95_ceiling_ms"] == 647.3922

    mode_metrics["okf"]["privacy_return_counts"]["deleted"] = 1
    failed = _assess_approved_gates(
        mode_metrics=mode_metrics,
        sync_lag_ms={"p95": 5_000.0, "max": 5_000.0},
        timeout_count=0,
        error_count=0,
        unresolved_sync_job_count=0,
        rag_baseline_p95_ms=431.5948,
    )
    assert failed["status"] == "FAIL"
    assert "deleted_fact_return" in failed["failed_gates"]


def test_evidence_output_must_be_unique_and_inside_okf_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="under docs/evidence/okf"):
        _validate_output_path(tmp_path / "outside.jsonl")

    allowed = _validate_output_path(
        Path(__file__).resolve().parents[2] / "docs" / "evidence" / "okf" / "new-test-output.jsonl"
    )
    assert allowed.name == "new-test-output.jsonl"
    assert "evidence" in allowed.parts
