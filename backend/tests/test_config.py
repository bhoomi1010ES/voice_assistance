import uuid

import pytest

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
    assert settings.stt_engine == "remote"
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


def test_settings_loads_remote_stt_configuration_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setenv("STT_ENGINE", "remote")
    monkeypatch.setenv(
        "STT_API_URL",
        "https://skin-technologies-strategies-membership.trycloudflare.com/v1/audio/transcriptions",
    )
    monkeypatch.setenv("STT_API_KEY", "test-secret-key")
    monkeypatch.setenv("STT_API_MODEL", "test-model")
    monkeypatch.setenv("STT_API_LANGUAGE", "en")

    settings = Settings(_env_file=None)

    assert settings.stt_engine == "remote"
    assert settings.stt_api_url_resolved.endswith("/v1/audio/transcriptions")
    assert settings.stt_api_key is not None
    assert str(settings.stt_api_key) == "**********"
    assert settings.stt_api_key.get_secret_value() == "test-secret-key"
    assert settings.stt_api_model == "test-model"
    assert settings.stt_api_language == "en"


def test_remote_stt_url_must_be_absolute_http_url() -> None:
    settings = Settings(
        _env_file=None,
        stt_engine="remote",
        stt_api_url="not-an-url",
        stt_api_key="test-secret-key",
    )

    with pytest.raises(RuntimeError, match=r"STT_API_URL must be an absolute HTTP\(S\) URL"):
        _ = settings.stt_api_url_resolved


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


def test_graph_feature_flags_default_to_safe_bounded_values() -> None:
    settings = Settings(
        _env_file=None,
        memory_retrieval_mode="off",
        memory_write_enabled=False,
    )

    assert settings.graph_rag_mode == "off"
    assert settings.graph_write_enabled is False
    assert settings.graph_max_depth == 2
    assert settings.graph_max_query_entities == 3
    assert settings.graph_max_edges_per_entity == 10
    assert settings.graph_max_paths == 20
    assert settings.graph_max_memories == 10
    assert settings.graph_rag_timeout_ms == 50


@pytest.mark.parametrize("mode", ["off", "shadow", "inject"])
def test_graph_retrieval_mode_accepts_supported_values(mode: str) -> None:
    settings = Settings(
        _env_file=None,
        memory_retrieval_mode="off",
        memory_write_enabled=False,
        graph_rag_mode=mode,
    )

    assert settings.graph_rag_mode == mode


def test_graph_retrieval_mode_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="graph_rag_mode"):
        Settings(
            _env_file=None,
            memory_retrieval_mode="off",
            memory_write_enabled=False,
            graph_rag_mode="enabled",
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("graph_max_depth", 0),
        ("graph_max_depth", 4),
        ("graph_max_query_entities", 0),
        ("graph_max_query_entities", 11),
        ("graph_max_edges_per_entity", 0),
        ("graph_max_edges_per_entity", 101),
        ("graph_max_paths", 0),
        ("graph_max_paths", 101),
        ("graph_max_memories", 0),
        ("graph_max_memories", 51),
        ("graph_rag_timeout_ms", 0),
        ("graph_rag_timeout_ms", 5_001),
    ],
)
def test_graph_integer_limits_are_positive_and_bounded(field_name: str, invalid_value: int) -> None:
    with pytest.raises(ValueError, match=field_name):
        Settings(
            _env_file=None,
            memory_retrieval_mode="off",
            memory_write_enabled=False,
            **{field_name: invalid_value},
        )


def test_graph_feature_flags_load_from_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GRAPH_RAG_MODE=shadow\n"
        "GRAPH_WRITE_ENABLED=true\n"
        "GRAPH_MAX_DEPTH=3\n"
        "GRAPH_RAG_TIMEOUT_MS=125\n",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=env_file,
        memory_retrieval_mode="off",
        memory_write_enabled=False,
    )

    assert settings.graph_rag_mode == "shadow"
    assert settings.graph_write_enabled is True
    assert settings.graph_max_depth == 3
    assert settings.graph_rag_timeout_ms == 125


def test_okf_defaults_are_disabled_and_rag_only() -> None:
    settings = Settings(
        _env_file=None,
        okf_enabled=False,
        okf_sync_enabled=False,
        knowledge_mode="rag",
        okf_shadow_reads=False,
        okf_evaluation_enabled=False,
    )

    assert settings.okf_enabled is False
    assert settings.okf_sync_enabled is False
    assert settings.knowledge_mode == "rag"
    assert settings.knowledge_rag_timeout_ms == 30_000
    assert settings.okf_shadow_reads is False
    assert settings.okf_shadow_user_ids == ()
    assert settings.okf_evaluation_enabled is False
    assert settings.okf_evaluation_user_ids == ()
    assert settings.okf_worker_mode == "in_process"
    assert settings.okf_context_max_chars == 4_000
    assert settings.okf_query_limit == 20
    assert settings.okf_retrieval_timeout_ms == 75
    assert settings.okf_policy_version == "okf-v1"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"okf_sync_enabled": True}, "OKF_SYNC_ENABLED requires OKF_ENABLED=true"),
        ({"okf_shadow_reads": True}, "OKF_SHADOW_READS requires OKF_ENABLED=true"),
        (
            {"knowledge_mode": "okf"},
            "KNOWLEDGE_MODE=okf or combined requires OKF_ENABLED=true",
        ),
        (
            {"knowledge_mode": "combined"},
            "KNOWLEDGE_MODE=okf or combined requires OKF_ENABLED=true",
        ),
    ],
)
def test_okf_capabilities_require_master_switch(overrides: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "okf_enabled": False,
        "okf_sync_enabled": False,
        "knowledge_mode": "rag",
        "okf_shadow_reads": False,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        Settings(
            _env_file=None,
            **values,
        )


@pytest.mark.parametrize("knowledge_mode", ["rag", "okf", "combined"])
def test_okf_enabled_accepts_each_knowledge_mode(knowledge_mode: str) -> None:
    settings = Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        knowledge_mode=knowledge_mode,
        okf_shadow_reads=knowledge_mode == "rag",
        okf_shadow_user_ids=(
            (uuid.UUID("00000000-0000-0000-0000-000000000101"),) if knowledge_mode == "rag" else ()
        ),
    )

    assert settings.knowledge_mode == knowledge_mode
    assert settings.okf_sync_enabled is True
    assert settings.okf_shadow_reads is (knowledge_mode == "rag")


def test_okf_shadow_reads_require_explicit_disposable_user_allowlist() -> None:
    with pytest.raises(ValueError, match="explicit disposable OKF_SHADOW_USER_IDS"):
        Settings(_env_file=None, okf_enabled=True, okf_shadow_reads=True)


def test_okf_shadow_user_allowlist_rejects_duplicate_ids() -> None:
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    with pytest.raises(ValueError, match="must not contain duplicates"):
        Settings(
            _env_file=None,
            okf_enabled=True,
            okf_shadow_reads=True,
            okf_shadow_user_ids=(user_id, user_id),
        )


def test_okf_shadow_requires_rag_to_remain_authoritative() -> None:
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    with pytest.raises(ValueError, match="requires KNOWLEDGE_MODE=rag"):
        Settings(
            _env_file=None,
            okf_enabled=True,
            knowledge_mode="combined",
            okf_shadow_reads=True,
            okf_shadow_user_ids=(user_id,),
        )


def test_okf_shadow_allowlist_loads_from_environment(monkeypatch) -> None:
    disposable_user_id = "00000000-0000-0000-0000-000000000101"
    monkeypatch.setenv("OKF_ENABLED", "true")
    monkeypatch.setenv("OKF_SYNC_ENABLED", "false")
    monkeypatch.setenv("OKF_SHADOW_READS", "true")
    monkeypatch.setenv("KNOWLEDGE_MODE", "rag")
    monkeypatch.setenv("OKF_SHADOW_USER_IDS", f'["{disposable_user_id}"]')

    settings = Settings(_env_file=None)

    assert settings.okf_shadow_user_ids == (uuid.UUID(disposable_user_id),)


def test_okf_evaluation_requires_explicit_dev_owner_allowlist() -> None:
    owner_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    settings = Settings(
        _env_file=None,
        app_env="development",
        okf_enabled=True,
        okf_sync_enabled=True,
        okf_evaluation_enabled=True,
        okf_evaluation_user_ids=(owner_id,),
    )

    assert settings.okf_evaluation_enabled is True
    assert settings.okf_evaluation_user_ids == (owner_id,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_env": "production"}, "limited to development/test"),
        ({"okf_sync_enabled": False}, "requires OKF_ENABLED and OKF_SYNC_ENABLED"),
        ({"okf_evaluation_user_ids": ()}, "requires explicit owner UUIDs"),
    ],
)
def test_okf_evaluation_rejects_unsafe_configuration(overrides, message: str) -> None:
    values = {
        "_env_file": None,
        "app_env": "development",
        "okf_enabled": True,
        "okf_sync_enabled": True,
        "okf_evaluation_enabled": True,
        "okf_evaluation_user_ids": (uuid.UUID("00000000-0000-0000-0000-000000000101"),),
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        Settings(**values)


def test_okf_evaluation_allowlist_rejects_duplicate_ids() -> None:
    owner_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    with pytest.raises(ValueError, match="OKF_EVALUATION_USER_IDS must not contain duplicates"):
        Settings(
            _env_file=None,
            okf_enabled=True,
            okf_sync_enabled=True,
            okf_evaluation_enabled=True,
            okf_evaluation_user_ids=(owner_id, owner_id),
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("okf_context_max_chars", 511),
        ("okf_query_limit", 0),
        ("okf_retrieval_timeout_ms", 5_001),
        ("knowledge_rag_timeout_ms", 60_001),
        ("okf_shadow_max_concurrent", 0),
        ("okf_job_lease_seconds", 9),
        ("okf_job_max_attempts", 21),
        ("okf_policy_version", "contains spaces"),
    ],
)
def test_okf_limits_and_policy_version_are_validated(
    field_name: str, invalid_value: object
) -> None:
    with pytest.raises(ValueError, match=field_name):
        Settings(
            _env_file=None,
            okf_enabled=False,
            okf_sync_enabled=False,
            knowledge_mode="rag",
            okf_shadow_reads=False,
            **{field_name: invalid_value},
        )
