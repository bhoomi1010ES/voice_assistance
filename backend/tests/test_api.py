from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.tts.base import TTSEngineInfo
from tests.test_support import NoopSTTService


class StubInfrastructure:
    def __init__(self, readiness: dict[str, Any]) -> None:
        self.readiness = readiness
        self.closed = False

    async def check_readiness(self) -> dict[str, Any]:
        return self.readiness

    async def close(self) -> None:
        self.closed = True


class StubTTSService:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self.info: TTSEngineInfo | None = None
        self.closed = False

    async def initialize(self) -> TTSEngineInfo:
        self.info = TTSEngineInfo(
            enabled=self._enabled,
            provider="remote",
            model="kokoro" if self._enabled else None,
        )
        return self.info

    async def close(self) -> None:
        self.closed = True


class StubLLMService:
    async def initialize(self) -> None:
        return None

    def readiness(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "status": "ready",
            "provider": "test-provider",
            "model": "test-model",
        }

    async def close(self) -> None:
        return None


def test_health_does_not_require_dependencies() -> None:
    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_ready_reports_missing_connection_urls_without_crashing() -> None:
    app = create_app(
        settings=Settings(_env_file=None, database_url=None, redis_url=None),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {
            "postgres": {"status": "error", "error": "DATABASE_URL_NOT_CONFIGURED"},
            "redis": {"status": "error", "error": "REDIS_URL_NOT_CONFIGURED"},
        },
    }


def test_ready_returns_200_when_dependencies_are_healthy() -> None:
    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    app = create_app(
        settings=Settings(_env_file=None),
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == readiness


def test_ready_reports_in_process_okf_worker_state() -> None:
    settings = Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        okf_worker_mode="in_process",
        memory_retrieval_mode="off",
        memory_write_enabled=False,
    )
    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    app = create_app(
        settings=settings,
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        app.state.okf_worker = SimpleNamespace(running=True)
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"]["okf_sync_worker"] == {
        "enabled": True,
        "mode": "in_process",
        "status": "ready",
    }


def test_ready_fails_when_required_in_process_okf_worker_is_missing() -> None:
    settings = Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        okf_worker_mode="in_process",
        memory_retrieval_mode="off",
        memory_write_enabled=False,
    )
    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    app = create_app(
        settings=settings,
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["okf_sync_worker"] == {
        "enabled": True,
        "mode": "in_process",
        "status": "not_ready",
    }


def test_ready_returns_503_with_dependency_details_when_unavailable() -> None:
    readiness = {
        "status": "not_ready",
        "dependencies": {
            "postgres": {"status": "error", "error": "ConnectionRefusedError"},
            "redis": {"status": "ok"},
        },
    }
    app = create_app(
        settings=Settings(_env_file=None),
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == readiness


def test_ready_returns_503_when_redis_is_unavailable() -> None:
    readiness = {
        "status": "not_ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "error", "error": "ConnectionRefusedError"},
        },
    }
    app = create_app(
        settings=Settings(_env_file=None),
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == readiness


def test_ready_warms_and_probes_router_when_mode_is_on(monkeypatch) -> None:
    from app.routing import graph as routing_graph

    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    original_build_router_graph = routing_graph.build_router_graph
    compile_calls = 0

    def tracked_build_router_graph():
        nonlocal compile_calls
        compile_calls += 1
        return original_build_router_graph()

    monkeypatch.setattr(routing_graph, "build_router_graph", tracked_build_router_graph)
    app = create_app(
        settings=Settings(_env_file=None, router_mode="on"),
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert compile_calls == 1
    assert response.json()["dependencies"]["router"] == {
        "enabled": True,
        "status": "ready",
        "mode": "on",
        "graph_compiled": True,
        "route_handlers": {
            "status": "ready",
            "registered": 8,
            "expected": 8,
        },
        "decision_probe": {
            "status": "ready",
            "route": "GENERAL_LLM",
        },
    }


def test_ready_returns_503_when_on_router_cannot_compile(monkeypatch) -> None:
    from app.routing import graph as routing_graph

    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }

    def fail_build_router_graph():
        raise ImportError("langgraph unavailable")

    monkeypatch.setattr(routing_graph, "build_router_graph", fail_build_router_graph)
    app = create_app(
        settings=Settings(_env_file=None, router_mode="on"),
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["router"] == {
        "enabled": True,
        "status": "not_ready",
        "mode": "on",
        "graph_compiled": False,
        "error": "ROUTER_GRAPH_NOT_COMPILED",
    }


@pytest.mark.parametrize("router_mode", ["off", "shadow", "canary", "on"])
def test_ready_reports_initialized_tts_without_changing_other_dependencies_or_exposing_secrets(
    router_mode: str,
) -> None:
    settings = Settings(
        _env_file=None,
        tts_api_url="https://tts.example.test/v1/audio/speech",
        tts_api_key="tts-test-secret-value",
        router_mode=router_mode,
    )
    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    tts_service = StubTTSService(enabled=True)
    llm_service = StubLLMService()
    app = create_app(
        settings=settings,
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
        llm_service=llm_service,
        tts_service=tts_service,
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dependencies"]["postgres"] == {"status": "ok"}
    assert payload["dependencies"]["redis"] == {"status": "ok"}
    assert payload["dependencies"]["llm"]["status"] == "ready"
    assert payload["dependencies"]["tts"] == {
        "enabled": True,
        "status": "ready",
        "provider": "remote",
        "model": "kokoro",
    }
    assert "tts-test-secret-value" not in response.text
    assert tts_service.closed is True


def test_ready_reports_configured_but_unavailable_tts_as_not_ready() -> None:
    settings = Settings(
        _env_file=None,
        tts_api_url="https://tts.example.test/v1/audio/speech",
        tts_api_key="tts-test-secret-value",
        router_mode="off",
    )
    readiness = {
        "status": "ready",
        "dependencies": {
            "postgres": {"status": "ok"},
            "redis": {"status": "ok"},
        },
    }
    tts_service = StubTTSService(enabled=False)
    llm_service = StubLLMService()
    app = create_app(
        settings=settings,
        infrastructure=StubInfrastructure(readiness),
        stt_service=NoopSTTService(),
        llm_service=llm_service,
        tts_service=tts_service,
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["dependencies"]["postgres"] == {"status": "ok"}
    assert payload["dependencies"]["redis"] == {"status": "ok"}
    assert payload["dependencies"]["llm"]["status"] == "ready"
    assert payload["dependencies"]["tts"] == {
        "enabled": False,
        "status": "not_ready",
        "provider": "remote",
        "model": None,
    }
    assert "tts-test-secret-value" not in response.text
    assert tts_service.closed is True
