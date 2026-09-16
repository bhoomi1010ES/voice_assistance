from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.graph.indexing import GraphIndexingService
from app.graph.policy import GRAPH_INDEX_POLICY_VERSION
from app.graph.repository import GraphRepository
from app.graph.service import GraphService
from app.memory.jobs import MemoryJobWorker
from app.memory.policy import ExtractionCandidate
from app.memory.providers import EmbeddingResponse
from app.memory.repository import MemoryRepository
from app.memory.types import MemorySourceKind, MemoryType
from app.memory.writer import MemoryWriter
from app.models import (
    AuthSession,
    Device,
    Entity,
    EntityAlias,
    EntityRelationship,
    MemoryEntity,
    MemoryItem,
    MemoryJob,
    User,
    VoiceSession,
)

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def graph_index_database():
    """Run graph-write integration cases on a disposable upgraded PostgreSQL DB."""

    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL graph-index checks.")
    source_url = make_url(Settings().database_dsn)
    database_name = f"phase6a_graph_index_{uuid.uuid4().hex[:12]}"
    test_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(source_url)
    created = False

    async def create_database() -> bool:
        async with admin_engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            allowed = await connection.scalar(
                text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
            )
            if not allowed:
                return False
            await connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        return True

    if not asyncio.run(create_database()):
        asyncio.run(admin_engine.dispose())
        pytest.skip("Connected PostgreSQL role cannot create a disposable database.")
    created = True
    asyncio.run(admin_engine.dispose())

    child_environment = os.environ.copy()
    child_environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=child_environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    engine = None
    try:
        if migration.returncode != 0:
            raise AssertionError(f"Disposable graph DB migration failed:\n{migration.stderr}")
        engine = create_async_engine(test_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory
    finally:
        if engine is not None:
            asyncio.run(engine.dispose())
        if created:

            async def drop_database() -> None:
                drop_engine = create_async_engine(source_url)
                try:
                    async with drop_engine.connect() as connection:
                        connection = await connection.execution_options(
                            isolation_level="AUTOCOMMIT"
                        )
                        await connection.execute(
                            text(
                                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                "WHERE datname=:database_name AND pid<>pg_backend_pid()"
                            ),
                            {"database_name": database_name},
                        )
                        await connection.exec_driver_sql(
                            f'DROP DATABASE IF EXISTS "{database_name}"'
                        )
                finally:
                    await drop_engine.dispose()

            asyncio.run(drop_database())


@pytest.fixture
def graph_user(graph_index_database):
    factory = graph_index_database
    user_id = uuid.uuid4()

    async def create() -> None:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"graph-index-{user_id.hex}@example.invalid",
                    password_hash="graph-index-test-only",
                    memory_enabled=True,
                )
            )
            await session.commit()

    asyncio.run(create())
    yield user_id

    async def cleanup() -> None:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()

    asyncio.run(cleanup())


def _graph_settings(*, enabled: bool = True) -> Settings:
    return Settings(_env_file=None, graph_rag_mode="off", graph_write_enabled=enabled)


def _eligible_candidate(
    *,
    content: str = "Rahul works on Project Alpha.",
    target: str = "Project Alpha",
    confidence: float = 0.92,
) -> ExtractionCandidate:
    return ExtractionCandidate(
        content=content,
        memory_type=MemoryType.RELATIONSHIP,
        subject="Rahul",
        predicate="works_on",
        object_json={"name": target, "type": "project"},
        confidence=confidence,
        salience=0.9,
    )


class _StubEmbeddingProvider:
    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResponse:
        return EmbeddingResponse(
            model="test-embedding",
            vectors=tuple(tuple(0.0 for _ in range(1024)) for _ in texts),
        )


async def _write_memory(
    factory,
    user_id: uuid.UUID,
    *,
    settings: Settings,
    candidate=None,
    source_session_id: uuid.UUID | None = None,
):
    candidate = candidate or _eligible_candidate()
    async with factory() as session:
        memory, created = await MemoryWriter(settings).write_candidate(
            session,
            user_id=user_id,
            candidate=candidate,
            source_kind=MemorySourceKind.MANUAL_API,
            source_session_id=source_session_id,
        )
        await session.commit()
        return memory.id, created


async def _seed_memory(
    factory,
    *,
    user_id: uuid.UUID,
    subject: str = "Rahul",
    predicate: str = "works_on",
    object_json: dict | None = None,
    memory_type: str = "relationship",
    confidence: float = 0.92,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    subject_link: bool = True,
) -> uuid.UUID:
    memory_id = uuid.uuid4()
    async with factory() as session:
        memory = MemoryItem(
            id=memory_id,
            user_id=user_id,
            content="synthetic validated graph-index fixture",
            memory_type=memory_type,
            subject=subject,
            predicate=predicate,
            object_json=object_json or {"name": "Project Alpha", "type": "project"},
            confidence=confidence,
            source_kind="manual_api",
            extraction_policy_version="test-memory-policy",
            status="active",
            valid_from=valid_from,
            valid_to=valid_to,
        )
        session.add(memory)
        await session.flush()
        if subject_link:
            subject_entity = await session.scalar(
                select(Entity).where(
                    Entity.user_id == user_id,
                    Entity.entity_type == "subject",
                    Entity.normalized_name == subject.casefold(),
                )
            )
            if subject_entity is None:
                subject_entity = Entity(
                    user_id=user_id,
                    entity_type="subject",
                    canonical_name=subject,
                    normalized_name=subject.casefold(),
                )
                session.add(subject_entity)
                await session.flush()
            session.add(
                MemoryEntity(
                    memory_id=memory_id,
                    entity_id=subject_entity.id,
                    user_id=user_id,
                    relation=predicate,
                )
            )
        await session.commit()
    return memory_id


def test_graph_flag_gates_enqueue_and_worker_indexes_with_provenance(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        disabled_memory_id, created = await _write_memory(
            factory,
            user_id,
            settings=_graph_settings(enabled=False),
            candidate=_eligible_candidate(
                content="Rahul works on Project Disabled.", target="Project Disabled"
            ),
        )
        assert created
        async with factory() as session:
            jobs = (
                await session.scalars(select(MemoryJob).where(MemoryJob.user_id == user_id))
            ).all()
            assert [job.job_type for job in jobs] == ["embed_memory"]
            assert await session.scalar(select(func.count()).select_from(EntityRelationship)) == 0
            assert await session.get(MemoryItem, disabled_memory_id) is not None
            disabled_embed = next(job for job in jobs if job.memory_id == disabled_memory_id)
            disabled_embed.status = "completed"
            disabled_embed.completed_at = datetime.now(UTC)
            await session.commit()

        enabled_memory_id, created = await _write_memory(
            factory, user_id, settings=_graph_settings()
        )
        assert created
        # A same-candidate replay resolves to the same memory and graph job key.
        replay_id, replay_created = await _write_memory(
            factory, user_id, settings=_graph_settings()
        )
        assert replay_id == enabled_memory_id
        assert not replay_created

        async with factory() as session:
            graph_jobs = (
                await session.scalars(
                    select(MemoryJob).where(
                        MemoryJob.user_id == user_id,
                        MemoryJob.job_type == "index_memory_graph",
                    )
                )
            ).all()
            assert len(graph_jobs) == 1
            assert graph_jobs[0].policy_version == GRAPH_INDEX_POLICY_VERSION

        async def enqueue_again() -> bool:
            async with factory() as session:
                _job, was_created = await MemoryRepository().enqueue_graph_index(
                    session,
                    user_id=user_id,
                    memory_id=enabled_memory_id,
                    policy_version=GRAPH_INDEX_POLICY_VERSION,
                )
                await session.commit()
                return was_created

        assert await asyncio.gather(enqueue_again(), enqueue_again()) == [False, False]

        worker = MemoryJobWorker(_graph_settings(), embedding_provider=_StubEmbeddingProvider())
        async with factory() as session:
            assert await worker.run_once(session) is True  # existing embedding job
            assert await worker.run_once(session) is True  # graph indexing job

        async with factory() as session:
            edge = await session.scalar(
                select(EntityRelationship).where(
                    EntityRelationship.user_id == user_id,
                    EntityRelationship.source_memory_id == enabled_memory_id,
                )
            )
            assert edge is not None
            assert edge.relationship_type == "WORKS_ON"
            assert edge.confidence == pytest.approx(0.92)
            assert edge.status == "active"
            source_link = await session.scalar(
                select(MemoryEntity).where(
                    MemoryEntity.memory_id == enabled_memory_id,
                    MemoryEntity.user_id == user_id,
                )
            )
            assert source_link is not None and edge.source_entity_id == source_link.entity_id
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == disabled_memory_id)
                )
                == 0
            )

        async with factory() as session:
            replay = await GraphIndexingService(_graph_settings()).index_memory(
                session,
                user_id=user_id,
                memory_id=enabled_memory_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert replay.status == "already_indexed"
            assert dict(replay.timings_ms).keys() >= {
                "memory_load_ms",
                "eligibility_ms",
                "source_entity_resolution_ms",
                "target_entity_resolution_ms",
                "relationship_insert_ms",
                "total_ms",
            }
            await session.rollback()

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.user_id == user_id)
                )
                == 1
            )
            assert _graph_settings().graph_rag_mode == "off"

        disabled_owner_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                User(
                    id=disabled_owner_id,
                    email=f"graph-index-disabled-{disabled_owner_id.hex}@example.invalid",
                    password_hash="graph-index-test-only",
                    memory_enabled=False,
                )
            )
            await session.commit()
        await _write_memory(
            factory,
            disabled_owner_id,
            settings=_graph_settings(),
            candidate=_eligible_candidate(content="Rahul works on Project Disabled Owner."),
        )
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryJob)
                    .where(
                        MemoryJob.user_id == disabled_owner_id,
                        MemoryJob.job_type == "index_memory_graph",
                    )
                )
                == 0
            )
            await session.execute(delete(User).where(User.id == disabled_owner_id))
            await session.commit()

    asyncio.run(run())


def test_second_memory_keeps_separate_evidence_and_reuses_entities(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        settings = _graph_settings()
        first_id, _ = await _write_memory(factory, user_id, settings=settings)
        second_id, created = await _write_memory(
            factory,
            user_id,
            settings=settings,
            candidate=_eligible_candidate(content="Rahul works on Project Alpha this year."),
        )
        assert created and second_id != first_id
        indexer = GraphIndexingService(settings)
        async with factory() as session:
            first = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=first_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            second = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=second_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert first.status == second.status == "indexed"
            await session.commit()
        async with factory() as session:
            edges = (
                await session.scalars(
                    select(EntityRelationship)
                    .where(EntityRelationship.user_id == user_id)
                    .order_by(EntityRelationship.source_memory_id.asc())
                )
            ).all()
            assert [edge.source_memory_id for edge in edges] == sorted([first_id, second_id])
            assert len({edge.source_entity_id for edge in edges}) == 1
            assert len({edge.target_entity_id for edge in edges}) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(Entity.user_id == user_id, Entity.entity_type == "project")
                )
                == 1
            )

    asyncio.run(run())


def test_exact_alias_reuse_and_ambiguous_alias_skip(graph_index_database, graph_user) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        service = GraphService(_graph_settings())
        async with factory() as session:
            target = await service.get_or_create_entity(
                session,
                user_id=user_id,
                entity_type="project",
                canonical_name="Project Alpha",
            )
            await service.insert_alias(
                session,
                user_id=user_id,
                entity_id=target.entity.id,
                alias="Alpha Project",
                source_kind="manual",
            )
            await session.commit()

        alias_memory = await _seed_memory(
            factory,
            user_id=user_id,
            object_json={"name": "Alpha Project", "type": "project"},
        )
        indexer = GraphIndexingService(_graph_settings())
        async with factory() as session:
            result = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=alias_memory,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert result.status == "indexed"
            await session.commit()
        async with factory() as session:
            edge = await session.scalar(
                select(EntityRelationship).where(
                    EntityRelationship.user_id == user_id,
                    EntityRelationship.source_memory_id == alias_memory,
                )
            )
            assert edge is not None and edge.target_entity_id == target.entity.id

        ambiguous_memory = await _seed_memory(
            factory,
            user_id=user_id,
            object_json={"name": "Shared Target", "type": "project"},
        )
        async with factory() as session:
            one = await service.get_or_create_entity(
                session,
                user_id=user_id,
                entity_type="project",
                canonical_name="Project One",
            )
            two = await service.get_or_create_entity(
                session,
                user_id=user_id,
                entity_type="project",
                canonical_name="Project Two",
            )
            await service.insert_alias(
                session,
                user_id=user_id,
                entity_id=one.entity.id,
                alias="Shared Target",
                source_kind="manual",
            )
            await service.insert_alias(
                session,
                user_id=user_id,
                entity_id=two.entity.id,
                alias="Shared Target",
                source_kind="manual",
            )
            await session.commit()
        async with factory() as session:
            ambiguous = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=ambiguous_memory,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert ambiguous.status == "skipped"
            assert ambiguous.reason_code == "ambiguous_target"
            await session.commit()
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == ambiguous_memory)
                )
                == 0
            )

        near_match_memory = await _seed_memory(
            factory,
            user_id=user_id,
            object_json={"name": "Project Alfa", "type": "project"},
        )
        async with factory() as session:
            near_match = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=near_match_memory,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert near_match.status == "indexed"
            await session.commit()
        async with factory() as session:
            near_edge = await session.scalar(
                select(EntityRelationship).where(
                    EntityRelationship.source_memory_id == near_match_memory
                )
            )
            assert near_edge is not None and near_edge.target_entity_id != target.entity.id
            near_target = await session.get(Entity, near_edge.target_entity_id)
            assert near_target is not None and near_target.canonical_name == "Project Alfa"

    asyncio.run(run())


def test_skips_unsupported_scalar_and_non_active_memories(graph_index_database, graph_user) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        indexer = GraphIndexingService(_graph_settings())
        fixtures = [
            (
                await _seed_memory(
                    factory,
                    user_id=user_id,
                    predicate="likes",
                    object_json={"name": "Coffee", "type": "product"},
                ),
                "unsupported_predicate",
            ),
            (
                await _seed_memory(
                    factory,
                    user_id=user_id,
                    memory_type="preference",
                    predicate="preference",
                    object_json={"value": "coffee"},
                ),
                "unsupported_memory_type",
            ),
            (
                await _seed_memory(
                    factory,
                    user_id=user_id,
                    predicate="responsible_for",
                    object_json={"name": "Thing Without Type"},
                ),
                "invalid_entity_type",
            ),
        ]
        for memory_id, expected_reason in fixtures:
            async with factory() as session:
                result = await indexer.index_memory(
                    session,
                    user_id=user_id,
                    memory_id=memory_id,
                    policy_version=GRAPH_INDEX_POLICY_VERSION,
                )
                assert result.status == "skipped"
                assert result.reason_code == expected_reason
                await session.rollback()

        superseded_id = await _seed_memory(factory, user_id=user_id)
        async with factory() as session:
            memory = await session.get(MemoryItem, superseded_id)
            assert memory is not None
            memory.status = "superseded"
            await session.commit()
        async with factory() as session:
            result = await indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=superseded_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert result.status == "skipped"
            assert result.reason_code == "memory_not_active"
            await session.rollback()
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.user_id == user_id)
                )
                == 0
            )

    asyncio.run(run())


def test_concurrent_entity_get_or_create_and_distinct_memory_edges(
    graph_index_database, graph_user
):
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        first = await _seed_memory(factory, user_id=user_id, subject_link=False)
        second = await _seed_memory(factory, user_id=user_id, subject_link=False)
        duplicate = await _seed_memory(
            factory,
            user_id=user_id,
            subject="Asha",
            subject_link=False,
        )
        settings = _graph_settings()

        async def index(memory_id: uuid.UUID):
            async with factory() as session:
                result = await GraphIndexingService(settings).index_memory(
                    session,
                    user_id=user_id,
                    memory_id=memory_id,
                    policy_version=GRAPH_INDEX_POLICY_VERSION,
                )
                await session.commit()
                return result

        duplicate_results = await asyncio.gather(index(duplicate), index(duplicate))
        assert {result.status for result in duplicate_results} == {
            "indexed",
            "already_indexed",
        }
        results = await asyncio.gather(index(first), index(second))
        assert all(result.status == "indexed" for result in results)
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(
                        Entity.user_id == user_id,
                        Entity.entity_type == "person",
                        Entity.normalized_name == "rahul",
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(
                        Entity.user_id == user_id,
                        Entity.entity_type == "project",
                        Entity.normalized_name == "project alpha",
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.user_id == user_id)
                )
                == 3
            )

    asyncio.run(run())


def test_worker_disable_superseded_and_deleted_source_guards(graph_index_database, graph_user):
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        settings = _graph_settings()
        disabled_id, _ = await _write_memory(factory, user_id, settings=settings)
        async with factory() as session:
            owner = await session.get(User, user_id)
            assert owner is not None
            owner.memory_enabled = False
            await session.commit()
        worker = MemoryJobWorker(settings, embedding_provider=_StubEmbeddingProvider())
        async with factory() as session:
            assert await worker.run_once(session) is True
            assert await worker.run_once(session) is True
        async with factory() as session:
            graph_job = await session.scalar(
                select(MemoryJob).where(
                    MemoryJob.user_id == user_id,
                    MemoryJob.job_type == "index_memory_graph",
                )
            )
            assert graph_job is not None and graph_job.status == "completed"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == disabled_id)
                )
                == 0
            )

        # Restore the owner setting to exercise supersession of a queued source.
        async with factory() as session:
            owner = await session.get(User, user_id)
            assert owner is not None
            owner.memory_enabled = True
            await session.commit()
        superseded_id, _ = await _write_memory(
            factory,
            user_id,
            settings=settings,
            candidate=_eligible_candidate(content="Rahul works on Project Beta."),
        )
        async with factory() as session:
            memory = await session.get(MemoryItem, superseded_id)
            assert memory is not None
            memory.status = "superseded"
            await session.commit()
        async with factory() as session:
            assert await worker.run_once(session) is True  # embedding
            assert await worker.run_once(session) is True  # skipped graph job
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == superseded_id)
                )
                == 0
            )

        graph_flag_id, _ = await _write_memory(
            factory,
            user_id,
            settings=settings,
            candidate=_eligible_candidate(content="Rahul works on Project Flag Off."),
        )
        graph_disabled_worker = MemoryJobWorker(
            _graph_settings(enabled=False),
            embedding_provider=_StubEmbeddingProvider(),
        )
        async with factory() as session:
            assert await graph_disabled_worker.run_once(session) is True
            assert await graph_disabled_worker.run_once(session) is True
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == graph_flag_id)
                )
                == 0
            )

        deleted_id, _ = await _write_memory(
            factory,
            user_id,
            settings=settings,
            candidate=_eligible_candidate(content="Rahul works on Project Gamma."),
        )
        async with factory() as session:
            memory = await session.get(MemoryItem, deleted_id)
            assert memory is not None
            await session.delete(memory)
            await session.commit()
        async with factory() as session:
            # Both queued memory jobs cascade with the deleted source row.
            assert await worker.run_once(session) is False
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == deleted_id)
                )
                == 0
            )

    asyncio.run(run())


def test_cross_user_source_is_rejected_and_temporal_confidence_are_preserved(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    owner_id = graph_user
    other_id = uuid.uuid4()

    async def run() -> None:
        async with factory() as session:
            session.add(
                User(
                    id=other_id,
                    email=f"graph-index-other-{other_id.hex}@example.invalid",
                    password_hash="graph-index-test-only",
                )
            )
            await session.commit()
        valid_from = datetime(2026, 1, 5, tzinfo=UTC)
        valid_to = datetime(2026, 12, 31, tzinfo=UTC)
        memory_id = await _seed_memory(
            factory,
            user_id=owner_id,
            confidence=0.92,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        indexer = GraphIndexingService(_graph_settings())
        async with factory() as session:
            rejected = await indexer.index_memory(
                session,
                user_id=other_id,
                memory_id=memory_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert rejected.status == "skipped"
            assert rejected.reason_code == "memory_missing"
            await session.rollback()
        async with factory() as session:
            indexed = await indexer.index_memory(
                session,
                user_id=owner_id,
                memory_id=memory_id,
                policy_version=GRAPH_INDEX_POLICY_VERSION,
            )
            assert indexed.status == "indexed"
            await session.commit()
        async with factory() as session:
            edge = await session.scalar(
                select(EntityRelationship).where(
                    EntityRelationship.user_id == owner_id,
                    EntityRelationship.source_memory_id == memory_id,
                )
            )
            assert edge is not None
            assert edge.confidence == pytest.approx(0.92)
            assert edge.valid_from == valid_from
            assert edge.valid_to == valid_to
            assert edge.user_id != other_id

    asyncio.run(run())


def test_enqueued_job_is_skipped_if_session_is_excluded_before_processing(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        now = datetime.now(UTC)
        device_id = uuid.uuid4()
        auth_session_id = uuid.uuid4()
        voice_session_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                Device(
                    id=device_id,
                    user_id=user_id,
                    device_identifier=f"graph-index-device-{uuid.uuid4().hex}",
                    platform="android",
                )
            )
            await session.flush()
            session.add(
                AuthSession(
                    id=auth_session_id,
                    user_id=user_id,
                    device_id=device_id,
                    refresh_token_hash=uuid.uuid4().hex,
                    created_at=now,
                    last_used_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            await session.flush()
            session.add(
                VoiceSession(
                    id=voice_session_id,
                    user_id=user_id,
                    device_id=device_id,
                    auth_session_id=auth_session_id,
                    protocol_version=1,
                    client_metadata={"memory_excluded": False},
                    status="completed",
                    started_at=now,
                    last_activity_at=now,
                    ended_at=now,
                )
            )
            await session.commit()
        memory_id, created = await _write_memory(
            factory,
            user_id,
            settings=_graph_settings(),
            candidate=_eligible_candidate(content="Rahul works on Project Delta."),
            source_session_id=voice_session_id,
        )
        # Change the policy after enqueue to exercise the worker-side guard.
        async with factory() as session:
            memory = await session.get(MemoryItem, memory_id)
            assert memory is not None and created
            assert memory.source_session_id == voice_session_id
            voice_session = await session.get(VoiceSession, voice_session_id)
            assert voice_session is not None
            voice_session.client_metadata = {"memory_excluded": True}
            await session.commit()

        excluded_memory_id, _ = await _write_memory(
            factory,
            user_id,
            settings=_graph_settings(),
            candidate=_eligible_candidate(content="Rahul works on Project Epsilon."),
            source_session_id=voice_session_id,
        )
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryJob)
                    .where(
                        MemoryJob.user_id == user_id,
                        MemoryJob.job_type == "index_memory_graph",
                    )
                )
                == 1
            )
            assert await session.get(MemoryItem, excluded_memory_id) is not None
        worker = MemoryJobWorker(_graph_settings(), embedding_provider=_StubEmbeddingProvider())
        async with factory() as session:
            assert await worker.run_once(session) is True
            assert await worker.run_once(session) is True
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EntityRelationship)
                    .where(EntityRelationship.source_memory_id == memory_id)
                )
                == 0
            )

    asyncio.run(run())


def test_memory_supersession_marks_only_its_graph_evidence_superseded(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        settings = _graph_settings()
        writer = MemoryWriter(settings)
        old_candidate = ExtractionCandidate(
            content="I prefer green tea.",
            memory_type=MemoryType.PREFERENCE,
            subject="user",
            predicate="beverage",
            object_json={"value": "green tea"},
            confidence=0.95,
            salience=0.9,
        )
        async with factory() as session:
            old_memory, created = await writer.write_candidate(
                session,
                user_id=user_id,
                candidate=old_candidate,
                source_kind=MemorySourceKind.MANUAL_API,
            )
            assert created
            source_link = await session.scalar(
                select(MemoryEntity).where(
                    MemoryEntity.memory_id == old_memory.id,
                    MemoryEntity.user_id == user_id,
                )
            )
            assert source_link is not None
            target = await GraphService(settings).get_or_create_entity(
                session,
                user_id=user_id,
                entity_type="product",
                canonical_name="Green Tea",
            )
            edge = await GraphService(settings).insert_relationship(
                session,
                user_id=user_id,
                source_entity_id=source_link.entity_id,
                relationship_type="PREFERS",
                target_entity_id=target.entity.id,
                source_memory_id=old_memory.id,
                confidence=old_memory.confidence,
            )
            assert edge.created
            await session.commit()
            old_memory_id = old_memory.id
            edge_id = edge.edge.relationship_id

        new_candidate = ExtractionCandidate(
            content="I prefer black tea.",
            memory_type=MemoryType.PREFERENCE,
            subject="user",
            predicate="beverage",
            object_json={"value": "black tea"},
            confidence=0.96,
            salience=0.9,
        )
        async with factory() as session:
            new_memory, created = await writer.write_candidate(
                session,
                user_id=user_id,
                candidate=new_candidate,
                source_kind=MemorySourceKind.MANUAL_API,
            )
            assert created
            await session.commit()
            new_memory_id = new_memory.id

        async with factory() as session:
            old_memory = await session.get(MemoryItem, old_memory_id)
            old_edge = await session.get(EntityRelationship, edge_id)
            new_memory = await session.get(MemoryItem, new_memory_id)
            assert old_memory is not None and old_memory.status == "superseded"
            assert old_edge is not None and old_edge.status == "superseded"
            assert new_memory is not None and new_memory.status == "active"

    asyncio.run(run())


def test_background_indexing_microbenchmark(graph_index_database, graph_user) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> list[float]:
        settings = _graph_settings()
        memory_ids = []
        for index in range(30):
            memory_ids.append(
                await _seed_memory(
                    factory,
                    user_id=user_id,
                    subject=f"Person {index}",
                    object_json={"name": "Shared Project", "type": "project"},
                    subject_link=False,
                )
            )
        timings = []
        service = GraphIndexingService(settings)
        for memory_id in memory_ids:
            async with factory() as session:
                result = await service.index_memory(
                    session,
                    user_id=user_id,
                    memory_id=memory_id,
                    policy_version=GRAPH_INDEX_POLICY_VERSION,
                )
                timings.append(dict(result.timings_ms)["total_ms"])
                await session.commit()
        return timings

    timings = asyncio.run(run())
    ordered = sorted(timings)
    p50 = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    print(
        "BACKGROUND_GRAPH_INDEX_MICROBENCHMARK "
        f"iterations={len(timings)} min_ms={min(timings):.3f} "
        f"mean_ms={sum(timings) / len(timings):.3f} p50_ms={p50:.3f} "
        f"p95_ms={p95:.3f} max_ms={max(timings):.3f}"
    )
    assert len(timings) == 30


def test_orphan_entity_cleanup_is_bounded_idempotent_and_alias_aware(
    graph_index_database, graph_user
) -> None:
    factory = graph_index_database
    user_id = graph_user

    async def run() -> None:
        orphan_id = uuid.uuid4()
        retained_id = uuid.uuid4()
        async with factory() as session:
            session.add_all(
                [
                    Entity(
                        id=orphan_id,
                        user_id=user_id,
                        entity_type="other",
                        canonical_name="Cleanup Orphan",
                        normalized_name="cleanup orphan",
                    ),
                    Entity(
                        id=retained_id,
                        user_id=user_id,
                        entity_type="other",
                        canonical_name="Retained Alias",
                        normalized_name="retained alias",
                    ),
                ]
            )
            await session.flush()
            session.add(
                EntityAlias(
                    user_id=user_id,
                    entity_id=retained_id,
                    alias="Retained Alias",
                    normalized_alias="retained alias",
                    source_kind="manual",
                )
            )
            await session.commit()

        async with factory() as session:
            removed = await GraphRepository().cleanup_orphaned_entities(
                session,
                user_id=user_id,
                limit=1,
            )
            await session.commit()
            assert removed == 1
            assert await session.get(Entity, orphan_id) is None
            assert await session.get(Entity, retained_id) is not None

        async with factory() as session:
            assert (
                await GraphRepository().cleanup_orphaned_entities(session, user_id=user_id)
            ) == 0
            await session.rollback()

    asyncio.run(run())
