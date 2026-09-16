from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import MemoryItem, User

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_owner_scoped_supersession_migration_preserves_rows_and_rejects_cross_owner() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL migration checks.")
    if os.getenv("RUN_GRAPH_MIGRATION_TESTS") != "1":
        pytest.skip("Set RUN_GRAPH_MIGRATION_TESTS=1 to create a disposable migration DB.")

    source_url = make_url(Settings().database_dsn)
    database_name = f"phase6b_memory_fk_{uuid.uuid4().hex[:12]}"
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

    environment = os.environ.copy()
    environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=_BACKEND_ROOT,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"Alembic {' '.join(arguments)} failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    async def seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        engine = create_async_engine(test_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = uuid.uuid4()
        other_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        async with factory() as session:
            session.add_all(
                [
                    User(
                        id=owner_id,
                        email=f"phase6b-owner-{owner_id.hex}@example.invalid",
                        password_hash="migration-fixture-only",
                    ),
                    User(
                        id=other_id,
                        email=f"phase6b-other-{other_id.hex}@example.invalid",
                        password_hash="migration-fixture-only",
                    ),
                ]
            )
            await session.flush()
            session.add(
                MemoryItem(
                    id=parent_id,
                    user_id=owner_id,
                    content="original owner memory",
                    memory_type="fact",
                    source_kind="manual_api",
                    status="active",
                )
            )
            await session.flush()
            session.add(
                MemoryItem(
                    id=child_id,
                    user_id=owner_id,
                    content="superseding owner memory",
                    memory_type="fact",
                    source_kind="manual_api",
                    supersedes_id=parent_id,
                    status="active",
                )
            )
            await session.commit()
        await engine.dispose()
        return owner_id, other_id, parent_id

    async def snapshot() -> str:
        engine = create_async_engine(test_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            rows = (await session.scalars(select(MemoryItem).order_by(MemoryItem.id))).all()
            values = [
                {
                    "id": str(row.id),
                    "user_id": str(row.user_id),
                    "content": row.content,
                    "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
                    "status": row.status,
                }
                for row in rows
            ]
        await engine.dispose()
        return hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    try:
        run_alembic("upgrade", "0013_graph_index_job_type")
        owner_id, other_id, parent_id = asyncio.run(seed())
        before = asyncio.run(snapshot())

        run_alembic("upgrade", "0014_memory_supersession_owner")
        assert asyncio.run(snapshot()) == before

        async def assert_composite_fk() -> None:
            engine = create_async_engine(test_url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                valid = MemoryItem(
                    user_id=owner_id,
                    content="valid same-owner reference",
                    memory_type="fact",
                    source_kind="manual_api",
                    supersedes_id=parent_id,
                    status="active",
                )
                session.add(valid)
                await session.flush()
                await session.rollback()
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            MemoryItem(
                                user_id=other_id,
                                content="cross-owner reference must fail",
                                memory_type="fact",
                                source_kind="manual_api",
                                supersedes_id=parent_id,
                                status="active",
                            )
                        )
                        await session.flush()
            await engine.dispose()

        asyncio.run(assert_composite_fk())
        run_alembic("downgrade", "0013_graph_index_job_type")
        assert "0013_graph_index_job_type" in run_alembic("current").stdout
    finally:
        if created:

            async def drop_database() -> None:
                engine = create_async_engine(source_url)
                try:
                    async with engine.connect() as connection:
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
                    await engine.dispose()

            asyncio.run(drop_database())
