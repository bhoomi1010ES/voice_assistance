from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis, from_url

from app.core.config import Settings
from app.db.session import Database


@dataclass
class Infrastructure:
    """Own PostgreSQL and Redis clients for the application lifecycle."""

    settings: Settings
    database: Database = field(init=False)
    redis: Redis | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.database = Database(self.settings)
        if self.settings.redis_url:
            self.redis = from_url(self.settings.redis_dsn, decode_responses=True)

    async def check_postgres(self) -> dict[str, str]:
        if not self.settings.database_url:
            return {"status": "error", "error": "DATABASE_URL_NOT_CONFIGURED"}
        try:
            await self.database.ping()
        except Exception as error:  # noqa: BLE001 - readiness must remain available during outages
            return {"status": "error", "error": type(error).__name__}
        return {"status": "ok"}

    async def check_redis(self) -> dict[str, str]:
        if self.redis is None:
            return {"status": "error", "error": "REDIS_URL_NOT_CONFIGURED"}
        try:
            await self.redis.ping()
        except Exception as error:  # noqa: BLE001 - readiness must remain available during outages
            return {"status": "error", "error": type(error).__name__}
        return {"status": "ok"}

    async def check_readiness(self) -> dict[str, Any]:
        postgres_status, redis_status = await self._check_dependencies()
        ready = postgres_status["status"] == "ok" and redis_status["status"] == "ok"
        return {
            "status": "ready" if ready else "not_ready",
            "dependencies": {
                "postgres": postgres_status,
                "redis": redis_status,
            },
        }

    async def _check_dependencies(self) -> tuple[dict[str, str], dict[str, str]]:
        return await self.check_postgres(), await self.check_redis()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
        await self.database.dispose()
