from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import MemoryJob, User

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_OLD_TYPES = ("extract_turn", "embed_memory", "reembed_memory", "purge_session")
_NEW_TYPES = (*_OLD_TYPES, "index_memory_graph")


def test_graph_index_job_migration_upgrade_preserves_rows_and_downgrades_safely() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL migration checks.")
    if os.getenv("RUN_GRAPH_MIGRATION_TESTS") != "1":
        pytest.skip("Set RUN_GRAPH_MIGRATION_TESTS=1 to create a disposable migration DB.")

    source_url = make_url(Settings().database_dsn)
    database_name = f"phase6a_graph_job_{uuid.uuid4().hex[:12]}"
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

    async def seed_existing_jobs() -> uuid.UUID:
        engine = create_async_engine(test_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"graph-job-migration-{user_id.hex}@example.invalid",
                    password_hash="migration-fixture-only",
                    memory_enabled=True,
                )
            )
            await session.flush()
            for job_type in _OLD_TYPES:
                session.add(
                    MemoryJob(
                        user_id=user_id,
                        job_type=job_type,
                        idempotency_key=f"migration-fixture:{job_type}",
                        status="pending",
                        attempts=0,
                        available_at=datetime.now(UTC),
                        policy_version="phase6-explicit-v1",
                    )
                )
            await session.commit()
        await engine.dispose()
        return user_id

    async def snapshot() -> dict[str, object]:
        engine = create_async_engine(test_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.scalars(select(MemoryJob).order_by(MemoryJob.id.asc()))).all()
            serialized = []
            for row in rows:
                values = {}
                for column in MemoryJob.__table__.columns:
                    value = getattr(row, column.key)
                    if isinstance(value, (uuid.UUID, datetime)):
                        value = value.isoformat() if isinstance(value, datetime) else str(value)
                    values[column.key] = value
                serialized.append(values)
            counts = Counter(row.job_type for row in rows)
            fingerprint = hashlib.sha256(
                json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        await engine.dispose()
        return {
            "total": len(serialized),
            "counts": dict(sorted(counts.items())),
            "fingerprint": fingerprint,
        }

    async def assert_job_types(user_id: uuid.UUID, allowed: tuple[str, ...]) -> None:
        engine = create_async_engine(test_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.begin()
            try:
                for job_type in allowed:
                    async with session.begin_nested():
                        session.add(
                            MemoryJob(
                                user_id=user_id,
                                job_type=job_type,
                                idempotency_key=f"constraint-check:{uuid.uuid4()}",
                                status="pending",
                                attempts=0,
                                available_at=datetime.now(UTC),
                                policy_version="migration-test",
                            )
                        )
                        await session.flush()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            MemoryJob(
                                user_id=user_id,
                                job_type=(
                                    "index_memory_graph"
                                    if "index_memory_graph" not in allowed
                                    else "invalid_graph_job"
                                ),
                                idempotency_key=f"constraint-reject:{uuid.uuid4()}",
                                status="pending",
                                attempts=0,
                                available_at=datetime.now(UTC),
                                policy_version="migration-test",
                            )
                        )
                        await session.flush()
            finally:
                await session.rollback()
        await engine.dispose()

    try:
        run_alembic("upgrade", "0012_graph_rag_foundation")
        before_current = run_alembic("current")
        assert "0012_graph_rag_foundation" in before_current.stdout
        user_id = asyncio.run(seed_existing_jobs())
        before = asyncio.run(snapshot())

        run_alembic("upgrade", "0013_graph_index_job_type")
        after = asyncio.run(snapshot())
        assert after == before
        asyncio.run(assert_job_types(user_id, _NEW_TYPES))
        after_current = run_alembic("current")
        assert "0013_graph_index_job_type" in after_current.stdout

        graph_rows = asyncio.run(_count_graph_jobs(test_url))
        assert graph_rows == 0
        run_alembic("downgrade", "0012_graph_rag_foundation")
        downgraded = asyncio.run(snapshot())
        assert downgraded == before
        asyncio.run(assert_job_types(user_id, _OLD_TYPES))
        downgraded_current = run_alembic("current")
        assert "0012_graph_rag_foundation" in downgraded_current.stdout
    finally:
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
                                "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                            ),
                            {"database_name": database_name},
                        )
                        await connection.exec_driver_sql(
                            f'DROP DATABASE IF EXISTS "{database_name}"'
                        )
                finally:
                    await drop_engine.dispose()

            asyncio.run(drop_database())


async def _count_graph_jobs(url) -> int:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return int(
                await connection.scalar(
                    text("SELECT count(*) FROM memory_jobs WHERE job_type='index_memory_graph'")
                )
                or 0
            )
    finally:
        await engine.dispose()
