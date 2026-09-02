from app.core.config import Settings


def test_settings_loads_connection_urls_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://user:password@db.example:55432/voice_assistance"
    )
    monkeypatch.setenv("REDIS_URL", "redis://cache.example:56379/2")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == (
        "postgresql+asyncpg://user:password@db.example:55432/voice_assistance"
    )
    assert settings.redis_url == "redis://cache.example:56379/2"
    assert settings.stt_engine == "windows"
    assert settings.stt_windows_language == "en-US"
    assert settings.database_dsn == settings.database_url
    assert settings.redis_dsn == settings.redis_url
    assert settings.stt_model_path == "models/whisper-large-v3-turbo-ct2"
    assert settings.stt_device == "cpu"
    assert settings.stt_compute_type == "int8"
    assert settings.stt_language is None
    assert settings.stt_beam_size == 1
    assert settings.stt_threads == 4
    assert settings.stt_workers == 2
    assert settings.stt_timeout == 180.0


def test_settings_loads_stt_configuration_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("STT_MODEL_PATH", "D:/models/whisper-large-v3-turbo-ct2")
    monkeypatch.setenv("STT_DEVICE", "cpu")
    monkeypatch.setenv("STT_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.setenv("STT_BEAM_SIZE", "3")
    monkeypatch.setenv("STT_WORKERS", "2")
    monkeypatch.setenv("STT_TIMEOUT", "12.5")

    settings = Settings(_env_file=None)

    assert settings.stt_model_path == "D:/models/whisper-large-v3-turbo-ct2"
    assert settings.stt_device == "cpu"
    assert settings.stt_compute_type == "int8"
    assert settings.stt_language == "en"
    assert settings.stt_beam_size == 3
    assert settings.stt_workers == 2
    assert settings.stt_timeout == 12.5


def test_settings_loads_windows_stt_configuration_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("STT_ENGINE", "windows")
    monkeypatch.setenv("STT_WINDOWS_WORKER_PATH", "C:/workers/WindowsSttWorker.exe")
    monkeypatch.setenv("STT_WINDOWS_LANGUAGE", "en-US")
    monkeypatch.setenv("STT_START_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("STT_FINAL_TIMEOUT_SECONDS", "22")
    monkeypatch.setenv("STT_WORKER_TIMEOUT_SECONDS", "3")

    settings = Settings(_env_file=None)

    assert settings.stt_engine == "windows"
    assert settings.stt_windows_worker_path == "C:/workers/WindowsSttWorker.exe"
    assert settings.stt_windows_language == "en-US"
    assert settings.stt_start_timeout_seconds == 7
    assert settings.stt_final_timeout_seconds == 22
    assert settings.stt_worker_timeout_seconds == 3


def test_database_dsn_normalizes_postgresql_scheme_to_asyncpg() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:password@db.example:5432/voice_assistance",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.database_dsn == (
        "postgresql+asyncpg://user:password@db.example:5432/voice_assistance"
    )


def test_settings_loads_values_from_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=dotenv\n"
        "LOG_LEVEL=WARNING\n"
        "DATABASE_URL=postgresql+asyncpg://dotenv:password@localhost:5432/dotenv\n"
        "REDIS_URL=redis://localhost:6379/4\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "dotenv"
    assert settings.log_level == "WARNING"
    assert settings.database_url == "postgresql+asyncpg://dotenv:password@localhost:5432/dotenv"
    assert settings.redis_url == "redis://localhost:6379/4"


def test_environment_overrides_dotenv_values(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=dotenv\n"
        "LOG_LEVEL=WARNING\n"
        "DATABASE_URL=postgresql+asyncpg://dotenv:password@localhost:5432/dotenv\n"
        "REDIS_URL=redis://localhost:6379/4\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV", "environment")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://environment:password@localhost:5432/environment"
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/5")

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "environment"
    assert settings.log_level == "ERROR"
    assert settings.database_url.endswith("/environment")
    assert settings.redis_url == "redis://localhost:6379/5"
