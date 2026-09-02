from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
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

    # Phase 4 STT engine settings. Windows Speech is the active default;
    # Whisper settings remain available only for the explicit legacy adapter.
    stt_engine: Literal["windows", "whisper"] = "windows"
    stt_windows_worker_path: str = "backend/windows_stt/publish/WindowsSttWorker.exe"
    stt_dotnet_path: str | None = None
    stt_windows_language: str = "en-US"
    stt_start_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    stt_final_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    stt_worker_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    stt_worker_max_line_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    # One-turn physical audio diagnostic. Disabled by default so ordinary
    # backend sessions never write microphone data to disk.
    stt_diagnostic_capture_enabled: bool = False
    stt_diagnostic_capture_dir: str = "scratch/phase4_stt_diagnostics"

    # Legacy local CPU Whisper settings. They are not loaded by the default
    # Windows engine and can be removed after the migration is accepted.
    stt_model_path: str = "models/whisper-large-v3-turbo-ct2"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str | None = None
    stt_beam_size: int = Field(default=1, ge=1, le=20)
    stt_threads: int = Field(default=4, ge=1, le=16)
    stt_workers: int = Field(default=2, ge=1, le=8)
    # CPU Large-v3-Turbo inference can take longer than the audio capture
    # window on modest machines; this remains a bounded per-inference timeout.
    stt_timeout: float = Field(default=180.0, gt=0, le=300)
    stt_partial_interval_seconds: float = Field(default=1.5, gt=0, le=30)
    stt_partial_window_seconds: int = Field(default=30, ge=1, le=120)
    stt_max_audio_seconds: int = Field(default=120, ge=1, le=600)
    stt_max_active_turns: int = Field(default=4, ge=1, le=32)

    voice_protocol_version: int = 1
    voice_sample_rate_hz: int = 16_000
    voice_channels: int = 1
    voice_frame_samples: int = 320
    voice_frame_bytes: int = 640
    voice_heartbeat_interval_seconds: int = 15
    voice_heartbeat_timeout_seconds: int = 45
    voice_max_session_seconds: int = 1_800
    voice_max_turn_seconds: int = 120
    voice_idle_timeout_seconds: int = 90
    voice_max_frame_bytes: int = 4_096
    voice_max_control_bytes: int = 16 * 1024
    voice_queue_capacity_frames: int = 100
    voice_reconnect_grace_seconds: int = 30

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
    def stt_model_dir(self) -> Path:
        """Return the configured STT model path resolved against the project root."""

        path = Path(self.stt_model_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def stt_windows_worker_path_resolved(self) -> Path:
        """Return the configured Windows worker executable path."""

        path = Path(self.stt_windows_worker_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def stt_dotnet_path_resolved(self) -> Path | None:
        """Return an optional explicitly configured dotnet host path."""

        if not self.stt_dotnet_path:
            return None
        path = Path(self.stt_dotnet_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def stt_diagnostic_capture_dir_resolved(self) -> Path:
        """Return the bounded physical-diagnostic output directory."""

        path = Path(self.stt_diagnostic_capture_dir).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def jwt_secret(self) -> str:
        if not self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            raise RuntimeError("JWT_SECRET_KEY must contain at least 32 characters")
        return self.jwt_secret_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
