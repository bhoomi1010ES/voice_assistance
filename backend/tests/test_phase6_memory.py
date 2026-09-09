from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.llm.tool_loop import ToolRegistry
from app.memory.chunking import chunk_text
from app.memory.extraction import extract_explicit_candidates, extract_explicit_tool_candidate
from app.memory.policy import ExtractionCandidate, validate_candidate
from app.memory.providers import (
    MemoryProviderError,
    RemoteEmbeddingProvider,
    RemoteReranker,
)
from app.memory.retrieval import dense_retrieve, fuse_candidates, rerank_fused
from app.memory.tool_tools import build_explicit_memory_save_call, register_memory_tools
from app.memory.types import (
    FusedMemory,
    MemoryIntent,
    MemoryType,
    build_memory_query_plan,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        stt_api_key="shared-test-key",
        memory_retrieval_mode="inject",
        embedding_api_url="https://memory.test/v1/embeddings",
        rerank_api_url="https://memory.test/v1/rerank",
        memory_embedding_dimension=3,
    )


def test_memory_tool_registration_respects_write_rollout_and_confirmation() -> None:
    read_registry = ToolRegistry()
    register_memory_tools(read_registry, allow_write=False)
    assert [tool.name for tool in read_registry.definitions()] == ["memory_search"]

    write_registry = ToolRegistry()
    register_memory_tools(write_registry, allow_write=True)
    tools = {name: write_registry.get(name) for name in write_registry.names()}
    assert set(tools) == {"memory_search", "memory_save", "memory_forget"}
    memory_search = tools["memory_search"]
    memory_save = tools["memory_save"]
    memory_forget = tools["memory_forget"]
    assert memory_search is not None
    assert memory_save is not None
    assert memory_forget is not None
    assert memory_search.read_only is True
    assert memory_search.requires_confirmation is False
    assert memory_save.read_only is False
    assert memory_save.requires_confirmation is True
    assert memory_forget.read_only is False
    assert memory_forget.requires_confirmation is True


def test_explicit_memory_call_is_grounded_deterministic_and_typed() -> None:
    turn_id = uuid.uuid4()
    call = build_explicit_memory_save_call(
        "Remember that I prefer early morning meetings.",
        turn_id=turn_id,
    )

    assert call is not None
    assert call.tool_call_id == f"server-memory-save-{turn_id}"
    assert call.name == "memory_save"
    assert call.arguments == {
        "content": "I prefer early morning meetings",
        "memory_type": "preference",
        "subject": "user",
        "predicate": "preference",
    }
    assert extract_explicit_tool_candidate("I prefer early morning meetings.") is None


def test_explicit_memory_call_rejects_sensitive_or_implicit_content() -> None:
    turn_id = uuid.uuid4()

    assert build_explicit_memory_save_call("I like tea.", turn_id=turn_id) is None
    assert (
        build_explicit_memory_save_call(
            "Remember that my password=do-not-store-this",
            turn_id=turn_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_embedding_contract_uses_shared_key_and_rejects_bad_dimension() -> None:
    settings = _settings()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": settings.memory_expected_embedding_model,
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            },
        )

    provider = RemoteEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    result = await provider.embed(("hello",))
    assert result.vectors == ((0.1, 0.2, 0.3),)
    assert requests[0].headers["authorization"] == "Bearer shared-test-key"
    assert requests[0].url.path == "/v1/embeddings"
    await provider.close()

    async def configured_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": settings.memory_expected_embedding_model,
                "dim": 3,
                "embeddings": [[0.1, 0.2, 0.3]],
            },
        )

    configured = RemoteEmbeddingProvider(
        settings,
        transport=httpx.MockTransport(configured_handler),
    )
    configured_result = await configured.embed(("hello",))
    assert configured_result.vectors == ((0.1, 0.2, 0.3),)
    await configured.close()

    async def bad_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": settings.memory_expected_embedding_model,
                "data": [{"index": 0, "embedding": [0.1]}],
            },
        )

    bad = RemoteEmbeddingProvider(settings, transport=httpx.MockTransport(bad_handler))
    with pytest.raises(MemoryProviderError) as error:
        await bad.embed(("hello",))
    assert error.value.code == "memory_provider_contract_invalid"
    await bad.close()


@pytest.mark.asyncio
async def test_reranker_contract_is_ordered_and_document_indexes_are_bounded() -> None:
    settings = _settings()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": settings.memory_expected_rerank_model,
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ],
            },
        )

    provider = RemoteReranker(settings, transport=httpx.MockTransport(handler))
    result = await provider.rerank("question", ("one", "two"))
    assert [(item.index, item.score) for item in result] == [(1, 0.9), (0, 0.2)]
    assert "authorization" in requests[0].headers
    await provider.close()

    async def configured_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": settings.memory_expected_rerank_model,
                "results": [
                    {"index": 1, "score": 0.9, "document": "two"},
                    {"index": 0, "score": 0.2, "document": "one"},
                ],
            },
        )

    configured = RemoteReranker(
        settings,
        transport=httpx.MockTransport(configured_handler),
    )
    configured_result = await configured.rerank("question", ("one", "two"))
    assert [(item.index, item.score) for item in configured_result] == [(1, 0.9), (0, 0.2)]
    await configured.close()


@pytest.mark.asyncio
async def test_post_only_provider_routes_are_ready_when_get_returns_405() -> None:
    settings = _settings()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, json={"detail": "Method Not Allowed"})

    embedding = RemoteEmbeddingProvider(settings, transport=httpx.MockTransport(handler))
    reranker = RemoteReranker(settings, transport=httpx.MockTransport(handler))
    assert await embedding.health() is True
    assert await reranker.health() is True
    await embedding.close()
    await reranker.close()


@pytest.mark.asyncio
async def test_dense_retrieval_orders_by_aggregate_distance() -> None:
    settings = _settings()
    provider = RemoteEmbeddingProvider(
        settings,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "model": settings.memory_expected_embedding_model,
                    "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
                },
            )
        ),
    )
    session = AsyncMock()
    session.execute.return_value = type("Result", (), {"all": lambda _self: []})()

    await dense_retrieve(
        session,
        user_id=uuid.uuid4(),
        plan=build_memory_query_plan("memory query"),
        provider=provider,
        limit=8,
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "min(memory_chunks.embedding" in sql
    assert "ORDER BY distance ASC" in sql
    await provider.close()


def test_query_plan_and_policy_are_deterministic_and_grounded() -> None:
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    plan = build_memory_query_plan("What are my latest preferences today?", now=now)
    assert plan.intent == MemoryIntent.TIME_RANGE
    assert plan.memory_types == (MemoryType.PREFERENCE,)
    assert plan.start_at == datetime(2026, 9, 8, tzinfo=UTC)
    candidate = ExtractionCandidate(
        content="I prefer green tea",
        memory_type=MemoryType.PREFERENCE,
        subject="user",
        predicate="preference",
        confidence=1,
        salience=1,
    )
    assert validate_candidate(candidate, "I prefer green tea.").content == "I prefer green tea"
    assert extract_explicit_candidates("Please remember that I work remotely.")[0].content == (
        "I work remotely"
    )
    with pytest.raises(ValueError, match="memory_candidate_sensitive"):
        validate_candidate(
            ExtractionCandidate(content="password: secret", memory_type=MemoryType.FACT),
            "password: secret",
        )


def test_chunking_and_rrf_collapse_are_bounded_and_stable() -> None:
    chunks = chunk_text("one two three four five six seven eight", max_chars=128, overlap_chars=3)
    assert chunks
    assert all(len(chunk) <= 128 for chunk in chunks)
    user_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    created = datetime.now(UTC)
    first = FusedMemory(
        memory_id=first_id,
        user_id=user_id,
        content="first",
        rank=1,
        created_at=created,
        memory_type=MemoryType.FACT,
    )
    second = first.model_copy(update={"memory_id": second_id, "content": "second"})
    from app.memory.types import MemoryCandidate

    candidates = [
        MemoryCandidate(
            memory_id=first_id,
            user_id=user_id,
            content="first",
            source="fts",
            source_rank=1,
            created_at=created,
            memory_type=MemoryType.FACT,
        ),
        MemoryCandidate(
            memory_id=first_id,
            user_id=user_id,
            content="first",
            source="dense",
            source_rank=2,
            created_at=created,
            memory_type=MemoryType.FACT,
        ),
        MemoryCandidate(
            memory_id=second_id,
            user_id=user_id,
            content="second",
            source="structured",
            source_rank=1,
            created_at=created,
            memory_type=MemoryType.FACT,
        ),
    ]
    fused = fuse_candidates((candidates[:2], candidates[2:]), k=1, limit=8)
    assert len(fused) == 2
    assert fused[0].memory_id == first_id
    assert fused[0].sources == ("dense", "fts")
    assert second.content == "second"


@pytest.mark.asyncio
async def test_rerank_falls_back_without_exposing_provider_payload() -> None:
    settings = _settings()
    created = datetime.now(UTC)
    item = FusedMemory(
        memory_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content="safe memory",
        rank=1,
        created_at=created,
        memory_type=MemoryType.FACT,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "provider secret"})

    provider = RemoteReranker(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(MemoryProviderError) as error:
        await rerank_fused((item,), query="safe", provider=provider, limit=8)
    assert error.value.code == "memory_provider_rate_limited"
    assert "provider secret" not in str(error.value)
    await provider.close()
