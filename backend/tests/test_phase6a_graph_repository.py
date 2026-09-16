from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.graph import GraphEntityAmbiguous, GraphEntityNotFound, GraphInvalidTraversal, GraphService
from app.graph.repository import GraphRepository, normalize_graph_name
from app.graph.types import GraphEntity, GraphResolution


def test_graph_name_normalization_reuses_memory_convention() -> None:
    assert normalize_graph_name("  ＲＡＨＵＬ\tKumar  ") == "rahul kumar"
    assert normalize_graph_name("ALEX") == "alex"
    with pytest.raises(ValueError, match="must not be blank"):
        normalize_graph_name(" \t ")
    with pytest.raises(ValueError, match="at most 512"):
        normalize_graph_name("x" * 513, max_length=512)


def test_resolution_does_not_choose_ambiguous_entity() -> None:
    timestamp = datetime(2026, 9, 15, tzinfo=UTC)
    first = GraphEntity(uuid.uuid4(), uuid.uuid4(), "person", "Alex Smith", "alex smith", timestamp)
    second = GraphEntity(
        uuid.uuid4(), uuid.uuid4(), "person", "Alex Patel", "alex patel", timestamp
    )
    result = GraphResolution(status="ambiguous", candidates=(first, second), matched_by="alias")
    assert result.entity is None
    with pytest.raises(GraphEntityAmbiguous):
        result.require_entity()

    missing = GraphResolution(status="not_found", candidates=(), matched_by=None)
    with pytest.raises(GraphEntityNotFound):
        missing.require_entity()


def test_neighbor_query_is_owner_scoped_current_and_bounded() -> None:
    settings = Settings(_env_file=None, graph_max_edges_per_entity=3)
    repository = GraphRepository(settings)
    query = repository._build_neighbor_statement(
        user_id=uuid.uuid4(),
        entity_ids=(uuid.uuid4(),),
        direction="both",
        as_of=datetime(2026, 9, 15, tzinfo=UTC),
        relationship_type=None,
        max_edges_per_entity=3,
    )
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "row_number() OVER (PARTITION BY" in sql
    assert "entity_relationships.user_id" in sql
    assert "memory_items.status" in sql
    assert "entity_relationships.status" in sql


@pytest.mark.asyncio
async def test_stage_three_rejects_depth_beyond_two_or_configuration() -> None:
    service = GraphService(Settings(_env_file=None, graph_max_depth=3))
    with pytest.raises(GraphInvalidTraversal, match="Stage 3 limit"):
        await service.traverse(
            None,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            depth=3,
        )

    service_with_depth_one = GraphService(Settings(_env_file=None, graph_max_depth=1))
    with pytest.raises(GraphInvalidTraversal, match="Stage 3 limit"):
        await service_with_depth_one.traverse(
            None,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            depth=2,
        )
