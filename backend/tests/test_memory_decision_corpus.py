from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.memory.evaluation import evaluate_memory_result
from app.memory.types import (
    FusedMemory,
    MemoryQueryPlan,
    MemoryRetrievalResult,
    MemoryStatus,
    MemoryType,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "phase6_memory_decision_corpus_v1.json"


def test_labeled_memory_decision_corpus_matches_safe_routes() -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    owner_id = uuid.UUID(corpus["owner_id"])
    now = datetime.fromisoformat(corpus["now"]).astimezone(UTC)
    assert corpus["corpus_id"] == "phase6-memory-decision-corpus-v1"
    assert corpus["version"] == "1.0.0"
    assert corpus["calibration_policy"]["rerank_score_is_probability"] is False
    assert corpus["calibration_policy"]["direct_score_threshold"] is None

    outcomes: dict[str, str] = {}
    for case in corpus["cases"]:
        memories = tuple(
            FusedMemory(
                memory_id=uuid.uuid5(uuid.NAMESPACE_URL, record["id"]),
                user_id=uuid.UUID(record.get("owner_id", corpus["owner_id"])),
                content=record["content"],
                rank=index,
                score=record.get("score", 0.0),
                sources=tuple(record.get("sources", ())),
                created_at=now,
                memory_type=MemoryType(record.get("memory_type", "fact")),
                subject=record.get("subject"),
                predicate=record.get("predicate"),
                object_json=record.get("object_json"),
                status=MemoryStatus(record.get("status", "active")),
                valid_from=(
                    datetime.fromisoformat(record["valid_from"])
                    if record.get("valid_from")
                    else None
                ),
                valid_to=(
                    datetime.fromisoformat(record["valid_to"]) if record.get("valid_to") else None
                ),
            )
            for index, record in enumerate(case["records"], start=1)
        )
        retrieval = MemoryRetrievalResult(
            status=case.get("retrieval_status", "ready"),
            plan=MemoryQueryPlan(normalized_query=case["query"]),
            memories=memories,
        )
        outcome = evaluate_memory_result(
            retrieval,
            user_id=owner_id,
            query=case["query"],
            now=now,
        )
        outcomes[case["id"]] = outcome.route.value
        assert outcome.route.value == case["expected_route"], case["id"]

    assert len(outcomes) == 11
    assert sum(route == "DIRECT_RAG" for route in outcomes.values()) == 1
