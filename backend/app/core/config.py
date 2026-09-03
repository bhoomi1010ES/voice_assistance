from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, model_validator
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

    # Phase 4 uses the remote transcription adapter. Windows remains importable
    # only for legacy diagnostics and is rejected by the production selector.
    stt_engine: Literal["windows", "remote", "whisper"] = "remote"
    stt_api_url: str | None = None
    stt_api_key: SecretStr | None = None
    stt_api_auth_header: str = "Authorization"
    stt_api_auth_scheme: str = "Bearer"
    stt_api_model: str | None = None
    stt_api_file_field: str = "file"
    stt_api_filename: str = "audio.wav"
    stt_api_response_format: str = "json"
    stt_api_language: str | None = "en"
    stt_api_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    stt_api_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    stt_api_max_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
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

    # Phase 5 remains disabled until all four primary settings are supplied.
    # This preserves the Phase 4-only runtime while making partial LLM
    # configuration a startup error instead of silently selecting a fallback.
    llm_provider: Literal["nvidia", "openai", "openai_compatible", "anthropic"] | None = None
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    llm_request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    llm_max_response_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=16 * 1024,
        le=64 * 1024 * 1024,
    )
    llm_max_sse_event_bytes: int = Field(default=256 * 1024, ge=1024, le=2 * 1024 * 1024)
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=16_384)
    llm_max_context_tokens: int = Field(default=32_768, ge=1024, le=1_048_576)
    llm_max_concurrent_requests: int = Field(default=8, ge=1, le=64)
    llm_max_tool_rounds: int = Field(default=4, ge=0, le=16)
    llm_max_tool_calls: int = Field(default=8, ge=1, le=64)
    llm_max_tool_wall_time_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_tool_result_chars: int = Field(default=16_384, ge=256, le=1_048_576)
    llm_max_retry_attempts: int = Field(default=2, ge=0, le=5)
    llm_anthropic_version: str = "2023-06-01"

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

    @model_validator(mode="after")
    def validate_llm_configuration(self) -> Self:
        """Require one complete, safe LLM configuration or no LLM configuration."""

        configured = (
            self.llm_provider,
            self.llm_base_url,
            self.llm_api_key,
            self.llm_model,
        )
        if not any(value is not None for value in configured):
            return self
        if any(value is None for value in configured):
            raise ValueError(
                "LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL must be configured together"
        )

        try:
            _ = self.llm_base_url_resolved
        except RuntimeError as error:
            raise ValueError(str(error)) from None
        if not self.llm_model or not self.llm_model.strip():
            raise ValueError("LLM_MODEL must not be blank")
        if self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip():
            raise ValueError("LLM_API_KEY must not be blank")
        return self

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
    def stt_api_url_resolved(self) -> str:
        """Return the configured remote transcription endpoint."""

        if not self.stt_api_url or not self.stt_api_url.strip():
            raise RuntimeError("STT_API_URL is required when STT_ENGINE=remote")
        value = self.stt_api_url.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("STT_API_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise RuntimeError("STT_API_URL must not contain query parameters or fragments")
        return value.rstrip("/")

    @property
    def llm_configured(self) -> bool:
        """Return whether a complete Phase 5 provider selection is present."""

        return all(
            value is not None
            for value in (
                self.llm_provider,
                self.llm_base_url,
                self.llm_api_key,
                self.llm_model,
            )
        )

    @property
    def llm_base_url_resolved(self) -> str:
        """Return a validated provider base URL without a trailing slash."""

        if not self.llm_base_url or not self.llm_base_url.strip():
            raise RuntimeError("LLM_BASE_URL is required when Phase 5 LLM is configured")
        value = self.llm_base_url.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("LLM_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise RuntimeError("LLM_BASE_URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise RuntimeError("LLM_BASE_URL must not contain query parameters or fragments")
        if parsed.path.rstrip("/").endswith(("/chat/completions", "/responses", "/messages")):
            raise RuntimeError("LLM_BASE_URL must be a base URL, not an inference endpoint")
        if self.llm_provider == "anthropic" and parsed.path.rstrip("/").endswith("/v1"):
            raise RuntimeError("Anthropic LLM_BASE_URL must not already include /v1")
        if parsed.scheme == "http" and not self._allow_insecure_llm_host(parsed.hostname):
            raise RuntimeError(
                "LLM_BASE_URL must use HTTPS except for local/private development hosts"
            )
        return value.rstrip("/")

    def _allow_insecure_llm_host(self, hostname: str | None) -> bool:
        if self.app_env.lower() not in {"development", "test"} or hostname is None:
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            address = ip_address(hostname)
        except ValueError:
            return False
        return address.is_loopback or address.is_private

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
