from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.test_support import NoopSTTService


class StubInfrastructure:
    async def check_readiness(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "dependencies": {
                "postgres": {"status": "ok"},
                "redis": {"status": "ok"},
            },
        }

    async def close(self) -> None:
        return None


class StubLLMService:
    def __init__(self) -> None:
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    def readiness(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "status": "ready",
            "provider": "nvidia",
            "host": "https://integrate.api.nvidia.com",
            "model": "nvidia/nemotron-3-super-120b-a12b",
            "api_family": "openai_chat_completions",
            "live_verified": False,
            "capabilities": {"streaming": True, "text_generation": True},
        }

    async def close(self) -> None:
        self.closed = True


def test_ready_exposes_only_safe_llm_configuration() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_provider="nvidia",
        llm_base_url="https://integrate.api.nvidia.com/v1",
        llm_api_key="test-placeholder-key",
        llm_model="nvidia/nemotron-3-super-120b-a12b",
    )
    service = StubLLMService()
    app = create_app(
        settings=settings,
        infrastructure=StubInfrastructure(),
        stt_service=NoopSTTService(),
        llm_service=service,
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dependencies"]["llm"]["provider"] == "nvidia"
    assert payload["dependencies"]["llm"]["model"] == (
        "nvidia/nemotron-3-super-120b-a12b"
    )
    assert "test-placeholder-key" not in response.text
    assert service.initialized is True
    assert service.closed is True
