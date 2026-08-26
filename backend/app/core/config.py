from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables and ``.env``."""

    app_name: str = "Voice Assistance Backend"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    database_url: str | None = None
    redis_url: str | None = None
    jwt_secret_key: str | None = None
    jwt_issuer: str = "voice-assistance"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def database_dsn(self) -> str:
        """Return the configured async SQLAlchemy URL."""

        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        parsed = urlsplit(self.database_url)
        if parsed.scheme in {"postgres", "postgresql"}:
            return urlunsplit(
                ("postgresql+asyncpg", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
            )
        return self.database_url

    @property
    def redis_dsn(self) -> str:
        """Return the Redis URL used by the async client."""

        if not self.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        return self.redis_url

    @property
    def jwt_secret(self) -> str:
        if not self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            raise RuntimeError("JWT_SECRET_KEY must contain at least 32 characters")
        return self.jwt_secret_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
