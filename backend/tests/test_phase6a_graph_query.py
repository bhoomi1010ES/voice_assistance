from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.graph import (
    GraphEdge,
    GraphEntity,
    GraphFallbackReason,
    GraphMemory,
    GraphNeighbor,
    GraphService,
    RelationshipType,
)


class _Session:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class _SessionFactory:
    def __init__(self) -> None:
        self.session = _Session()

    def __call__(self):
        factory = self

        class _Context:
            async def __aenter__(self):
                return factory.session

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

        return _Context()


class _Repository:
    def __init__(self, *, slow_resolution: bool = False) -> None:
        self.slow_resolution = slow_resolution
        self.entities: tuple[GraphEntity, ...] = ()
        self.neighbors: tuple[GraphNeighbor, ...] = ()
        self.memories: tuple[GraphMemory, ...] = ()

    async def find_entities_by_canonical_name(self, session, **kwargs):
        if self.slow_resolution:
            await asyncio.sleep(0.05)
        return tuple(
            entity
            for entity in self.entities
            if entity.normalized_name == kwargs["normalized_name"]
        )[: kwargs.get("limit")]

    async def find_entities_by_alias(self, session, **kwargs):
        return ()

    async def memory_enabled(self, session, *, user_id):
        return True

    async def get_owned_entity(self, session, *, user_id, entity_id):
        return next(entity for entity in self.entities if entity.id == entity_id)

    async def list_neighbors(self, session, **kwargs):
        entity_ids = set(kwargs["entity_ids"])
        relationship_type = kwargs.get("relationship_type")
        return tuple(
            neighbor
            for neighbor in self.neighbors
            if neighbor.origin_entity_id in entity_ids
            and (relationship_type is None or neighbor.edge.relationship_type == relationship_type)
        )[: kwargs["max_edges_per_entity"]]

    async def get_source_memories(self, session, *, source_memory_ids, **kwargs):
        wanted = set(source_memory_ids)
        return tuple(memory for memory in self.memories if memory.id in wanted)


def _fixture_repository() -> tuple[_Repository, uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    rahul_id, priya_id, memory_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    rahul = GraphEntity(
        id=rahul_id,
        user_id=user_id,
        entity_type="person",
        canonical_name="Rahul",
        normalized_name="rahul",
        created_at=now,
    )
    priya = GraphEntity(
        id=priya_id,
        user_id=user_id,
        entity_type="person",
        canonical_name="Priya",
        normalized_name="priya",
        created_at=now,
    )
    edge = GraphEdge(
        relationship_id=uuid.uuid4(),
        user_id=user_id,
        source_entity_id=rahul_id,
        source_name="Rahul",
        relationship_type=RelationshipType.COLLEAGUE_OF,
        target_entity_id=priya_id,
        target_name="Priya",
        source_memory_id=memory_id,
        confidence=0.9,
        valid_from=None,
        valid_to=None,
        status="active",
        created_at=now,
    )
    repository = _Repository()
    repository.entities = (rahul, priya)
    repository.neighbors = (
        GraphNeighbor(origin_entity_id=rahul_id, neighbor_entity_id=priya_id, edge=edge),
    )
    repository.memories = (
        GraphMemory(
            id=memory_id,
            user_id=user_id,
            content="Rahul is Priya's colleague.",
            memory_type="relationship",
            subject="Rahul",
            predicate="colleague_of",
            created_at=now,
        ),
    )
    return repository, user_id, rahul_id, memory_id


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        graph_rag_mode="shadow",
        memory_retrieval_mode="inject",
        stt_api_key="test-key",
        embedding_api_url="http://localhost:8001/v1/embeddings",
        rerank_api_url="http://localhost:8001/v1/rerank",
        **overrides,
    )


@pytest.mark.asyncio
async def test_query_evidence_returns_bounded_paths_and_complete_source_text() -> None:
    repository, user_id, rahul_id, memory_id = _fixture_repository()
    service = GraphService(
        _settings(graph_rag_timeout_ms=500),
        repository=repository,
    )

    result = await service.query_evidence(
        _Session(),
        user_id=user_id,
        query="Who works with Rahul?",
    )

    assert result.bundle is not None
    assert result.fallback_reasons == ()
    assert result.bundle.source_memory_ids == (memory_id,)
    assert result.bundle.source_memories[0].content == "Rahul is Priya's colleague."
    assert result.bundle.paths[0].entity_ids == (rahul_id, repository.entities[1].id)
    assert result.bundle.paths[0].hop_count == 1


@pytest.mark.asyncio
async def test_query_evidence_cancellation_is_fail_open() -> None:
    repository, user_id, _, _ = _fixture_repository()
    service = GraphService(
        _settings(),
        repository=repository,
    )

    result = await service.query_evidence(
        _Session(),
        user_id=user_id,
        query="Who works with Rahul?",
        cancellation_check=lambda: True,
    )

    assert result.bundle is None
    assert result.cancelled is True
    assert result.fallback_reasons == (GraphFallbackReason.GRAPH_CANCELLED,)


@pytest.mark.asyncio
async def test_query_evidence_timeout_rolls_back_isolated_session() -> None:
    repository, user_id, _, _ = _fixture_repository()
    repository.slow_resolution = True
    session = _Session()
    read_factory = _SessionFactory()
    service = GraphService(
        _settings(),
        repository=repository,
    )

    result = await service.query_evidence(
        session,
        user_id=user_id,
        query="Who works with Rahul?",
        timeout_ms=1,
        read_session_factory=read_factory,
    )

    assert result.bundle is None
    assert result.fallback_reasons == (GraphFallbackReason.GRAPH_TIMEOUT,)
    assert session.rollback_count == 0
    assert read_factory.session.rollback_count == 1


def test_query_evidence_disabled_mode_does_not_touch_session() -> None:
    repository, user_id, _, _ = _fixture_repository()
    session = _Session()
    service = GraphService(Settings(_env_file=None), repository=repository)

    result = asyncio.run(
        service.query_evidence(
            session,
            user_id=user_id,
            query="Who works with Rahul?",
        )
    )

    assert result.bundle is None
    assert result.decision.should_query is False
    assert session.rollback_count == 0
