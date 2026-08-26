import os

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services.infrastructure import Infrastructure

pytestmark = pytest.mark.integration


@pytest.fixture
async def infrastructure():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run infrastructure checks.")
    instance = Infrastructure(Settings())
    readiness = await instance.check_readiness()
    if readiness["status"] != "ready":
        await instance.close()
        pytest.skip(f"Infrastructure unavailable: {readiness['dependencies']}")
    yield instance
    await instance.close()


async def test_postgres_and_pgvector_are_available(infrastructure: Infrastructure) -> None:
    assert infrastructure.database.engine is not None
    async with infrastructure.database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
        assert (
            await connection.scalar(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
            == "vector"
        )


async def test_redis_is_available(infrastructure: Infrastructure) -> None:
    assert infrastructure.redis is not None
    assert await infrastructure.redis.ping()
