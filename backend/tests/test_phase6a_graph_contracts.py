from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.graph import (
    EntityType,
    GraphBackfillResult,
    GraphEdge,
    GraphEvidenceBundle,
    GraphFallbackReason,
    GraphIndexJobPayload,
    GraphPath,
    GraphQueryDecision,
    GraphSkipReason,
    RelationshipType,
    build_graph_query_decision,
)


def test_graph_query_decision_is_deterministic_and_separate_from_memory_plan() -> None:
    first = build_graph_query_decision(
        "Who works with Rahul?",
        graph_rag_mode="shadow",
        memory_retrieval_mode="inject",
    )
    second = build_graph_query_decision(
        "Who works with Rahul?",
        graph_rag_mode="shadow",
        memory_retrieval_mode="inject",
    )

    assert first == second
    assert first.should_query is True
    assert first.depth == 1
    assert first.query_entities == ("Rahul",)
    assert first.relationship_type is RelationshipType.RELATED_TO
    assert first.skip_reason is None


def test_graph_query_decision_bounds_two_hop_relationship_questions() -> None:
    decision = build_graph_query_decision(
        "Who is testing the project Rahul works on?",
        graph_rag_mode="shadow",
        memory_retrieval_mode="inject",
    )

    assert decision.should_query is True
    assert decision.depth == 2
    assert decision.query_entities == ("Rahul",)


@pytest.mark.parametrize(
    ("graph_mode", "memory_mode", "reason"),
    [
        ("off", "inject", GraphSkipReason.GRAPH_DISABLED),
        ("shadow", "off", GraphSkipReason.MEMORY_RETRIEVAL_DISABLED),
    ],
)
def test_graph_query_decision_respects_disabled_feature_gates(
    graph_mode: str,
    memory_mode: str,
    reason: GraphSkipReason,
) -> None:
    decision = build_graph_query_decision(
        "Who works with Rahul?",
        graph_rag_mode=graph_mode,  # type: ignore[arg-type]
        memory_retrieval_mode=memory_mode,  # type: ignore[arg-type]
    )

    assert decision.should_query is False
    assert decision.query_entities == ()
    assert decision.skip_reason is reason


def test_graph_query_decision_skips_non_relationship_queries() -> None:
    decision = build_graph_query_decision(
        "What is my favorite drink?",
        graph_rag_mode="shadow",
        memory_retrieval_mode="inject",
    )

    assert decision.should_query is False
    assert decision.skip_reason is GraphSkipReason.NOT_RELATIONSHIP_QUERY


def test_graph_contracts_are_checked_and_provenance_complete() -> None:
    user_id = uuid.uuid4()
    source_entity_id = uuid.uuid4()
    target_entity_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    edge = GraphEdge(
        relationship_id=uuid.uuid4(),
        user_id=user_id,
        source_entity_id=source_entity_id,
        source_name="Rahul",
        relationship_type=RelationshipType.RELATED_TO,
        target_entity_id=target_entity_id,
        target_name="Anika",
        source_memory_id=memory_id,
        confidence=1.0,
        valid_from=None,
        valid_to=None,
        status="active",
        created_at=datetime.now(UTC),
    )
    path = GraphPath(
        entity_ids=(source_entity_id, target_entity_id),
        edges=(edge,),
    )
    bundle = GraphEvidenceBundle(
        paths=(path,),
        source_memory_ids=(memory_id,),
    )

    assert bundle.source_memory_ids == (memory_id,)
    assert EntityType.PERSON.value == "person"
    assert GraphFallbackReason.GRAPH_TIMEOUT.value == "graph_timeout"
    assert GraphIndexJobPayload(
        user_id=user_id,
        memory_id=memory_id,
        policy_version="v1",
    ).job_type == "index_memory_graph"

    with pytest.raises(ValueError, match="edges must connect"):
        GraphPath(entity_ids=(source_entity_id, target_entity_id), edges=())
    with pytest.raises(ValueError, match="every path source memory"):
        GraphEvidenceBundle(paths=(path,), source_memory_ids=())
    with pytest.raises(ValueError, match="policy_version"):
        GraphIndexJobPayload(user_id=user_id, memory_id=memory_id, policy_version=" ")


def test_graph_query_decision_rejects_inconsistent_routing_state() -> None:
    with pytest.raises(ValidationError, match="skip reason"):
        GraphQueryDecision(
            normalized_query="Who works with Rahul?",
            should_query=True,
            query_entities=("Rahul",),
            skip_reason=GraphSkipReason.GRAPH_DISABLED,
        )


def test_graph_backfill_result_requires_consistent_bounded_counts() -> None:
    user_id = uuid.uuid4()
    cursor = uuid.uuid4()
    result = GraphBackfillResult(
        user_id=user_id,
        scanned=3,
        indexed=1,
        already_indexed=1,
        skipped=1,
        next_cursor=cursor,
        complete=False,
    )
    assert result.next_cursor == cursor

    with pytest.raises(ValueError, match="add up to scanned"):
        GraphBackfillResult(
            user_id=user_id,
            scanned=2,
            indexed=1,
            already_indexed=1,
            skipped=1,
            next_cursor=cursor,
            complete=True,
        )
