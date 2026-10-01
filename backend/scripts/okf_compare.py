"""Compare frozen RAG decisions with an offline OKF/COMBINED fixture replay.

The OKF side uses deterministic source-field mapping and provenance projection,
not a live PostgreSQL query. Use its measurements as an operator comparison
harness, not as a substitute for the gated database retrieval tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_CORPUS = BACKEND_ROOT / "tests" / "fixtures" / "okf_corpus_v1.json"
DEFAULT_CONTRACT = BACKEND_ROOT / "tests" / "fixtures" / "okf_contract_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "evidence" / "okf" / "okf4_comparison_latest.json"

_ROUTE_TO_STATUS = {
    "DIRECT_RAG": "direct_answer",
    "RAG_PLUS_LLM": "continue_with_evidence",
    "NO_RESULT": "no_result",
    "UNAVAILABLE": "unavailable",
}


def _duration_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(value: int) -> float:
        if not ordered:
            return 0.0
        index = round((value / 100) * (len(ordered) - 1))
        return round(ordered[index], 6)

    return {
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "max": round(max(ordered, default=0.0), 6),
    }


def _key_tokens(canonical_key: str) -> set[str]:
    return {
        token
        for segment in canonical_key.split("/")
        for token in segment.split("-")
        if token
    }


def _source_proposals(case: dict[str, Any], owner_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    from app.okf.policy import map_memory_to_proposals

    by_key_and_value: dict[tuple[str, str], dict[str, Any]] = {}
    for source in case["sources"]:
        if (
            source.get("owner") == "other"
            or source.get("session_excluded") is True
            or source.get("status", "active") != "active"
        ):
            continue
        source_id = uuid.uuid5(uuid.NAMESPACE_URL, f"okf-corpus:{source['id']}")
        memory = SimpleNamespace(
            id=source_id,
            status=source.get("status", "active"),
            memory_type=source["memory_type"],
            subject=source.get("subject"),
            predicate=source.get("predicate"),
            object_json=source.get("object_json"),
            content=source["content"],
            confidence=float(source.get("confidence", 0.9)),
            valid_from=(
                datetime.fromisoformat(source["valid_from"]) if source.get("valid_from") else None
            ),
            valid_to=(
                datetime.fromisoformat(source["valid_to"]) if source.get("valid_to") else None
            ),
        )
        for proposal in map_memory_to_proposals(memory):
            value_key = json.dumps(proposal.value_json, sort_keys=True, separators=(",", ":"))
            grouped = by_key_and_value.setdefault(
                (proposal.canonical_key, value_key),
                {
                    "concept_type": proposal.concept_type,
                    "canonical_key": proposal.canonical_key,
                    "value_json": proposal.value_json,
                    "display_text": proposal.display_text,
                    "source_memory_ids": set(),
                },
            )
            grouped["source_memory_ids"].add(source_id)

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (key, _value_key), record in by_key_and_value.items():
        record["source_memory_ids"] = tuple(sorted(record["source_memory_ids"], key=str))
        output[key].append(record)
    return output


def _projected_okf(case: dict[str, Any], owner_id: uuid.UUID) -> dict[str, Any]:
    from app.okf.query_plan import plan_okf_query

    started = time.perf_counter_ns()
    rag = case["rag"]
    plan = plan_okf_query(rag["query"], limit=20)
    proposals = _source_proposals(case, owner_id)
    selected_keys = {
        key
        for key, records in proposals.items()
        if (not plan.concept_types or records[0]["concept_type"] in plan.concept_types)
        and all(term in _key_tokens(key) for term in plan.key_terms)
    }
    if plan.project_expansion:
        roots = {
            key
            for key in selected_keys
            if key.startswith("projects/") and key.count("/") == 1
        }
        parents = {
            "/".join(key.split("/")[:2])
            for key in selected_keys
            if key.startswith("projects/") and key.count("/") > 1
        }
        selected_keys.update(
            key
            for key in proposals
            if any(key.startswith(f"{root}/") for root in roots)
            or key in parents
        )
    selected_keys = set(sorted(selected_keys)[: plan.limit])
    records = [
        record
        for key in sorted(selected_keys)
        for record in sorted(
            proposals[key],
            key=lambda item: json.dumps(item["value_json"], sort_keys=True),
        )
    ]
    source_ids = sorted(
        {str(source_id) for record in records for source_id in record["source_memory_ids"]}
    )
    conflicted = any(len(proposals[key]) > 1 for key in selected_keys)
    if not records:
        status = (
            "unavailable"
            if case["expected_mapping"].get("reason")
            in {"source_session_excluded", "foreign_owner"}
            else "no_result"
        )
    elif conflicted:
        status = "conflict"
    elif len(records) == 1:
        status = "direct_answer"
    else:
        status = "continue_with_evidence"
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "status": status,
        "canonical_keys": sorted(selected_keys),
        "source_memory_ids": source_ids,
        "provenance_count": sum(bool(item["source_memory_ids"]) for item in records),
        "fact_count": len(records),
        "latency_ms": elapsed,
    }


def _gold_okf_source_ids(case: dict[str, Any], owner_id: uuid.UUID) -> list[str]:
    expected_keys = set(case["expected_mapping"].get("canonical_keys", ()))
    proposals = _source_proposals(case, owner_id)
    return sorted(
        {
            str(source_id)
            for key in expected_keys
            for record in proposals.get(key, ())
            for source_id in record["source_memory_ids"]
        }
    )


def _expected_okf_status(case: dict[str, Any], expected_sources: list[str]) -> str:
    if not expected_sources:
        return (
            "unavailable"
            if case["expected_mapping"].get("reason")
            in {"source_session_excluded", "foreign_owner"}
            else "no_result"
        )
    state = case["expected_mapping"].get("assertion_state")
    if state == "contested":
        return "conflict"
    expected_keys = case["expected_mapping"].get("canonical_keys", ())
    return "direct_answer" if len(expected_keys) == 1 else "continue_with_evidence"


def _combined_status(
    *, evidence_ids: list[str], rag_status: str, okf_status: str, conflict: bool
) -> str:
    if conflict:
        return "conflict"
    if not evidence_ids:
        if rag_status == okf_status == "unavailable":
            return "unavailable"
        return "no_result"
    if len(evidence_ids) == 1 and "direct_answer" in {rag_status, okf_status}:
        return "direct_answer"
    return "continue_with_evidence"


def capture_comparison(
    *,
    corpus_path: Path = DEFAULT_CORPUS,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    from scripts.okf_baseline import capture_baseline

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    owner_id = uuid.UUID(corpus["owner_id"])
    rag_baseline = capture_baseline(corpus_path=corpus_path, contract_path=contract_path)
    rag_by_case = {item["case_id"]: item for item in rag_baseline["cases"]}
    if corpus["cases"]:
        # Exclude one-time module import/setup cost from per-query measurements.
        _projected_okf(corpus["cases"][0], owner_id)
    modes: dict[str, dict[str, list[Any]]] = {
        name: {
            "correct": [],
            "no_result": [],
            "latency": [],
            "provenance": [],
            "cross_user_leaks": [],
        }
        for name in ("rag", "okf", "combined")
    }
    outputs = []

    for case in corpus["cases"]:
        rag = case["rag"]
        rag_record = rag_by_case[case["id"]]
        okf_started = time.perf_counter_ns()
        okf = _projected_okf(case, owner_id)
        measured_ms = (time.perf_counter_ns() - okf_started) / 1_000_000
        okf["latency_ms"] = max(okf["latency_ms"], measured_ms)
        source_id_by_uuid = {
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"okf-corpus:{source['id']}")): source["id"]
            for source in case["sources"]
        }
        source_uuid_by_id = {
            source_id: source_uuid for source_uuid, source_id in source_id_by_uuid.items()
        }
        foreign_source_ids = {
            source["id"] for source in case["sources"] if source.get("owner") == "other"
        }
        rag_ids = list(rag_record["evidence_ids"])
        okf_ids = [source_id_by_uuid.get(item, item) for item in okf["source_memory_ids"]]
        combined_ids = sorted(set(rag_ids) | set(okf_ids))
        rag_status = _ROUTE_TO_STATUS[rag_record["route"]]
        expected_rag_ids = list(rag.get("expected_evidence_ids", ()))
        expected_okf_ids = [
            source_id_by_uuid.get(item, item)
            for item in _gold_okf_source_ids(case, owner_id)
        ]
        expected_combined_ids = sorted(set(expected_rag_ids) | set(expected_okf_ids))
        expected_okf_status = _expected_okf_status(case, expected_okf_ids)
        expected_rag_status = _ROUTE_TO_STATUS[rag["expected_route"]]
        conflict = okf["status"] == "conflict"
        combined_status = _combined_status(
            evidence_ids=combined_ids,
            rag_status=rag_status,
            okf_status=okf["status"],
            conflict=conflict,
        )
        expected_combined_status = _combined_status(
            evidence_ids=expected_combined_ids,
            rag_status=expected_rag_status,
            okf_status=expected_okf_status,
            conflict=case["expected_mapping"].get("assertion_state") == "contested",
        )
        valid_source_ids = {
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"okf-corpus:{source['id']}"))
            for source in case["sources"]
            if source.get("owner") != "other"
            and source.get("status", "active") == "active"
            and source.get("session_excluded") is not True
        }
        rag_uuid_ids = [source_uuid_by_id.get(item, item) for item in rag_ids]
        combined_uuid_ids = [source_uuid_by_id.get(item, item) for item in combined_ids]
        rag_correct = (
            rag_record["route"] == rag["expected_route"]
            and rag_ids == expected_rag_ids
        )
        okf_correct = (
            okf["status"] == expected_okf_status
            and set(okf_ids) == set(expected_okf_ids)
            and set(okf["canonical_keys"])
            == set(case["expected_mapping"].get("canonical_keys", ()))
        )
        combined_correct = (
            set(combined_ids) == set(expected_combined_ids)
            and combined_status == expected_combined_status
        )
        for mode, correct, _ids, status, latency in (
            ("rag", rag_correct, rag_ids, rag_status, rag_record["latency_ms"]["total"]),
            ("okf", okf_correct, okf_ids, okf["status"], okf["latency_ms"]),
            (
                "combined",
                combined_correct,
                combined_ids,
                combined_status,
                rag_record["latency_ms"]["total"] + okf["latency_ms"],
            ),
        ):
            modes[mode]["correct"].append(correct)
            modes[mode]["latency"].append(latency)
            modes[mode]["cross_user_leaks"].append(
                len(set(_ids) & foreign_source_ids)
            )
            if mode == "okf":
                modes[mode]["provenance"].extend(
                    [True] * okf["provenance_count"]
                    + [False] * (okf["fact_count"] - okf["provenance_count"])
                )
            else:
                provenance_ids = {
                    "rag": rag_uuid_ids,
                    "combined": combined_uuid_ids,
                }[mode]
                modes[mode]["provenance"].extend(
                    item in valid_source_ids for item in provenance_ids
                )
            if expected_combined_status == "no_result":
                modes[mode]["no_result"].append(status == "no_result")
        outputs.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "rag": {"status": rag_status, "evidence_ids": rag_ids, "correct": rag_correct},
                "okf": {**okf, "correct": okf_correct},
                "combined": {
                    "status": combined_status,
                    "evidence_ids": combined_ids,
                    "correct": combined_correct,
                },
            }
        )

    metrics = {}
    for mode, values in modes.items():
        metrics[mode] = {
            "accuracy": round(sum(values["correct"]) / max(1, len(values["correct"])), 4),
            "no_result_correctness": (
                round(sum(values["no_result"]) / len(values["no_result"]), 4)
                if values["no_result"]
                else None
            ),
            "no_result_cases_measured": len(values["no_result"]),
            "provenance_coverage": (
                round(sum(values["provenance"]) / len(values["provenance"]), 4)
                if values["provenance"]
                else None
            ),
            "facts_with_provenance": sum(values["provenance"]),
            "facts_returned": len(values["provenance"]),
            "cross_user_leaks": sum(values["cross_user_leaks"]),
            "latency_ms": _duration_summary(values["latency"]),
        }
    return {
        "comparison_id": "okf4-offline-replay-v1",
        "status": "MEASURED_NOT_THRESHOLDED",
        "captured_at": datetime.now(UTC).isoformat(),
        "scope": {
            "database_used": False,
            "llm_used": False,
            "okf_mode": "synthetic canonical-key/provenance projection",
            "rag_mode": "frozen OKF-0 RAG decision replay",
            "thresholds": "none; operator approval must use measured baseline data",
        },
        "corpus": {
            "id": corpus["corpus_id"],
            "version": corpus["version"],
            "cases": len(corpus["cases"]),
            "sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        },
        "metrics": metrics,
        "cases": outputs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = capture_comparison(corpus_path=args.corpus, contract_path=args.contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "metrics": report["metrics"], "output": str(args.output)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
