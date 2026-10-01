from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.okf_baseline import capture_baseline, write_baseline

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CONTRACT_PATH = FIXTURE_DIR / "okf_contract_v1.json"
CORPUS_PATH = FIXTURE_DIR / "okf_corpus_v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_okf_contract_v1_freezes_required_domain_boundaries() -> None:
    contract = _load(CONTRACT_PATH)

    assert contract["contract_id"] == "okf-contract-v1"
    assert contract["version"] == "1.0.0"
    assert contract["policy_version"] == "okf-v1"
    assert contract["concept_types"]["enabled"] == [
        "profile",
        "preference",
        "project",
        "decision",
        "relationship",
        "fact",
    ]
    assert contract["mapping"]["parses_free_text_for_identity_or_value"] is False
    assert contract["assertion_policy"]["confidence_may_choose_winner"] is False
    assert (
        contract["privacy_barrier"]["background_reconciliation_may_leave_stale_read_window"]
        is False
    )
    assert contract["privacy_barrier"]["disable_is_deletion"] is False
    assert set(contract["service_contracts"]["KnowledgeDisposition"]) == {
        "direct_answer",
        "continue_with_evidence",
        "no_result",
        "unavailable",
        "conflict",
        "cancelled",
    }


def test_okf_corpus_v1_covers_required_categories_and_valid_keys() -> None:
    contract = _load(CONTRACT_PATH)
    corpus = _load(CORPUS_PATH)

    assert corpus["corpus_id"] == "okf-corpus-v1"
    assert corpus["version"] == "1.0.0"
    assert corpus["policy_version"] == contract["policy_version"]
    categories = {case["category"] for case in corpus["cases"]}
    assert set(corpus["required_categories"]).issubset(categories)
    assert len(corpus["cases"]) >= 12

    key_pattern = re.compile(contract["canonical_key"]["pattern"])
    seen_case_ids: set[str] = set()
    for case in corpus["cases"]:
        assert case["id"] not in seen_case_ids
        seen_case_ids.add(case["id"])
        assert case["sources"] or case["rag"]["records"] == []
        source_ids = {source["id"] for source in case["sources"]}
        assert len(source_ids) == len(case["sources"])
        assert set(case["rag"]["records"]).issubset(source_ids)
        for key in case["expected_mapping"].get("canonical_keys", []):
            assert len(key) <= contract["canonical_key"]["max_characters"]
            assert len(key.split("/")) <= contract["canonical_key"]["max_segments"]
            assert key_pattern.fullmatch(key), (case["id"], key)


def test_okf0_rag_baseline_matches_frozen_expectations() -> None:
    result = capture_baseline(corpus_path=CORPUS_PATH, contract_path=CONTRACT_PATH)

    assert result["status"] == "PASS", result["failures"]
    assert result["failures"] == []
    assert result["scope"]["runtime_configuration_changed"] is False
    assert result["scope"]["database_used"] is False
    case_count = result["corpus"]["case_count"]
    assert result["call_counts"]["query_plan"] == case_count
    assert result["call_counts"]["retrieval_fixture_input"] == case_count
    assert result["call_counts"]["memory_evaluation"] == case_count
    assert result["call_counts"]["embedding_provider"] == 0
    assert result["call_counts"]["reranker"] == 0
    assert sum(result["route_counts"].values()) == case_count
    assert set(result["route_counts"]) == {
        "DIRECT_RAG",
        "NO_RESULT",
        "RAG_PLUS_LLM",
        "UNAVAILABLE",
    }


def test_okf0_rag_baseline_output_is_reproducible_and_content_free(tmp_path: Path) -> None:
    result = capture_baseline(corpus_path=CORPUS_PATH, contract_path=CONTRACT_PATH)
    output = tmp_path / "baseline.json"

    write_baseline(result, output)
    stored = _load(output)

    assert stored["status"] == "PASS"
    assert stored["corpus"]["sha256"] == result["corpus"]["sha256"]
    assert stored["contract"]["sha256"] == result["contract"]["sha256"]
    serialized = output.read_text(encoding="utf-8")
    for case in _load(CORPUS_PATH)["cases"]:
        for source in case["sources"]:
            assert source["content"] not in serialized
