from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.models import Entity, EntityAlias, EntityRelationship, MemoryEntity, MemoryItem, User

pytestmark = pytest.mark.integration


@pytest.fixture
def graph_database():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL graph schema checks.")
    engine = create_async_engine(Settings().database_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    asyncio.run(engine.dispose())


async def _expect_database_rejection(session, row) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(row)
            await session.flush()


def test_graph_ownership_uniqueness_and_check_constraints(graph_database) -> None:
    _engine, factory = graph_database

    async def run() -> None:
        user_a_id = uuid.uuid4()
        user_b_id = uuid.uuid4()
        entity_a1_id, entity_a2_id, entity_b1_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        memory_a1_id, memory_a2_id, memory_b1_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        timestamp = datetime(2026, 9, 1, tzinfo=UTC)

        async with factory() as session:
            await session.begin()
            try:
                session.add_all(
                    [
                        User(
                            id=user_a_id,
                            email=f"graph-schema-{user_a_id.hex}@example.invalid",
                            password_hash="graph-schema-test-only",
                        ),
                        User(
                            id=user_b_id,
                            email=f"graph-schema-{user_b_id.hex}@example.invalid",
                            password_hash="graph-schema-test-only",
                        ),
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        Entity(
                            id=entity_a1_id,
                            user_id=user_a_id,
                            entity_type="person",
                            canonical_name="Rahul",
                            normalized_name="rahul",
                        ),
                        Entity(
                            id=entity_a2_id,
                            user_id=user_a_id,
                            entity_type="project",
                            canonical_name="Project Alpha",
                            normalized_name="project alpha",
                        ),
                        Entity(
                            id=entity_b1_id,
                            user_id=user_b_id,
                            entity_type="person",
                            canonical_name="Other user entity",
                            normalized_name="other user entity",
                        ),
                        MemoryItem(
                            id=memory_a1_id,
                            user_id=user_a_id,
                            content="Synthetic graph schema provenance one.",
                            memory_type="relationship",
                            source_kind="manual_api",
                        ),
                        MemoryItem(
                            id=memory_a2_id,
                            user_id=user_a_id,
                            content="Synthetic graph schema provenance two.",
                            memory_type="relationship",
                            source_kind="manual_api",
                        ),
                        MemoryItem(
                            id=memory_b1_id,
                            user_id=user_b_id,
                            content="Synthetic cross-user provenance.",
                            memory_type="relationship",
                            source_kind="manual_api",
                        ),
                    ]
                )
                await session.flush()
                session.add(
                    MemoryEntity(
                        memory_id=memory_a1_id,
                        entity_id=entity_a1_id,
                        user_id=user_a_id,
                        relation="mentions",
                    )
                )
                await session.flush()

                alias = EntityAlias(
                    user_id=user_a_id,
                    entity_id=entity_a1_id,
                    alias="Rahul",
                    normalized_alias="rahul",
                    source_memory_id=memory_a1_id,
                    source_kind="memory",
                )
                alias_on_distinct_entity = EntityAlias(
                    user_id=user_a_id,
                    entity_id=entity_a2_id,
                    alias="Rahul",
                    normalized_alias="rahul",
                    source_kind="manual",
                )
                canonical_alias = EntityAlias(
                    user_id=user_a_id,
                    entity_id=entity_a1_id,
                    alias="Rahul Kumar",
                    normalized_alias="rahul kumar",
                    source_kind="canonical",
                )
                legacy_alias = EntityAlias(
                    user_id=user_a_id,
                    entity_id=entity_a1_id,
                    alias="R.",
                    normalized_alias="r",
                    source_kind="legacy",
                )
                session.add_all([alias, alias_on_distinct_entity, canonical_alias, legacy_alias])
                await session.flush()

                await _expect_database_rejection(
                    session,
                    EntityAlias(
                        user_id=user_a_id,
                        entity_id=entity_a1_id,
                        alias="RAHUL",
                        normalized_alias="rahul",
                        source_kind="manual",
                    ),
                )
                await _expect_database_rejection(
                    session,
                    EntityAlias(
                        user_id=user_a_id,
                        entity_id=entity_b1_id,
                        alias="other",
                        normalized_alias="other",
                        source_kind="manual",
                    ),
                )
                await _expect_database_rejection(
                    session,
                    EntityAlias(
                        user_id=user_a_id,
                        entity_id=entity_a1_id,
                        alias="cross-user source",
                        normalized_alias="cross-user source",
                        source_memory_id=memory_b1_id,
                        source_kind="memory",
                    ),
                )
                await _expect_database_rejection(
                    session,
                    EntityAlias(
                        user_id=user_a_id,
                        entity_id=entity_a1_id,
                        alias="invalid source",
                        normalized_alias="invalid source",
                        source_kind="generated",
                    ),
                )
                await _expect_database_rejection(
                    session,
                    Entity(
                        user_id=user_a_id,
                        entity_type="subject",
                        canonical_name="Legacy subject",
                        normalized_name="legacy subject",
                    ),
                )
                session.add(
                    Entity(
                        user_id=user_a_id,
                        entity_type="self",
                        canonical_name="Self",
                        normalized_name="self",
                    )
                )
                await session.flush()
                await _expect_database_rejection(
                    session,
                    Entity(
                        user_id=user_a_id,
                        entity_type="self",
                        canonical_name="Me",
                        normalized_name="me",
                    ),
                )

                def edge(
                    *,
                    relationship_type: str = "WORKS_ON",
                    source_entity_id: uuid.UUID = entity_a1_id,
                    target_entity_id: uuid.UUID = entity_a2_id,
                    source_memory_id: uuid.UUID = memory_a1_id,
                    confidence: float = 0.5,
                    status: str = "active",
                    valid_from: datetime | None = None,
                    valid_to: datetime | None = None,
                ) -> EntityRelationship:
                    return EntityRelationship(
                        user_id=user_a_id,
                        source_entity_id=source_entity_id,
                        relationship_type=relationship_type,
                        target_entity_id=target_entity_id,
                        source_memory_id=source_memory_id,
                        confidence=confidence,
                        status=status,
                        valid_from=valid_from,
                        valid_to=valid_to,
                        extraction_policy_version="phase6a-schema-test",
                    )

                session.add(edge(confidence=0.0, valid_from=timestamp))
                session.add(edge(source_memory_id=memory_a2_id, confidence=0.5, valid_to=timestamp))
                session.add(
                    edge(
                        relationship_type="RELATED_TO",
                        source_memory_id=memory_a2_id,
                        confidence=1.0,
                        status="superseded",
                        valid_from=timestamp,
                        valid_to=timestamp + timedelta(days=1),
                    )
                )
                await session.flush()

                await _expect_database_rejection(
                    session,
                    edge(),
                )
                await _expect_database_rejection(
                    session,
                    edge(source_entity_id=entity_b1_id),
                )
                await _expect_database_rejection(
                    session,
                    edge(target_entity_id=entity_b1_id),
                )
                await _expect_database_rejection(
                    session,
                    edge(source_memory_id=memory_b1_id),
                )
                await _expect_database_rejection(
                    session,
                    edge(source_entity_id=entity_a1_id, target_entity_id=entity_a1_id),
                )
                await _expect_database_rejection(
                    session,
                    edge(relationship_type="NOT_A_REVIEWED_RELATION"),
                )
                await _expect_database_rejection(session, edge(confidence=-0.1))
                await _expect_database_rejection(session, edge(confidence=1.1))
                await _expect_database_rejection(session, edge(status="deleted"))
                await _expect_database_rejection(
                    session,
                    edge(valid_from=timestamp, valid_to=timestamp - timedelta(days=1)),
                )

                await session.delete(await session.get(MemoryItem, memory_a1_id))
                await session.flush()
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EntityAlias)
                        .where(EntityAlias.source_memory_id == memory_a1_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EntityRelationship)
                        .where(EntityRelationship.source_memory_id == memory_a1_id)
                    )
                    == 0
                )

                await session.delete(await session.get(Entity, entity_a2_id))
                await session.flush()
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EntityAlias)
                        .where(EntityAlias.entity_id == entity_a2_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(EntityRelationship)
                        .where(EntityRelationship.target_entity_id == entity_a2_id)
                    )
                    == 0
                )
            finally:
                await session.rollback()

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.id.in_([user_a_id, user_b_id]))
                )
                == 0
            )

    asyncio.run(run())


def test_graph_lookup_indexes_exist_and_match_query_shapes(graph_database) -> None:
    engine, _factory = graph_database

    async def run() -> None:
        expected_indexes = {
            "ix_entity_aliases_user_normalized",
            "ix_entity_aliases_user_entity",
            "ix_entity_relationships_source_status",
            "ix_entity_relationships_target_status",
            "ix_entity_relationships_type_status",
            "ix_entity_relationships_user_memory",
        }
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND tablename IN ('entity_aliases', 'entity_relationships')"
                    )
                )
            ).scalars()
            index_names = set(rows)
            assert expected_indexes <= index_names

            await connection.execute(text("SET enable_seqscan = off"))
            query_shapes = (
                "SELECT * FROM entity_aliases WHERE user_id = :user_id "
                "AND normalized_alias = :lookup",
                "SELECT * FROM entity_relationships WHERE user_id = :user_id "
                "AND source_entity_id = :entity_id AND status = 'active'",
                "SELECT * FROM entity_relationships WHERE user_id = :user_id "
                "AND target_entity_id = :entity_id AND status = 'active'",
                "SELECT * FROM entity_relationships WHERE user_id = :user_id "
                "AND relationship_type = :lookup AND status = 'active'",
                "SELECT * FROM entity_relationships WHERE user_id = :user_id "
                "AND source_memory_id = :entity_id",
            )
            for statement in query_shapes:
                plan = (
                    await connection.execute(
                        text(f"EXPLAIN {statement}"),
                        {"user_id": uuid.uuid4(), "entity_id": uuid.uuid4(), "lookup": "test"},
                    )
                ).scalars()
                assert "Index" in "\n".join(plan)
            await connection.rollback()

    asyncio.run(run())
