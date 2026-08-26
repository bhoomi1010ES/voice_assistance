from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


@dataclass
class Database:
    settings: Settings
    engine: AsyncEngine | None = field(init=False, default=None)
    session_factory: async_sessionmaker[AsyncSession] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if not self.settings.database_url:
            return
        self.engine = create_async_engine(
            self.settings.database_dsn,
            pool_pre_ping=True,
            future=True,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        if self.engine is None:
            raise RuntimeError("DATABASE_URL is not configured")
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
