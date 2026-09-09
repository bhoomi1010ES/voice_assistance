from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.main import create_app
from app.memory.jobs import MemoryJobWorker
from app.models import (
    AuditLog,
    AuthSession,
    ConversationTurn,
    Device,
    MemoryItem,
    MemoryJob,
    Message,
    User,
    VoiceSession,
)
from app.services.auth import hash_password
from tests.test_phase2_resources_integration import _auth, _email, _login, _register
from tests.test_support import NoopSTTService

pytestmark = pytest.mark.integration


@pytest.fixture
def resource_client():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run Phase 6 memory checks.")
    settings = Settings(
        jwt_secret_key="phase6-memory-integration-secret-do-not-use-in-production",
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


def test_memory_settings_crud_and_delete_all(resource_client) -> None:
    client, _settings, emails = resource_client
    email = _email("phase6-memory")
    emails.add(email)
    _register(client, email)
    tokens = _login(client, email, "phase6-memory-device")
    headers = _auth(tokens)

    settings = client.get("/memories/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["enabled"] is True

    created = client.post(
        "/memories",
        headers=headers,
        json={
            "content": "I prefer green tea",
            "memory_type": "preference",
            "subject": "user",
            "predicate": "beverage",
        },
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]
    assert created.json()["memory_type"] == "preference"
    assert (
        client.get("/memories/search", params={"query": "green tea"}, headers=headers).status_code
        == 200
    )

    excluded = client.patch(
        "/memories/settings",
        headers=headers,
        json={"enabled": False, "timezone": "Asia/Kolkata"},
    )
    assert excluded.status_code == 200
    assert excluded.json() == {
        "enabled": False,
        "timezone": "Asia/Kolkata",
        "locale": "en",
        "version": 1,
    }
    assert (
        client.post(
            "/memories",
            headers=headers,
            json={"content": "should be rejected"},
        ).status_code
        == 409
    )

    assert (
        client.request(
            "DELETE",
            "/memories",
            headers=headers,
            json={"confirmation": "DELETE_ALL_MEMORY"},
        ).status_code
        == 204
    )
    assert client.get(f"/memories/{memory_id}", headers=headers).status_code == 404


def test_explicit_extraction_job_is_idempotent_and_owner_scoped(resource_client) -> None:
    _client, settings, emails = resource_client
    email = _email("phase6-worker")
    emails.add(email)
    asyncio.run(_run_extraction_job(settings, email))


async def _run_extraction_job(settings: Settings, email: str) -> None:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as session:
        now = datetime.now(UTC)
        user = User(
            id=user_id,
            email=email,
            password_hash=hash_password("worker-test-password"),
            memory_enabled=True,
        )
        device = Device(
            id=uuid.uuid4(),
            user_id=user_id,
            device_identifier=f"worker-{uuid.uuid4().hex}",
            platform="android",
        )
        auth_session = AuthSession(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device.id,
            refresh_token_hash=uuid.uuid4().hex,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=1),
        )
        voice_session = VoiceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device.id,
            auth_session_id=auth_session.id,
            protocol_version=1,
            status="completed",
            started_at=now,
            last_activity_at=now,
            ended_at=now,
        )
        turn = ConversationTurn(
            id=uuid.uuid4(),
            session_id=voice_session.id,
            user_id=user_id,
            turn_number=1,
            status="committed",
            started_at=now,
            ended_at=now,
        )
        message = Message(
            id=uuid.uuid4(),
            turn_id=turn.id,
            user_id=user_id,
            role="user",
            content="Please remember that I work remotely.",
            is_final=True,
            sequence_no=0,
        )
        job = MemoryJob(
            id=uuid.uuid4(),
            user_id=user_id,
            job_type="extract_turn",
            source_message_id=message.id,
            source_turn_id=turn.id,
            source_session_id=voice_session.id,
            idempotency_key=f"extract-test:{message.id}",
            status="pending",
            available_at=now,
            policy_version="phase6-explicit-v1",
        )
        session.add(user)
        await session.flush()
        session.add(device)
        await session.flush()
        session.add(auth_session)
        await session.flush()
        session.add(voice_session)
        await session.flush()
        session.add(turn)
        await session.flush()
        session.add(message)
        await session.flush()
        session.add(job)
        await session.commit()

        worker = MemoryJobWorker(settings)
        assert await worker.run_once(session) is True
        memory = await session.scalar(
            select(MemoryItem).where(MemoryItem.user_id == user_id, MemoryItem.status == "active")
        )
        stored_job = await session.get(MemoryJob, job.id)
        assert memory is not None
        assert memory.content == "I work remotely"
        assert stored_job is not None and stored_job.status == "completed"
    await engine.dispose()
