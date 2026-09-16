from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.api.memories import _search_memories, create_memory
from app.core.config import Settings
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.memory.chunking import chunk_text
from app.memory.extraction import extract_explicit_candidates, extract_explicit_tool_candidate
from app.memory.jobs import MemoryJobWorker
from app.memory.policy import ExtractionCandidate, validate_candidate
from app.memory.providers import (
    MemoryProviderError,
    RemoteEmbeddingProvider,
    RemoteReranker,
)
from app.memory.retrieval import (
    MemoryRetrievalService,
    apply_relevance_boundary,
    dense_retrieve,
    fuse_candidates,
    rerank_fused,
    should_run_structured_retrieval,
)
from app.memory.tool_tools import (
    MemorySaveArguments,
    build_explicit_memory_save_call,
    memory_forget_handler,
    memory_save_handler,
    register_memory_tools,
)
from app.memory.types import (
    FusedMemory,
    MemoryCandidate,
    MemoryIntent,
    MemoryType,
    build_memory_query_plan,
)
from app.schemas import MemoryCreateRequest
from app.services.auth import AuthPrincipal


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


def test_automatic_extraction_handles_durable_forms_and_rejections() -> None:
    cases = {
        "Actually, I prefer green tea.": MemoryType.PREFERENCE,
        "My preference is online meetings.": MemoryType.PREFERENCE,
        "I work at Acme on Project Atlas.": MemoryType.FACT,
        "My home base is Pune.": MemoryType.FACT,
        "Rahul is my colleague.": MemoryType.RELATIONSHIP,
        "I usually run on Sunday mornings.": MemoryType.ROUTINE,
        "Every Friday I review Project Atlas.": MemoryType.ROUTINE,
    }
    for utterance, expected_type in cases.items():
        candidates = extract_explicit_candidates(utterance)
        assert len(candidates) == 1
        assert candidates[0].memory_type == expected_type
        assert candidates[0].content.rstrip(".") in utterance

    for utterance in (
        "I like this song today.",
        "Don't remember that I like tea.",
        "Save this task in my memory: call Rahul tomorrow.",
    ):
        assert extract_explicit_candidates(utterance) == ()


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


def test_structured_retrieval_is_reserved_for_structured_intents() -> None:
    assert not should_run_structured_retrieval(build_memory_query_plan("Where do I work?"))
    assert should_run_structured_retrieval(build_memory_query_plan("What happened yesterday?"))
    assert should_run_structured_retrieval(build_memory_query_plan("What drink do I prefer?"))


def test_relevance_boundary_rejects_ungrounded_neighbors_but_keeps_exact_evidence() -> None:
    created = datetime.now(UTC)
    weak_id = uuid.uuid4()
    exact_id = uuid.uuid4()
    user_id = uuid.uuid4()
    weak = FusedMemory(
        memory_id=weak_id,
        user_id=user_id,
        content="unrelated",
        rank=1,
        score=0.00001,
        created_at=created,
        memory_type=MemoryType.FACT,
    )
    exact = weak.model_copy(
        update={"memory_id": exact_id, "content": "exact lexical evidence", "rank": 2}
    )
    accepted = apply_relevance_boundary(
        (weak, exact),
        minimum_score=0.00005,
        trusted_memory_ids={exact_id},
    )
    assert tuple(item.memory_id for item in accepted) == (exact_id,)


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


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _RetrievalSession:
    def begin_nested(self):
        return _NestedTransaction()


def _candidate(
    *, user_id: uuid.UUID, memory_id: uuid.UUID, source: str, content: str
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        source=source,
        source_rank=1,
        score=1.0,
        created_at=datetime.now(UTC),
        memory_type=MemoryType.FACT,
    )


@pytest.mark.asyncio
async def test_reranker_failure_drops_dense_only_candidates(monkeypatch) -> None:
    import app.memory.retrieval as retrieval

    settings = _settings()
    user_id = uuid.uuid4()
    lexical = _candidate(
        user_id=user_id,
        memory_id=uuid.uuid4(),
        source="fts",
        content="I work remotely",
    )
    dense_only = _candidate(
        user_id=user_id,
        memory_id=uuid.uuid4(),
        source="dense",
        content="Unrelated nearest neighbour",
    )
    monkeypatch.setattr(retrieval, "fts_retrieve", AsyncMock(return_value=[lexical]))
    monkeypatch.setattr(retrieval, "dense_retrieve", AsyncMock(return_value=[dense_only]))
    reranker = AsyncMock()
    reranker.rerank.side_effect = MemoryProviderError("memory_provider_unavailable")

    service = MemoryRetrievalService(
        settings,
        embedding_provider=object(),
        reranker=reranker,
    )
    result = await service.retrieve(
        _RetrievalSession(),
        user_id=user_id,
        query="where do I work?",
    )

    assert result.status == "degraded"
    assert result.provider_error == "memory_provider_unavailable"
    assert [item.memory_id for item in result.memories] == [lexical.memory_id]


@pytest.mark.asyncio
async def test_optional_retrieval_database_failure_isolated_to_savepoint(monkeypatch) -> None:
    import app.memory.retrieval as retrieval

    async def fail(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic retrieval failure")

    monkeypatch.setattr(retrieval, "fts_retrieve", fail)
    service = MemoryRetrievalService(_settings())
    result = await service.retrieve(
        _RetrievalSession(),
        user_id=uuid.uuid4(),
        query="where do I work?",
    )

    assert result.status == "degraded"
    assert result.provider_error == "memory_database_error"
    assert result.memories == ()


@pytest.mark.asyncio
async def test_memory_search_passes_requested_limit_to_hybrid_service() -> None:
    user_id = uuid.uuid4()
    service = SimpleNamespace(retrieve=AsyncMock(return_value=SimpleNamespace(memories=())))
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=_settings(), memory_service=service),
        )
    )
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(
        id=user_id,
        status="active",
        memory_enabled=True,
    )
    principal = SimpleNamespace(user_id=user_id)

    assert await _search_memories("work", 3, request, session, principal) == []
    call = service.retrieve.await_args
    assert call.kwargs["limit"] == 3


@pytest.mark.asyncio
async def test_manual_rest_create_remains_available_when_operator_writes_are_off(
    monkeypatch,
) -> None:
    import app.api.memories as memories_api

    saved = SimpleNamespace(id=uuid.uuid4())
    writer = AsyncMock(return_value=(saved, True))
    monkeypatch.setattr(memories_api.MemoryWriter, "write_candidate", writer)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(
        id=uuid.uuid4(), status="active", memory_enabled=True
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=_settings().model_copy(update={"memory_write_enabled": False})
            )
        )
    )
    principal = AuthPrincipal(
        user_id=session.get.return_value.id, session_id=uuid.uuid4(), device_id=uuid.uuid4()
    )

    result = await create_memory(
        MemoryCreateRequest(content="I prefer jasmine tea", memory_type="preference"),
        request,
        session,
        principal,
    )

    assert result is saved
    writer.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirmed_memory_save_carries_turn_session_and_source_message(
    monkeypatch,
) -> None:
    import app.memory.tool_tools as tool_tools

    source_message_id = uuid.uuid4()
    saved_memory_id = uuid.uuid4()
    writer = AsyncMock(return_value=(SimpleNamespace(id=saved_memory_id), True))
    monkeypatch.setattr(tool_tools.MemoryWriter, "write_candidate", writer)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(memory_enabled=True)
    session.scalar.return_value = source_message_id
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        db=session,
        memory_settings=_settings().model_copy(update={"memory_write_enabled": True}),
    )

    result = await memory_save_handler(
        context,
        MemorySaveArguments(content="I prefer jasmine tea", memory_type=MemoryType.PREFERENCE),
    )

    assert result == {"memory_id": str(saved_memory_id), "created": True}
    call = writer.await_args
    assert call.kwargs["source_message_id"] == source_message_id
    assert call.kwargs["source_turn_id"] == context.turn_id
    assert call.kwargs["source_session_id"] == context.session_id


@pytest.mark.asyncio
async def test_confirmed_memory_forget_bumps_version_for_real_deletion(monkeypatch) -> None:
    import app.memory.tool_tools as tool_tools

    item = SimpleNamespace(id=uuid.uuid4(), status="active")
    bump = AsyncMock()
    monkeypatch.setattr(tool_tools.MemoryRepository, "bump_memory_version", bump)
    session = AsyncMock()
    session.scalar.return_value = item
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        db=session,
    )

    result = await memory_forget_handler(
        context,
        tool_tools.MemoryForgetArguments(memory_id=item.id),
    )

    assert result == {"deleted": True, "memory_id": str(item.id)}
    session.delete.assert_awaited_once_with(item)
    bump.assert_awaited_once_with(session, user_id=context.user_id)


@pytest.mark.asyncio
async def test_reembed_jobs_use_embedding_handler_and_unsupported_jobs_dead_letter() -> None:
    settings = _settings()
    worker = MemoryJobWorker(settings)
    session = AsyncMock()
    reembed_job = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_id=uuid.uuid4(),
        job_type="reembed_memory",
        attempts=1,
        locked_at=None,
    )
    worker.repository.claim_next = AsyncMock(return_value=reembed_job)
    worker._embed_memory = AsyncMock()

    assert await worker.run_once(session) is True
    worker._embed_memory.assert_awaited_once_with(session, reembed_job)

    unsupported = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        memory_id=None,
        job_type="future_memory_job",
        attempts=1,
        locked_at=None,
    )
    worker.repository.claim_next = AsyncMock(return_value=unsupported)
    assert await worker.run_once(session) is True
    statement = session.execute.await_args.args[0]
    assert statement.compile().params["status"] == "dead"
    assert statement.compile().params["last_error_code"] == "memory_job_type_unsupported"
