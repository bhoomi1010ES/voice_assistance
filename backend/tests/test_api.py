from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.test_support import NoopSTTService


class StubInfrastructure:
    def __init__(self, readiness: dict[str, Any]) -> None:
        self.readiness = readiness
        self.closed = False

    async def check_readiness(self) -> dict[str, Any]:
        return self.readiness

    async def close(self) -> None:
        self.closed = True


def test_health_does_not_require_dependencies() -> None:
    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_ready_reports_missing_connection_urls_without_crashing() -> None:
    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())

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
