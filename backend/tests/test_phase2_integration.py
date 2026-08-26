from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.models import AuditLog, User

pytestmark = pytest.mark.integration


def _unique_email(label: str) -> str:
    return f"phase2-{label}-{uuid.uuid4().hex}@example.test"


async def _cleanup_users(settings: Settings, emails: set[str]) -> None:
    if not emails or not settings.database_url:
        return
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_ids = list(
            (await session.scalars(select(User.id).where(User.email.in_(emails)))).all()
        )
        if user_ids:
            await session.execute(delete(AuditLog).where(AuditLog.user_id.in_(user_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
    await engine.dispose()


async def _audit_events(settings: Settings, emails: set[str]) -> list[AuditLog]:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_ids = list(
            (await session.scalars(select(User.id).where(User.email.in_(emails)))).all()
        )
        events = list(
            (
                await session.scalars(
                    select(AuditLog)
                    .where(AuditLog.user_id.in_(user_ids))
                    .order_by(AuditLog.created_at)
                )
            ).all()
        )
    await engine.dispose()
    return events


@pytest.fixture
def integration_client():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run Phase 2 integration checks.")

    settings = Settings(
        jwt_secret_key="phase2-integration-secret-key-do-not-use-in-production",
        access_token_expire_minutes=15,
        refresh_token_expire_days=1,
    )
    created_emails: set[str] = set()
    with TestClient(create_app(settings=settings)) as client:
        readiness = client.get("/ready")
        if readiness.status_code != 200:
            pytest.skip(f"Infrastructure unavailable: {readiness.json()}")
        yield client, settings, created_emails
    asyncio.run(_cleanup_users(settings, created_emails))


def _register(client: TestClient, email: str, password: str) -> dict:
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str, password: str, device: str) -> dict:
    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": password,
            "device_identifier": device,
            "platform": "android",
            "device_name": device,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_authentication_devices_sessions_and_cross_user_isolation(integration_client) -> None:
    client, settings, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email_a = _unique_email("user-a")
    email_b = _unique_email("user-b")
    created_emails.update({email_a, email_b})

    user_a = _register(client, email_a, password)
    user_b = _register(client, email_b, password)
    tokens_a = _login(client, email_a, password, "device-a")
    tokens_b = _login(client, email_b, password, "device-b")
    headers_a = _auth(tokens_a["access_token"])
    headers_b = _auth(tokens_b["access_token"])

    assert client.get("/auth/me", headers=headers_a).json()["id"] == user_a["id"]
    assert client.get("/auth/me", headers=headers_b).json()["id"] == user_b["id"]

    devices_a = client.get("/devices", headers=headers_a)
    devices_b = client.get("/devices", headers=headers_b)
    assert devices_a.status_code == 200
    assert devices_b.status_code == 200
    device_a = devices_a.json()[0]
    device_b = devices_b.json()[0]
    assert device_a["id"] != device_b["id"]
    assert all(item["id"] != device_b["id"] for item in devices_a.json())
    assert all(item["id"] != device_a["id"] for item in devices_b.json())

    sessions_a = client.get("/auth/sessions", headers=headers_a)
    sessions_b = client.get("/auth/sessions", headers=headers_b)
    assert sessions_a.status_code == 200
    assert sessions_b.status_code == 200
    session_a = sessions_a.json()[0]
    session_b = sessions_b.json()[0]
    assert session_a["id"] != session_b["id"]
    assert "refresh_token_hash" not in session_a

    assert client.post(f"/devices/{device_b['id']}/revoke", headers=headers_a).status_code == 404
    assert client.post(f"/devices/{device_a['id']}/revoke", headers=headers_b).status_code == 404
    assert (
        client.post(f"/auth/sessions/{session_b['id']}/revoke", headers=headers_a).status_code
        == 404
    )
    assert (
        client.post(f"/auth/sessions/{session_a['id']}/revoke", headers=headers_b).status_code
        == 404
    )
    assert (
        client.post(
            "/devices/register",
            headers=headers_a,
            json={
                "device_identifier": "attempted-cross-user-device",
                "platform": "android",
                "user_id": user_b["id"],
            },
        ).status_code
        == 422
    )

    assert (
        client.post(
            "/devices/register",
            headers=headers_a,
            json={"device_identifier": "device-a-extra", "platform": "android"},
        ).status_code
        == 201
    )

    events = asyncio.run(_audit_events(settings, created_emails))
    event_types = {event.event_type for event in events}
    assert {"ACCOUNT_REGISTERED", "LOGIN_SUCCESS", "DEVICE_REGISTERED"}.issubset(event_types)
    for event in events:
        assert "password" not in str(event.audit_metadata).lower()
        assert "token" not in str(event.audit_metadata).lower()


def test_registration_duplicate_and_invalid_login_are_rejected(integration_client) -> None:
    client, _, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email = _unique_email("duplicate")
    created_emails.add(email)
    _register(client, email, password)

    duplicate = client.post("/auth/register", json={"email": email, "password": password})
    assert duplicate.status_code == 409
    assert (
        client.post(
            "/auth/login",
            json={
                "email": email,
                "password": "wrong-password",
                "device_identifier": "invalid-password-device",
                "platform": "android",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login",
            json={
                "email": _unique_email("unknown"),
                "password": password,
                "device_identifier": "unknown-user-device",
                "platform": "android",
            },
        ).status_code
        == 401
    )


def test_refresh_rotation_logout_and_revocation(integration_client) -> None:
    client, _, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email = _unique_email("revocation")
    created_emails.add(email)
    _register(client, email, password)
    tokens = _login(client, email, password, "revocation-device")

    assert (
        client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"], "user_id": str(uuid.uuid4())},
        ).status_code
        == 422
    )

    rotated = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert (
        client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        ).status_code
        == 401
    )

    rotated_tokens = rotated.json()
    assert (
        client.post("/auth/logout", headers=_auth(rotated_tokens["access_token"])).status_code
        == 200
    )
    assert client.get("/auth/me", headers=_auth(rotated_tokens["access_token"])).status_code == 401
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": rotated_tokens["refresh_token"]}
        ).status_code
        == 401
    )

    second = _login(client, email, password, "revocation-device-2")
    devices = client.get("/devices", headers=_auth(second["access_token"])).json()
    device_id = devices[-1]["id"]
    assert (
        client.post(
            f"/devices/{device_id}/revoke",
            headers=_auth(second["access_token"]),
        ).status_code
        == 200
    )
    assert client.get("/auth/me", headers=_auth(second["access_token"])).status_code == 401
    assert (
        client.post(
            "/auth/refresh",
            json={"refresh_token": second["refresh_token"]},
        ).status_code
        == 401
    )


def test_invalid_expired_and_tampered_access_tokens_are_rejected(integration_client) -> None:
    client, settings, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email = _unique_email("invalid-tokens")
    created_emails.add(email)
    _register(client, email, password)
    tokens = _login(client, email, password, "invalid-token-device")
    access_token = tokens["access_token"]

    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert client.get("/auth/me", headers=_auth(f"{access_token}tampered")).status_code == 401

    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "typ": "access",
            "iss": settings.jwt_issuer,
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    assert client.get("/auth/me", headers=_auth(expired)).status_code == 401


def test_websocket_authentication_and_identity_override_are_blocked(integration_client) -> None:
    client, _, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email_a = _unique_email("socket-a")
    email_b = _unique_email("socket-b")
    created_emails.update({email_a, email_b})
    _register(client, email_a, password)
    _register(client, email_b, password)
    tokens_a = _login(client, email_a, password, "socket-device-a")
    tokens_b = _login(client, email_b, password, "socket-device-b")
    user_a = client.get("/auth/me", headers=_auth(tokens_a["access_token"])).json()
    user_b = client.get("/auth/me", headers=_auth(tokens_b["access_token"])).json()

    with client.websocket_connect("/ws", headers=_auth(tokens_a["access_token"])) as socket_a:
        assert socket_a.receive_json() == {"type": "authenticated", "user_id": user_a["id"]}
        socket_a.send_json({"message": "hello"})
        assert socket_a.receive_json() == {
            "type": "ack",
            "user_id": user_a["id"],
            "message": "hello",
        }
        socket_a.send_json({"user_id": user_b["id"], "message": "cross-scope"})
        with pytest.raises(WebSocketDisconnect) as disconnect:
            socket_a.receive_json()
        assert disconnect.value.code == 1008

    with client.websocket_connect("/ws", headers=_auth(tokens_b["access_token"])) as socket_b:
        assert socket_b.receive_json() == {"type": "authenticated", "user_id": user_b["id"]}
        socket_b.send_json({"user_id": user_a["id"], "message": "cross-scope"})
        with pytest.raises(WebSocketDisconnect) as disconnect:
            socket_b.receive_json()
        assert disconnect.value.code == 1008

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer malformed"}):
            pass


def test_revoked_session_and_device_cannot_open_websocket(integration_client) -> None:
    client, _, created_emails = integration_client
    password = "correct-horse-battery-staple"
    email = _unique_email("socket-revocation")
    created_emails.add(email)
    _register(client, email, password)
    tokens = _login(client, email, password, "socket-revocation-device")
    assert client.post("/auth/logout", headers=_auth(tokens["access_token"])).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=_auth(tokens["access_token"])):
            pass

    replacement = _login(client, email, password, "socket-revocation-device")
    device_id = client.get("/devices", headers=_auth(replacement["access_token"])).json()[0]["id"]
    assert (
        client.post(
            f"/devices/{device_id}/revoke", headers=_auth(replacement["access_token"])
        ).status_code
        == 200
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=_auth(replacement["access_token"])):
            pass
