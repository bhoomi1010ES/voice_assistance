
from __future__ import annotations

from scripts.phase8_classifier_assessment import assess_corpus


def test_current_frozen_corpus_does_not_show_a_need_for_semantic_classifier() -> None:
    report = assess_corpus()

    assert report["deterministic_case_count"] == 77
    assert report["decision_coverage"] == 77
    assert report["expected_decision_matches"] == 77
    assert report["route_counts"]["MIXED_AMBIGUOUS"] == 27
    assert report["legacy_named_tool_choices"] == 31
    assert report["legacy_auto_tool_choices_unresolved"] == 46
    assert report["named_tool_route_disagreements"] == 15
    assert report["false_executable_routes"] == 0
    assert report["legacy_false_executable_routes"] == 15
    assert report["phase7_cases"] == 10
    assert {row["rule_route"] for row in report["disagreements"]} == {
        "GENERAL_LLM",
        "MIXED_AMBIGUOUS",
        "MEMORY_QUERY",
    }
    assert report["model_calls"] == report["retrieval_calls"] == report["writes"] == 0
