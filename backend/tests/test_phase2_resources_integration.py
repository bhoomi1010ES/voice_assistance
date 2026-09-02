from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.main import create_app
from app.models import AuditLog, User, VoiceSession
from tests.test_support import NoopSTTService

pytestmark = pytest.mark.integration


def _email(label: str) -> str:
    return f"phase2-resources-{label}-{uuid.uuid4().hex}@example.test"


async def _cleanup(settings: Settings, emails: set[str]) -> None:
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


async def _insert_voice_session(settings: Settings, tokens: dict, *, status: str) -> str:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(
            VoiceSession(
                id=session_id,
                user_id=uuid.UUID(tokens["user"]["id"]),
                device_id=uuid.UUID(tokens["device"]["id"]),
                auth_session_id=uuid.UUID(tokens["session"]["id"]),
                protocol_version=1,
                status=status,
                started_at=now,
                last_activity_at=now,
                ended_at=now if status != "active" else None,
            )
        )
        await db.commit()
    await engine.dispose()
    return str(session_id)


@pytest.fixture
def resource_client():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run Phase 2 resource checks.")

    settings = Settings(
        jwt_secret_key="phase2-resource-integration-secret-do-not-use-in-production",
        access_token_expire_minutes=15,
        refresh_token_expire_days=1,
    )
    emails: set[str] = set()
    with TestClient(create_app(settings=settings, stt_service=NoopSTTService())) as client:
        readiness = client.get("/ready")
        if readiness.status_code != 200:
            pytest.skip(f"Infrastructure unavailable: {readiness.json()}")
        yield client, settings, emails
    asyncio.run(_cleanup(settings, emails))


def _register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str, device: str) -> dict:
    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "device_identifier": device,
            "platform": "android",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_memory_task_and_session_ownership_matrix(resource_client) -> None:
    client, settings, emails = resource_client
    email_a = _email("a")
    email_b = _email("b")
    emails.update({email_a, email_b})
    _register(client, email_a)
    _register(client, email_b)
    tokens_a = _login(client, email_a, "resource-device-a")
    tokens_b = _login(client, email_b, "resource-device-b")
    headers_a = _auth(tokens_a)
    headers_b = _auth(tokens_b)

    memory_a = client.post(
        "/memories",
        headers=headers_a,
        json={"content": "User A private memory", "metadata": {"source": "test"}},
    )
    memory_b = client.post(
        "/memories",
        headers=headers_b,
        json={"content": "User B private memory", "metadata": {"source": "test"}},
    )
    assert memory_a.status_code == 201
    assert memory_b.status_code == 201
    memory_a_id = memory_a.json()["id"]
    memory_b_id = memory_b.json()["id"]
    assert "user_id" not in memory_a.json()

    assert client.get(f"/memories/{memory_a_id}", headers=headers_a).status_code == 200
    assert client.get(f"/memories/{memory_b_id}", headers=headers_a).status_code == 404
    assert client.get(f"/memories/{memory_a_id}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"/memories/{memory_b_id}",
            headers=headers_a,
            json={"content": "attempted overwrite"},
        ).status_code
        == 404
    )
    assert client.delete(f"/memories/{memory_b_id}", headers=headers_a).status_code == 404
    assert (
        client.patch(
            f"/memories/{memory_a_id}",
            headers=headers_b,
            json={"content": "attempted overwrite"},
        ).status_code
        == 404
    )
    assert client.delete(f"/memories/{memory_a_id}", headers=headers_b).status_code == 404
    assert client.get("/memories?user_id=" + tokens_b["user"]["id"], headers=headers_a).json() == [
        memory_a.json()
    ]
    assert (
        client.get("/memories/search", params={"query": "User B private"}, headers=headers_a).json()
        == []
    )
    assert (
        client.post(
            "/memories",
            headers=headers_a,
            json={"content": "forged owner", "user_id": tokens_b["user"]["id"]},
        ).status_code
        == 422
    )

    task_a = client.post(
        "/tasks",
        headers=headers_a,
        json={"title": "User A task", "description": "private"},
    )
    task_b = client.post(
        "/tasks",
        headers=headers_b,
        json={"title": "User B task", "description": "private"},
    )
    assert task_a.status_code == 201
    assert task_b.status_code == 201
    task_a_id = task_a.json()["id"]
    task_b_id = task_b.json()["id"]
    assert client.get(f"/tasks/{task_a_id}", headers=headers_a).status_code == 200
    assert client.get(f"/tasks/{task_b_id}", headers=headers_a).status_code == 404
    assert client.get(f"/tasks/{task_a_id}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"/tasks/{task_b_id}", headers=headers_a, json={"title": "attempted overwrite"}
        ).status_code
        == 404
    )
    assert client.delete(f"/tasks/{task_b_id}", headers=headers_a).status_code == 404
    assert (
        client.patch(
            f"/tasks/{task_a_id}", headers=headers_b, json={"title": "attempted overwrite"}
        ).status_code
        == 404
    )
    assert client.delete(f"/tasks/{task_a_id}", headers=headers_b).status_code == 404
    assert (
        client.post(
            "/tasks",
            headers=headers_a,
            json={"title": "forged owner", "user_id": tokens_b["user"]["id"]},
        ).status_code
        == 422
    )

    session_a_id = asyncio.run(_insert_voice_session(settings, tokens_a, status="completed"))
    session_b_id = asyncio.run(_insert_voice_session(settings, tokens_b, status="completed"))
    sessions_a = client.get("/sessions", headers=headers_a)
    sessions_b = client.get("/sessions", headers=headers_b)
    assert sessions_a.status_code == 200
    assert sessions_b.status_code == 200
    assert session_a_id in {item["id"] for item in sessions_a.json()}
    assert session_b_id not in {item["id"] for item in sessions_a.json()}
    assert session_b_id in {item["id"] for item in sessions_b.json()}
    assert session_a_id not in {item["id"] for item in sessions_b.json()}
    assert client.get(f"/sessions/{session_a_id}", headers=headers_a).status_code == 200
    assert client.get(f"/sessions/{session_b_id}", headers=headers_a).status_code == 404
    assert client.get(f"/sessions/{session_b_id}", headers=headers_b).status_code == 200
    assert client.get(f"/sessions/{session_a_id}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"/sessions/{session_b_id}",
            headers=headers_a,
            json={"client_metadata": {"attempt": "cross-user"}},
        ).status_code
        == 404
    )
    assert client.post(f"/sessions/{session_b_id}/cancel", headers=headers_a).status_code == 404
    assert client.delete(f"/sessions/{session_b_id}", headers=headers_a).status_code == 404
    assert (
        client.patch(
            f"/sessions/{session_a_id}",
            headers=headers_b,
            json={"client_metadata": {"attempt": "cross-user"}},
        ).status_code
        == 404
    )
    assert client.post(f"/sessions/{session_a_id}/cancel", headers=headers_b).status_code == 404
    assert client.delete(f"/sessions/{session_a_id}", headers=headers_b).status_code == 404

    own_session_update = client.patch(
        f"/sessions/{session_a_id}",
        headers=headers_a,
        json={"client_metadata": {"label": "owned"}},
    )
    assert own_session_update.status_code == 200
    assert own_session_update.json()["client_metadata"] == {"label": "owned"}

    assert (
        client.patch(
            f"/tasks/{task_a_id}", headers=headers_a, json={"status": "completed"}
        ).status_code
        == 200
    )
    assert client.delete(f"/tasks/{task_a_id}", headers=headers_a).status_code == 204
    assert client.delete(f"/memories/{memory_a_id}", headers=headers_a).status_code == 204

    events = asyncio.run(_audit_events(settings, email_a))
    denied = [event for event in events if event.event_type == "UNAUTHORIZED_ACCESS"]
    assert len(denied) >= 7
    for event in denied:
        assert "password" not in str(event.audit_metadata).lower()
        assert "token" not in str(event.audit_metadata).lower()


def test_owned_session_cancel_and_delete_are_scoped(resource_client) -> None:
    client, settings, emails = resource_client
    email_a = _email("session-a")
    email_b = _email("session-b")
    emails.update({email_a, email_b})
    _register(client, email_a)
    _register(client, email_b)
    tokens_a = _login(client, email_a, "session-device-a")
    tokens_b = _login(client, email_b, "session-device-b")
    headers_a = _auth(tokens_a)
    headers_b = _auth(tokens_b)

    session_a_id = asyncio.run(_insert_voice_session(settings, tokens_a, status="active"))
    session_b_id = asyncio.run(_insert_voice_session(settings, tokens_b, status="completed"))

    cancelled = client.post(f"/sessions/{session_a_id}/cancel", headers=headers_a)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["close_reason"] == "cancelled_by_user"
    assert client.delete(f"/sessions/{session_a_id}", headers=headers_a).status_code == 204
    assert client.get(f"/sessions/{session_a_id}", headers=headers_a).status_code == 404

    assert client.delete(f"/sessions/{session_b_id}", headers=headers_a).status_code == 404
    assert client.delete(f"/sessions/{session_b_id}", headers=headers_b).status_code == 204


async def _audit_events(settings: Settings, email: str) -> list[AuditLog]:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_id = await session.scalar(select(User.id).where(User.email == email))
        events = list(
            (await session.scalars(select(AuditLog).where(AuditLog.user_id == user_id))).all()
        )
    await engine.dispose()
    return events
