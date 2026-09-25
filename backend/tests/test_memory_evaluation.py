from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.memory.evaluation import (
    MemoryEvaluationRoute,
    bound_memory_evidence,
    evaluate_memory_result,
)
from app.memory.types import (
    FusedMemory,
    MemoryIntent,
    MemoryQueryPlan,
    MemoryRetrievalResult,
    MemoryStatus,
    MemoryType,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def _memory(
    user_id: uuid.UUID,
    *,
    content: str = "My name is Anika.",
    subject: str | None = "user",
    predicate: str | None = "name",
    sources: tuple[str, ...] = ("structured", "fts"),
    score: float = 0.000001,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    object_json: dict | None = None,
) -> FusedMemory:
    return FusedMemory(
        memory_id=uuid.uuid4(),
        user_id=user_id,
        content=content,
        rank=1,
        score=score,
        sources=sources,
        created_at=NOW - timedelta(days=2),
        memory_type=MemoryType.FACT,
        subject=subject,
        predicate=predicate,
        object_json=object_json,
        status=status,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _result(*memories: FusedMemory, status: str = "ready") -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        status=status,
        plan=MemoryQueryPlan(normalized_query="What is my name?", intent=MemoryIntent.FACT),
        memories=tuple(memories),
    )


def test_single_current_exact_fact_is_direct_and_attributed_by_id() -> None:
    owner = uuid.uuid4()
    memory = _memory(owner)
    evaluated = evaluate_memory_result(
        _result(memory), user_id=owner, query="What is my name?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.DIRECT_RAG
    assert evaluated.direct_text == "I have this saved: “My name is Anika.”"
    assert evaluated.evidence_ids == (memory.memory_id,)
    assert evaluated.evidence[0].status == "active"
    assert evaluated.evidence[0].source == ("structured", "fts")


@pytest.mark.parametrize("status", ["disabled", "degraded"])
def test_disabled_and_degraded_retrieval_are_unavailable_not_empty(status: str) -> None:
    evaluated = evaluate_memory_result(
        _result(status=status), user_id=uuid.uuid4(), query="What is my name?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.UNAVAILABLE


def test_no_result_is_distinct_from_unavailable() -> None:
    evaluated = evaluate_memory_result(
        _result(), user_id=uuid.uuid4(), query="What is my favorite spaceship?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.NO_RESULT


def test_conflicting_active_facts_require_evidence_grounded_synthesis() -> None:
    owner = uuid.uuid4()
    mumbai = _memory(
        owner,
        content="I work remotely from Mumbai.",
        predicate="work_location",
        object_json={"city": "Mumbai"},
    )
    pune = _memory(
        owner,
        content="I work from Pune.",
        predicate="work_location",
        object_json={"city": "Pune"},
    )
    evaluated = evaluate_memory_result(
        _result(mumbai, pune), user_id=owner, query="Where do I work now?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.RAG_PLUS_LLM
    assert evaluated.reason == "conflicting_evidence"
    assert set(evaluated.evidence_ids) == {mumbai.memory_id, pune.memory_id}


def test_superseded_and_deleted_records_never_become_direct_answers() -> None:
    owner = uuid.uuid4()
    old = _memory(owner, status=MemoryStatus.SUPERSEDED)
    deleted = _memory(owner, status=MemoryStatus.DELETED)
    evaluated = evaluate_memory_result(
        _result(old, deleted), user_id=owner, query="What is my name?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.NO_RESULT
    assert evaluated.evidence_ids == ()


def test_expired_temporal_fact_is_not_used_for_a_current_question() -> None:
    owner = uuid.uuid4()
    expired = _memory(owner, valid_to=NOW - timedelta(seconds=1))
    evaluated = evaluate_memory_result(
        _result(expired), user_id=owner, query="What is my name?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.NO_RESULT


def test_cross_user_result_fails_closed_without_exposing_evidence() -> None:
    evaluated = evaluate_memory_result(
        _result(_memory(uuid.uuid4())),
        user_id=uuid.uuid4(),
        query="What is my name?",
        now=NOW,
    )

    assert evaluated.route == MemoryEvaluationRoute.UNAVAILABLE
    assert evaluated.reason == "owner_scope_violation"
    assert evaluated.evidence == ()


def test_dense_only_low_score_is_not_treated_as_grounded_evidence() -> None:
    owner = uuid.uuid4()
    memory = _memory(owner, sources=("dense",), score=0.0)
    evaluated = evaluate_memory_result(
        _result(memory), user_id=owner, query="What is my name?", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.NO_RESULT


def test_temporal_or_synthesis_question_does_not_get_a_direct_answer() -> None:
    owner = uuid.uuid4()
    memory = _memory(owner)
    evaluated = evaluate_memory_result(
        _result(memory), user_id=owner, query="Tell me about my name and background.", now=NOW
    )

    assert evaluated.route == MemoryEvaluationRoute.RAG_PLUS_LLM


def test_selected_evidence_is_bounded_before_context_assembly() -> None:
    owner = uuid.uuid4()
    memories = tuple(
        _memory(owner, content="x" * 2_000, predicate=f"fact_{index}") for index in range(8)
    )

    bounded = bound_memory_evidence(memories)

    assert len(bounded) <= 5
    assert sum(len(memory.content) for memory in bounded) <= 1_200


@pytest.mark.asyncio
async def test_direct_answer_uniqueness_check_is_owner_and_status_scoped() -> None:
    from types import SimpleNamespace

    from app.memory.retrieval import MemoryRetrievalService

    class Nested:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        statement = None
        rows = [uuid.uuid4()]

        def begin_nested(self):
            return Nested()

        async def scalars(self, statement):
            self.statement = statement
            return SimpleNamespace(all=lambda: self.rows)

    owner = uuid.uuid4()
    session = Session()
    service = object.__new__(MemoryRetrievalService)

    multiple = await service.has_multiple_current_matches(
        session,
        user_id=owner,
        subject="user",
        predicate="name",
        now=NOW,
    )

    compiled = session.statement.compile()
    sql = str(compiled).lower()
    assert multiple is False
    assert owner in compiled.params.values()
    assert "memory_items.status" in sql
    assert "memory_items.subject" in sql
    assert "memory_items.predicate" in sql
    assert " limit " in sql

    session.rows = [uuid.uuid4(), uuid.uuid4()]
    assert (
        await service.has_multiple_current_matches(
            session,
            user_id=owner,
            subject="user",
            predicate="name",
            now=NOW,
        )
        is True
    )
