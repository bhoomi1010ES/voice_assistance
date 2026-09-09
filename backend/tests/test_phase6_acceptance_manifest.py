from __future__ import annotations

import json
from pathlib import Path

CORPUS_PATH = Path(__file__).parent / "fixtures" / "phase6_retrieval_corpus_v1.json"
REQUIRED_CATEGORIES = {
    "latest",
    "oldest",
    "preference",
    "relationship",
    "exact_name",
    "paraphrase",
    "conflict",
    "deleted",
    "cross_user",
    "no_result",
    "mumbai_alias",
    "relative_date",
    "timezone",
    "repeated",
    "negation",
    "disabled",
    "exclusion",
    "prompt_injection",
    "provider_outage",
}


def load_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_phase6_retrieval_acceptance_manifest_is_versioned_and_complete() -> None:
    corpus = load_corpus()
    assert corpus["corpus_id"] == "phase6-retrieval-corpus-v1"
    assert corpus["version"] == "1.0.0"
    assert len(corpus["cases"]) == 20
    assert {case["category"] for case in corpus["cases"]} == REQUIRED_CATEGORIES
    assert len({case["id"] for case in corpus["cases"]}) == 20
    assert all("expected_ids" in case for case in corpus["cases"])


def test_phase6_retrieval_manifest_declares_zero_tolerance_security_gates() -> None:
    gates = load_corpus()["hard_gates"]
    assert gates
    assert all(value == 0 for value in gates.values())
    assert all(record["trust"] == "untrusted" for record in load_corpus()["records"])
