from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.api.dependencies import get_current_principal, get_db
from app.core.config import Settings
from app.main import create_app
from app.models import Device
from app.services.auth import AuthPrincipal
from tests.test_support import NoopSTTService


def _sample_device(
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    push_token: str | None = None,
) -> Device:
    now = datetime.now(UTC)
    return Device(
        id=device_id,
        user_id=user_id,
        device_identifier="android-test-identifier",
        platform="android",
        device_kind="physical",
        name="Test Android Device",
        device_metadata={"brand": "Google", "model": "Pixel"},
        push_token=push_token,
        push_token_revoked_at=None,
        created_at=now,
        last_seen_at=now,
        revoked_at=None,
    )


def test_update_push_token_success_sets_token_and_clears_revoked_at() -> None:
    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    device = _sample_device(user_id, device_id, push_token="old-token")
    device.push_token_revoked_at = datetime.now(UTC)

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = device
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    principal = AuthPrincipal(user_id=user_id, device_id=device_id, session_id=session_id)

    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: session

    with TestClient(app) as client:
        response = client.patch(
            f"/devices/{device_id}/push-token",
            json={"token": "new-fcm-device-token"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(device_id)
    assert device.push_token == "new-fcm-device-token"
    assert device.push_token_revoked_at is None
    session.commit.assert_awaited()
    session.refresh.assert_awaited_with(device)


def test_update_push_token_null_clears_token_and_sets_revoked_at() -> None:
    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    device = _sample_device(user_id, device_id, push_token="existing-fcm-token")

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = device
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    principal = AuthPrincipal(user_id=user_id, device_id=device_id, session_id=session_id)

    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: session

    with TestClient(app) as client:
        response = client.patch(
            f"/devices/{device_id}/push-token",
            json={"token": None},
        )

    assert response.status_code == 200
    assert device.push_token is None
    assert device.push_token_revoked_at is not None
    assert isinstance(device.push_token_revoked_at, datetime)
    session.commit.assert_awaited()


def test_update_push_token_empty_string_normalizes_to_none() -> None:
    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    device = _sample_device(user_id, device_id, push_token="existing-fcm-token")

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = device
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    principal = AuthPrincipal(user_id=user_id, device_id=device_id, session_id=session_id)

    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: session

    with TestClient(app) as client:
        response = client.patch(
            f"/devices/{device_id}/push-token",
            json={"token": "   "},
        )

    assert response.status_code == 200
    assert device.push_token is None
    assert device.push_token_revoked_at is not None
    session.commit.assert_awaited()


def test_update_push_token_device_not_found_returns_404_and_records_audit() -> None:
    user_id = uuid.uuid4()
    device_id = uuid.uuid4()
    session_id = uuid.uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = None
    session.commit = AsyncMock()

    principal = AuthPrincipal(user_id=user_id, device_id=device_id, session_id=session_id)

    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: session

    with TestClient(app) as client:
        response = client.patch(
            f"/devices/{device_id}/push-token",
            json={"token": "any-token"},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["detail"]["code"] == "RESOURCE_NOT_FOUND"
    session.commit.assert_awaited()


def test_update_push_token_unauthenticated_returns_401() -> None:
    device_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    app = create_app(settings=Settings(_env_file=None), stt_service=NoopSTTService())
    app.dependency_overrides[get_db] = lambda: session

    with TestClient(app) as client:
        response = client.patch(
            f"/devices/{device_id}/push-token",
            json={"token": "any-token"},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTHENTICATION_FAILED"
