from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.memory.forget_resolution import resolve_forget_target
from app.memory.types import (
    FusedMemory,
    MemoryQueryPlan,
    MemoryRetrievalResult,
    MemoryStatus,
    MemoryType,
)


class _Rows:
    def __init__(self, values) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class _Session:
    def __init__(self, values) -> None:
        self.values = values

    async def scalars(self, _query):
        return _Rows(self.values)


def _row(memory_id, user_id, content, status="active"):
    return SimpleNamespace(id=memory_id, user_id=user_id, content=content, status=status)


@pytest.mark.asyncio
async def test_forget_resolution_finds_exact_active_owned_target() -> None:
    owner = uuid.uuid4()
    memory_id = uuid.uuid4()

    result = await resolve_forget_target(
        _Session([_row(memory_id, owner, "I prefer cappuccino")]),
        user_id=owner,
        transcript="Forget that I prefer cappuccino.",
    )

    assert result.status == "unique"
    assert result.memory_ids == (memory_id,)


@pytest.mark.asyncio
async def test_forget_resolution_uses_owner_scoped_semantic_retrieval() -> None:
    owner = uuid.uuid4()
    memory_id = uuid.uuid4()
    calls = []
    memory = FusedMemory(
        memory_id=memory_id,
        user_id=owner,
        content="The place I like to shop is Phoenix Marketcity.",
        score=0.91,
        rank=1,
        sources=("dense",),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
        memory_type=MemoryType.PREFERENCE,
        status=MemoryStatus.ACTIVE,
    )

    class SemanticService:
        settings = SimpleNamespace(memory_min_rerank_score=0.35)

        async def retrieve(self, *args, **kwargs):
            calls.append((args, kwargs))
            return MemoryRetrievalResult(
                status="ready",
                plan=MemoryQueryPlan(normalized_query=kwargs["query"]),
                memories=(memory,),
            )

    result = await resolve_forget_target(
        _Session([_row(memory_id, owner, memory.content)]),
        user_id=owner,
        transcript="Forget about my preferred shopping mall.",
        memory_service=SemanticService(),
    )

    assert result.status == "unique"
    assert result.memory_ids == (memory_id,)
    assert calls[0][1]["user_id"] == owner
    assert calls[0][1]["query"] == "preferred shopping mall"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows_factory", "expected_status"),
    [
        (
            lambda owner: [
                _row(uuid.uuid4(), owner, "My preferred shopping mall is Phoenix Mall"),
                    _row(uuid.uuid4(), owner, "My preferred shopping mall is Select Citywalk"),
            ],
            "ambiguous",
        ),
        (lambda _owner: [], "no_match"),
        (
            lambda owner: [
                _row(
                    uuid.uuid4(),
                    owner,
                    "My preferred shopping mall is Phoenix Mall",
                    "superseded",
                )
            ],
            "no_match",
        ),
        (
            lambda owner: [
                _row(
                    uuid.uuid4(),
                    owner,
                    "My preferred shopping mall is Phoenix Mall",
                    "deleted",
                )
            ],
            "no_match",
        ),
        (
            lambda _owner: [
                _row(
                    uuid.uuid4(),
                    uuid.uuid4(),
                    "My preferred shopping mall is Phoenix Mall",
                )
            ],
            "no_match",
        ),
    ],
)
async def test_forget_resolution_ambiguity_and_inactive_or_foreign_rows_fail_closed(
    rows_factory,
    expected_status: str,
) -> None:
    owner = uuid.uuid4()

    result = await resolve_forget_target(
        _Session(rows_factory(owner)),
        user_id=owner,
        transcript="Forget about my preferred shopping mall.",
    )

    assert result.status == expected_status
    if expected_status == "ambiguous":
        assert len(result.memory_ids) == 2
    else:
        assert result.memory_ids == ()
