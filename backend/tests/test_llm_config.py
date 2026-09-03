from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.llm.factory import create_llm_provider
from app.llm.providers.nvidia import NvidiaProvider


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "llm_provider": "nvidia",
        "llm_base_url": "https://integrate.api.nvidia.com/v1",
        "llm_api_key": "test-placeholder-key",
        "llm_model": "nvidia/nemotron-3-super-120b-a12b",
    }
    values.update(overrides)
    return Settings(**values)


def test_llm_is_disabled_when_all_primary_settings_are_absent() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_configured is False
    assert settings.llm_provider is None


def test_nvidia_configuration_is_exact_and_secret_is_redacted() -> None:
    settings = _settings()

    assert settings.llm_configured is True
    assert settings.llm_provider == "nvidia"
    assert settings.llm_base_url_resolved == "https://integrate.api.nvidia.com/v1"
    assert settings.llm_model == "nvidia/nemotron-3-super-120b-a12b"
    assert settings.llm_api_key is not None
    assert str(settings.llm_api_key) == "**********"
    assert "test-placeholder-key" not in repr(settings)


@pytest.mark.parametrize("missing", ["llm_provider", "llm_base_url", "llm_api_key", "llm_model"])
def test_partial_llm_configuration_is_rejected(missing: str) -> None:
    values = {
        "llm_provider": "nvidia",
        "llm_base_url": "https://integrate.api.nvidia.com/v1",
        "llm_api_key": "test-placeholder-key",
        "llm_model": "nvidia/nemotron-3-super-120b-a12b",
    }
    values.pop(missing)

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "base_url",
    [
        "not-a-url",
        "https://user:password@example.test/v1",
        "https://example.test/v1?key=secret",
        "https://example.test/v1#fragment",
        "https://example.test/v1/chat/completions",
        "http://public.example.test/v1",
    ],
)
def test_unsafe_llm_base_urls_are_rejected(base_url: str) -> None:
    with pytest.raises(ValidationError):
        _settings(llm_base_url=base_url)


def test_private_http_is_allowed_only_outside_staging_and_production() -> None:
    settings = _settings(
        app_env="development",
        llm_provider="openai_compatible",
        llm_base_url="http://127.0.0.1:8104/v1/",
    )
    assert settings.llm_base_url_resolved == "http://127.0.0.1:8104/v1"

    with pytest.raises(ValidationError):
        _settings(
            app_env="production",
            llm_provider="openai_compatible",
            llm_base_url="http://127.0.0.1:8104/v1",
        )


def test_factory_selects_nvidia_without_rewriting_endpoint_or_model() -> None:
    provider = create_llm_provider(_settings())

    assert isinstance(provider, NvidiaProvider)
    assert provider.endpoint == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert provider.model == "nvidia/nemotron-3-super-120b-a12b"


@pytest.mark.parametrize(
    ("provider", "base_url", "expected_type"),
    [
        ("openai", "https://api.openai.com/v1", "OpenAIResponsesProvider"),
        ("anthropic", "https://api.anthropic.com", "AnthropicMessagesProvider"),
    ],
)
def test_factory_selects_native_provider_without_fallback(
    provider: str,
    base_url: str,
    expected_type: str,
) -> None:
    settings = _settings(llm_provider=provider, llm_base_url=base_url)
    selected = create_llm_provider(settings)

    assert type(selected).__name__ == expected_type
