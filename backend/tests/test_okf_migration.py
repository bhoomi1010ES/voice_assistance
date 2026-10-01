from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import (
    Entity,
    EntityAlias,
    EntityRelationship,
    MemoryChunk,
    MemoryEntity,
    MemoryItem,
    Reminder,
    Task,
    ToolExecutionRecord,
    User,
)

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_OKF_TABLES = {
    "okf_concepts",
    "okf_concept_assertions",
    "okf_concept_versions",
    "okf_concept_sources",
    "okf_sync_jobs",
}
_LEGACY_MODELS = (
    MemoryItem,
    MemoryChunk,
    Entity,
    MemoryEntity,
    EntityAlias,
    EntityRelationship,
    Task,
    Reminder,
    ToolExecutionRecord,
)


def test_okf_migration_is_additive_reversible_and_preserves_existing_rows() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL migration checks.")
    if os.getenv("RUN_OKF_MIGRATION_TESTS") != "1":
        pytest.skip("Set RUN_OKF_MIGRATION_TESTS=1 to create a disposable migration DB.")

    source_url = make_url(Settings().database_dsn)
    database_name = f"okf_schema_{uuid.uuid4().hex[:12]}"
    test_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(source_url)
    created = False

    async def create_database() -> bool:
        async with admin_engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            allowed = await connection.scalar(
                text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
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

    environment = os.environ.copy()
    environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)

    def run_alembic(
        *arguments: str, expect_success: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=_BACKEND_ROOT,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=180,
        )
        if expect_success and result.returncode != 0:
            raise AssertionError(
                f"Alembic {' '.join(arguments)} failed ({result.returncode}):\n{result.stderr}"
            )
        if not expect_success and result.returncode == 0:
            raise AssertionError(f"Alembic {' '.join(arguments)} unexpectedly succeeded.")
        return result

    try:
        # Empty database proof: upgrade all the way, then remove only OKF.
        run_alembic("upgrade", "head")
        asyncio.run(_assert_okf_schema(test_url))
        run_alembic("downgrade", "0016_one_self_entity_per_owner")
        asyncio.run(_assert_okf_tables_absent(test_url))

        # Existing-data proof: seed using the current model at head, then
        # downgrade the additive OKF schema and prove re-upgrade preserves it.
        run_alembic("upgrade", "head")
        owner_id, memory_id = asyncio.run(_seed_existing_rows(test_url))
        before = asyncio.run(_legacy_snapshot(test_url))
        before_search = asyncio.run(_memory_search_snapshot(test_url, memory_id))

        run_alembic("downgrade", "0016_one_self_entity_per_owner")
        asyncio.run(_assert_okf_tables_absent(test_url))
        run_alembic("upgrade", "head")
        asyncio.run(_assert_okf_schema(test_url))
        after = asyncio.run(_legacy_snapshot(test_url))
        after_search = asyncio.run(_memory_search_snapshot(test_url, memory_id))
        assert after == before
        assert after_search == before_search
        assert after_search["memory_fts"]
        assert after_search["chunk_fts"]
        assert after_search["embedding"].startswith("[")
        assert asyncio.run(_count_okf_rows(test_url)) == 0

        test_owner_id = asyncio.run(_exercise_okf_constraints(test_url, owner_id, memory_id))
        rejected = run_alembic("downgrade", "0016_one_self_entity_per_owner", expect_success=False)
        assert "cannot downgrade OKF schema while live OKF rows exist" in (
            rejected.stdout + rejected.stderr
        )

        asyncio.run(_remove_constraint_fixtures(test_url, owner_id, test_owner_id))
        assert asyncio.run(_count_okf_rows(test_url)) == 0
        run_alembic("downgrade", "0016_one_self_entity_per_owner")
        asyncio.run(_assert_okf_tables_absent(test_url))
        assert asyncio.run(_legacy_snapshot(test_url)) == before
        assert asyncio.run(_memory_search_snapshot(test_url, memory_id)) == before_search
    finally:
        if created:
            asyncio.run(_drop_database(source_url, database_name))


async def _seed_existing_rows(url: URL) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    user_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    source_entity_id = uuid.uuid4()
    target_entity_id = uuid.uuid4()
    task_id = uuid.uuid4()
    try:
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"okf-migration-{user_id.hex}@example.invalid",
                    password_hash="migration-fixture-only",
                    memory_enabled=True,
                )
            )
            await session.flush()
            session.add(
                MemoryItem(
                    id=memory_id,
                    user_id=user_id,
                    content="The migration fixture uses PostgreSQL for its project database.",
                    metadata_json={"fixture": "okf-1"},
                    memory_type="project",
                    subject="migration-fixture",
                    predicate="database",
                    object_json={"value": "PostgreSQL"},
                    confidence=0.95,
                    salience=0.75,
                    source_kind="manual_api",
                    dedupe_key="okf-1-existing-memory",
                    extraction_policy_version="phase6-explicit-v1",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                MemoryChunk(
                    id=chunk_id,
                    memory_id=memory_id,
                    user_id=user_id,
                    chunk_no=0,
                    content="PostgreSQL project database migration fixture",
                    token_count=5,
                    embedding=[0.001] * 1024,
                    embedding_model="BAAI/bge-m3",
                    embedded_at=now,
                    created_at=now,
                )
            )
            session.add_all(
                [
                    Entity(
                        id=source_entity_id,
                        user_id=user_id,
                        entity_type="project",
                        canonical_name="Migration fixture",
                        normalized_name="migration fixture",
                        metadata_json={"fixture": True},
                        created_at=now,
                    ),
                    Entity(
                        id=target_entity_id,
                        user_id=user_id,
                        entity_type="product",
                        canonical_name="PostgreSQL",
                        normalized_name="postgresql",
                        metadata_json={"fixture": True},
                        created_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add(
                MemoryEntity(
                    memory_id=memory_id,
                    entity_id=source_entity_id,
                    user_id=user_id,
                    relation="subject",
                )
            )
            session.add(
                EntityAlias(
                    user_id=user_id,
                    entity_id=target_entity_id,
                    alias="Postgres",
                    normalized_alias="postgres",
                    source_memory_id=memory_id,
                    source_kind="memory",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                EntityRelationship(
                    user_id=user_id,
                    source_entity_id=source_entity_id,
                    relationship_type="DEPENDS_ON",
                    target_entity_id=target_entity_id,
                    source_memory_id=memory_id,
                    confidence=0.9,
                    status="active",
                    extraction_policy_version="graph-v1",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                Task(
                    id=task_id,
                    user_id=user_id,
                    title="Preserve migration fixtures",
                    status="pending",
                    priority="high",
                    timezone="UTC",
                    timezone_source="device",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                Reminder(
                    user_id=user_id,
                    task_id=task_id,
                    title="Check migration preservation",
                    trigger_at=now + timedelta(days=1),
                    timezone="UTC",
                    timezone_source="device",
                    status="scheduled",
                    delivery_channel="push",
                    delivery_id=f"okf-migration-{uuid.uuid4()}",
                    created_at=now,
                    updated_at=now,
                )
            )
            # Pending confirmation state is Redis-backed, so the durable SQL
            # confirmation artifact is its tool execution/idempotency record.
            session.add(
                ToolExecutionRecord(
                    user_id=user_id,
                    turn_id=uuid.uuid4(),
                    tool_name="create_task",
                    tool_call_id="okf-migration-confirmation",
                    status="started",
                    arguments_json={"confirmation_id": str(uuid.uuid4())},
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()
    return user_id, memory_id


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def _legacy_snapshot(url: URL) -> dict[str, dict[str, Any]]:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    snapshot: dict[str, dict[str, Any]] = {}
    try:
        async with factory() as session:
            for model in _LEGACY_MODELS:
                primary_key = list(model.__table__.primary_key.columns)
                rows = (await session.scalars(select(model).order_by(*primary_key))).all()
                values = [
                    {
                        column.key: _json_safe(getattr(row, column.key))
                        for column in model.__table__.columns
                    }
                    for row in rows
                ]
                payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
                snapshot[model.__tablename__] = {
                    "count": len(values),
                    "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                }
    finally:
        await engine.dispose()
    return snapshot


async def _memory_search_snapshot(url: URL, memory_id: uuid.UUID) -> dict[str, str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT m.search_tsv::text AS memory_fts, "
                        "c.search_tsv::text AS chunk_fts, c.embedding::text AS embedding "
                        "FROM memory_items m JOIN memory_chunks c "
                        "ON c.memory_id=m.id AND c.user_id=m.user_id WHERE m.id=:memory_id"
                    ),
                    {"memory_id": memory_id},
                )
            ).one()
            return dict(row._mapping)
    finally:
        await engine.dispose()


async def _assert_okf_schema(url: URL) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name LIKE 'okf_%'"
                        )
                    )
                ).all()
            )
            assert tables == _OKF_TABLES

            current_fk = (
                await connection.execute(
                    text(
                        "SELECT condeferrable, condeferred FROM pg_constraint "
                        "WHERE conname='fk_okf_assertions_current_version'"
                    )
                )
            ).one()
            assert current_fk == (True, True)

            parent_definition = await connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname='fk_okf_concepts_parent_user'"
                )
            )
            assert "ON DELETE SET NULL (parent_concept_id)" in parent_definition

            job_memory_fks = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid=c.conrelid "
                    "WHERE t.relname='okf_sync_jobs' AND c.contype='f' "
                    "AND pg_get_constraintdef(c.oid) LIKE '%memory_id%'"
                )
            )
            assert job_memory_fks == 0
    finally:
        await engine.dispose()


async def _assert_okf_tables_absent(url: URL) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name LIKE 'okf_%'"
                )
            )
            assert count == 0
    finally:
        await engine.dispose()


async def _count_okf_rows(url: URL) -> int:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            total = 0
            for table_name in _OKF_TABLES:
                total += int(
                    await connection.scalar(
                        text(f'SELECT count(*) FROM "{table_name}"')  # noqa: S608
                    )
                    or 0
                )
            return total
    finally:
        await engine.dispose()


async def _exercise_okf_constraints(
    url: URL, existing_owner_id: uuid.UUID, existing_memory_id: uuid.UUID
) -> uuid.UUID:
    engine = create_async_engine(url)
    test_owner_id = uuid.uuid4()
    test_memory_id = uuid.uuid4()
    existing_owner_concept_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    assertion_id = uuid.uuid4()
    version_id = uuid.uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,password_hash,status,memory_enabled,timezone,locale,memory_version,"
                    "created_at,updated_at) VALUES "
                    "(:id,:email,'fixture','active',true,'UTC','en',0,:now,:now)"
                ),
                {
                    "id": test_owner_id,
                    "email": f"okf-constraint-{test_owner_id.hex}@example.invalid",
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO memory_items "
                    "(id,user_id,content,metadata,memory_type,confidence,salience,source_kind,"
                    "status,created_at,updated_at) VALUES "
                    "(:id,:user_id,'constraint source','{}'::jsonb,'fact',1,1,'manual_api',"
                    "'active',:now,:now)"
                ),
                {"id": test_memory_id, "user_id": test_owner_id, "now": now},
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_concepts "
                    "(id,user_id,concept_type,canonical_key,title,status,policy_version) VALUES "
                    "(:id,:user_id,'fact','facts/existing-owner','Existing owner','active',"
                    "'okf-v1')"
                ),
                {"id": existing_owner_concept_id, "user_id": existing_owner_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_concepts "
                    "(id,user_id,concept_type,canonical_key,title,status,policy_version) VALUES "
                    "(:parent,:user_id,'project','projects/schema-proof','Schema proof','active',"
                    "'okf-v1'), "
                    "(:child,:user_id,'decision','projects/schema-proof/database','Database',"
                    "'active','okf-v1')"
                ),
                {"parent": parent_id, "child": child_id, "user_id": test_owner_id},
            )
            await connection.execute(
                text("UPDATE okf_concepts SET parent_concept_id=:parent WHERE id=:child"),
                {"parent": parent_id, "child": child_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_concept_assertions "
                    "(id,user_id,concept_id,value_json,display_text,status,current_version,"
                    "confidence,policy_version) VALUES "
                    '(:id,:user_id,:concept_id,\'{"value":"PostgreSQL"}\'::jsonb,'
                    "'PostgreSQL','active',1,1,'okf-v1')"
                ),
                {"id": assertion_id, "user_id": test_owner_id, "concept_id": child_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_concept_versions "
                    "(id,user_id,concept_id,assertion_id,version,value_json,display_text,status,"
                    "confidence,change_kind,policy_version) VALUES "
                    "(:id,:user_id,:concept_id,:assertion_id,1,"
                    "'{\"value\":\"PostgreSQL\"}'::jsonb,'PostgreSQL','active',1,'create',"
                    "'okf-v1')"
                ),
                {
                    "id": version_id,
                    "user_id": test_owner_id,
                    "concept_id": child_id,
                    "assertion_id": assertion_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_concept_sources "
                    "(user_id,concept_version_id,memory_id,evidence_role) "
                    "VALUES (:user_id,:version_id,:memory_id,'supports')"
                ),
                {
                    "user_id": test_owner_id,
                    "version_id": version_id,
                    "memory_id": test_memory_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO okf_sync_jobs "
                    "(id,user_id,memory_id,event_type,idempotency_key,status,attempts,"
                    "policy_version) VALUES "
                    "(:id,:user_id,:missing_memory,'remove_memory','remove:missing','pending',0,"
                    "'okf-v1')"
                ),
                {
                    "id": uuid.uuid4(),
                    "user_id": test_owner_id,
                    "missing_memory": uuid.uuid4(),
                },
            )

        # Cross-owner parent and source references must fail at the database.
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO okf_concepts "
                        "(id,user_id,parent_concept_id,concept_type,canonical_key,title,status,"
                        "policy_version) VALUES "
                        "(:id,:user_id,:parent,'fact','facts/cross-owner','Cross owner','active',"
                        "'okf-v1')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "user_id": test_owner_id,
                        "parent": existing_owner_concept_id,
                    },
                )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO okf_concept_sources "
                        "(user_id,concept_version_id,memory_id,evidence_role) "
                        "VALUES (:user_id,:version_id,:memory_id,'supports')"
                    ),
                    {
                        "user_id": test_owner_id,
                        "version_id": version_id,
                        "memory_id": existing_memory_id,
                    },
                )

        # The deferred FK must reject an assertion whose selected version does
        # not exist when the transaction reaches commit.
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO okf_concept_assertions "
                        "(id,user_id,concept_id,value_json,display_text,status,current_version,"
                        "confidence,policy_version) VALUES "
                        "(:id,:user_id,:concept_id,'{}'::jsonb,'Missing version','active',99,1,"
                        "'okf-v1')"
                    ),
                    {"id": uuid.uuid4(), "user_id": test_owner_id, "concept_id": child_id},
                )

        # Deleting a parent detaches only the parent ID and retains ownership.
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM okf_concepts WHERE id=:parent"), {"parent": parent_id}
            )
            child = (
                await connection.execute(
                    text("SELECT parent_concept_id,user_id FROM okf_concepts WHERE id=:child"),
                    {"child": child_id},
                )
            ).one()
            assert child.parent_concept_id is None
            assert child.user_id == test_owner_id
    finally:
        await engine.dispose()
    return test_owner_id


async def _remove_constraint_fixtures(
    url: URL, existing_owner_id: uuid.UUID, test_owner_id: uuid.UUID
) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM okf_concepts WHERE user_id=:user_id"),
                {"user_id": existing_owner_id},
            )
            await connection.execute(
                text("DELETE FROM users WHERE id=:user_id"), {"user_id": test_owner_id}
            )
    finally:
        await engine.dispose()


async def _drop_database(source_url: URL, database_name: str) -> None:
    engine = create_async_engine(source_url)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await engine.dispose()
