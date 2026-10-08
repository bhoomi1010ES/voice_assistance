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
from app.memory.policy import ExtractionCandidate
from app.memory.repository import MemoryRepository
from app.memory.retrieval import MemoryRetrievalService, fts_retrieve
from app.memory.types import MemorySourceKind, MemoryType, build_memory_query_plan
from app.memory.writer import MemoryWriter
from app.models import (
    AuditLog,
    AuthSession,
    ConversationTurn,
    Device,
    MemoryItem,
    MemoryJob,
    Message,
    OkfSyncJob,
    User,
    VoiceSession,
)
from app.okf.lifecycle import OkfLifecycleService
from app.services.auth import hash_password
from tests.test_phase2_resources_integration import _auth, _email, _login, _register
from tests.test_support import NoopSTTService

pytestmark = pytest.mark.integration


def _memory_settings(base: Settings | None = None) -> Settings:
    """Explicit capability settings for direct DB tests; no provider is constructed."""
    base = base or Settings()
    return Settings(
        database_url=base.database_url,
        redis_url=base.redis_url,
        stt_api_key="audit-synthetic-memory-key",
        memory_write_enabled=True,
        memory_retrieval_mode="inject",
        embedding_api_url="https://memory.invalid/v1/embeddings",
        rerank_api_url="https://memory.invalid/v1/rerank",
    )


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


async def _create_voice_sourced_memory(
    settings: Settings,
    email: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_id = await session.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        device = await session.scalar(
            select(Device)
            .where(Device.user_id == user_id, Device.revoked_at.is_(None))
            .order_by(Device.created_at.desc())
        )
        auth_session = await session.scalar(
            select(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.created_at.desc())
        )
        assert device is not None and auth_session is not None
        now = datetime.now(UTC)
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
        session.add(voice_session)
        await session.flush()
        memory, created = await MemoryWriter(settings).write_candidate(
            session,
            user_id=user_id,
            candidate=ExtractionCandidate(
                content="The Orion project uses Redis",
                memory_type=MemoryType.FACT,
                subject="Project Orion",
                predicate="uses",
                object_json={"service": "Redis"},
                confidence=1.0,
                salience=1.0,
            ),
            source_kind=MemorySourceKind.EXPLICIT_TOOL,
            source_session_id=voice_session.id,
        )
        assert created
        await session.commit()
        result = (voice_session.id, memory.id)
    await engine.dispose()
    return result


async def _assert_memory_update_kept_source_and_enqueued_sync(
    settings: Settings,
    email: str,
    source_session_id: uuid.UUID,
    old_memory_id: uuid.UUID,
    new_memory_id: uuid.UUID,
) -> None:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        old = await session.get(MemoryItem, old_memory_id)
        new = await session.get(MemoryItem, new_memory_id)
        assert old is not None and new is not None
        assert old.status == "superseded"
        assert old.content == "The Orion project uses Redis"
        assert old.subject == "Project Orion" and old.predicate == "uses"
        assert old.object_json == {"service": "Redis"}
        assert new.status == "active" and new.supersedes_id == old.id
        assert new.source_session_id == source_session_id
        assert new.subject == "Project Orion" and new.predicate == "uses"
        assert new.object_json == {"service": "PostgreSQL"}
        user_id = await session.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        upsert = await session.scalar(
            select(OkfSyncJob).where(
                OkfSyncJob.user_id == user_id,
                OkfSyncJob.memory_id == new_memory_id,
                OkfSyncJob.event_type == "upsert_memory",
            )
        )
        assert upsert is not None and upsert.status == "pending"
    await engine.dispose()


def test_memory_settings_crud_and_delete_all(resource_client) -> None:
    client, settings, emails = resource_client
    client.app.state.settings = settings.model_copy(
        update={"okf_enabled": True, "okf_sync_enabled": True}
    )
    email = _email("phase6-memory")
    emails.add(email)
    _register(client, email)
    tokens = _login(client, email, "phase6-memory-device")
    headers = _auth(tokens)

    settings = client.get("/memories/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["enabled"] is True
    assert settings.json()["knowledge_mode"] == "rag"
    assert settings.json()["okf_available"] is True

    selected_okf = client.patch(
        "/memories/settings", headers=headers, json={"knowledge_mode": "okf"}
    )
    assert selected_okf.status_code == 200, selected_okf.text
    assert selected_okf.json()["knowledge_mode"] == "okf"
    assert client.get("/memories/settings", headers=headers).json()["knowledge_mode"] == "okf"

    client.app.state.settings = client.app.state.settings.model_copy(
        update={"okf_enabled": False, "okf_sync_enabled": False}
    )
    unavailable_settings = client.get("/memories/settings", headers=headers)
    assert unavailable_settings.status_code == 200
    assert unavailable_settings.json()["okf_available"] is False
    rejected_okf = client.patch(
        "/memories/settings", headers=headers, json={"knowledge_mode": "okf"}
    )
    assert rejected_okf.status_code == 409
    assert rejected_okf.json()["detail"]["code"] == "KNOWLEDGE_MODE_UNAVAILABLE"
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"okf_enabled": True, "okf_sync_enabled": True}
    )
    selected_rag = client.patch(
        "/memories/settings", headers=headers, json={"knowledge_mode": "rag"}
    )
    assert selected_rag.status_code == 200
    assert selected_rag.json()["knowledge_mode"] == "rag"

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
    assert client.get("/memories/settings", headers=headers).json()["version"] == 3
    edited = client.patch(
        f"/memories/{memory_id}",
        headers=headers,
        json={"content": "I prefer jasmine tea"},
    )
    assert edited.status_code == 200, edited.text
    memory_id = edited.json()["id"]
    assert client.get("/memories/settings", headers=headers).json()["version"] == 4
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
    assert excluded.json()["version"] == 5
    assert excluded.json() == {
        "enabled": False,
        "knowledge_mode": "rag",
        "okf_available": True,
        "timezone": "Asia/Kolkata",
        "locale": "en",
        "version": 5,
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
        client.patch(
            f"/memories/{memory_id}",
            headers=headers,
            json={"content": "editing while disabled must be rejected"},
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
    assert client.get("/memories/settings", headers=headers).json()["version"] == 6
    assert client.get(f"/memories/{memory_id}", headers=headers).status_code == 404


def test_memory_update_preserves_source_session_and_enqueues_okf(resource_client) -> None:
    client, settings, emails = resource_client
    settings = settings.model_copy(update={"okf_enabled": True, "okf_sync_enabled": True})
    client.app.state.settings = settings
    email = _email("phase6-memory-okf-update")
    emails.add(email)
    _register(client, email)
    tokens = _login(client, email, "phase6-memory-okf-update-device")
    headers = _auth(tokens)

    source_session_id, old_memory_id = asyncio.run(_create_voice_sourced_memory(settings, email))
    edited = client.patch(
        f"/memories/{old_memory_id}",
        headers=headers,
        json={
            "subject": "Project Orion",
            "predicate": "uses",
            "object_json": {"service": "PostgreSQL"},
        },
    )

    assert edited.status_code == 200, edited.text
    new_memory_id = uuid.UUID(edited.json()["id"])
    assert edited.json()["supersedes_id"] == str(old_memory_id)
    asyncio.run(
        _assert_memory_update_kept_source_and_enqueued_sync(
            settings,
            email,
            source_session_id,
            old_memory_id,
            new_memory_id,
        )
    )


def test_explicit_extraction_job_is_idempotent_and_owner_scoped(resource_client) -> None:
    _client, settings, emails = resource_client
    email = _email("phase6-worker")
    emails.add(email)
    asyncio.run(_run_extraction_job(_memory_settings(settings), email))


def test_optional_retrieval_failure_leaves_database_session_usable(
    resource_client, monkeypatch
) -> None:
    _client, settings, _emails = resource_client

    async def fail_fts(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("synthetic optional retrieval failure")

    async def check() -> None:
        import app.memory.retrieval as retrieval

        monkeypatch.setattr(retrieval, "fts_retrieve", fail_fts)
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await MemoryRetrievalService(_memory_settings(settings)).retrieve(
                session,
                user_id=uuid.uuid4(),
                query="where do I work?",
            )
            assert result.status == "degraded"
            assert result.provider_error == "memory_database_error"
            await session.execute(select(User).limit(1))
            await session.commit()
        await engine.dispose()

    asyncio.run(check())


def test_memory_version_is_once_per_created_or_superseding_write(resource_client) -> None:
    _client, settings, emails = resource_client
    email = _email("phase6-lifecycle")
    emails.add(email)

    async def run() -> None:
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=email,
                    password_hash="phase6-lifecycle-test-only",
                    memory_enabled=True,
                )
            )
            await session.commit()
            okf_settings = settings.model_copy(
                update={"okf_enabled": True, "okf_sync_enabled": True}
            )
            writer = MemoryWriter(okf_settings)
            first_candidate = ExtractionCandidate(
                content="I prefer green tea",
                memory_type=MemoryType.PREFERENCE,
                subject="user",
                predicate="beverage",
                object_json={"value": "green tea"},
                confidence=1.0,
                salience=1.0,
            )
            first, created = await writer.write_candidate(
                session,
                user_id=user_id,
                candidate=first_candidate,
                source_kind=MemorySourceKind.MANUAL_API,
            )
            assert created
            await session.commit()
            owner = await session.get(User, user_id)
            assert owner is not None and owner.memory_version == 1
            assert owner.memory_generation == 0

            repository = MemoryRepository()
            reembed, enqueued = await repository.enqueue_reembed_memory(
                session,
                user_id=user_id,
                memory_id=first.id,
                model_version="test-reembed-model",
                policy_version="phase6-reembed-test",
            )
            replay_job, replay_enqueued = await repository.enqueue_reembed_memory(
                session,
                user_id=user_id,
                memory_id=first.id,
                model_version="test-reembed-model",
                policy_version="phase6-reembed-test",
            )
            assert enqueued and not replay_enqueued and replay_job.id == reembed.id
            await session.commit()

            replay, replay_created = await writer.write_candidate(
                session,
                user_id=user_id,
                candidate=first_candidate,
                source_kind=MemorySourceKind.MANUAL_API,
            )
            assert replay.id == first.id and not replay_created
            await session.commit()
            owner = await session.get(User, user_id)
            assert owner is not None and owner.memory_version == 1
            assert owner.memory_generation == 0

            second, second_created = await writer.write_candidate(
                session,
                user_id=user_id,
                candidate=ExtractionCandidate(
                    content="I prefer jasmine tea",
                    memory_type=MemoryType.PREFERENCE,
                    subject="user",
                    predicate="beverage",
                    object_json={"value": "jasmine tea"},
                    confidence=1.0,
                    salience=1.0,
                ),
                source_kind=MemorySourceKind.MANUAL_API,
            )
            assert second_created and second.supersedes_id == first.id
            await session.commit()
            owner = await session.get(User, user_id)
            assert owner is not None and owner.memory_version == 2
            assert owner.memory_generation == 1
        await engine.dispose()

    asyncio.run(run())


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
            # This integration database can contain legitimate queued work
            # from physical validation. Make the isolated fixture oldest so
            # one worker iteration deterministically claims this exact job.
            available_at=now - timedelta(days=3650),
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
        stored_user = await session.get(User, user_id)
        stored_job = await session.get(MemoryJob, job.id)
        assert memory is not None
        assert memory.content == "I work remotely"
        assert memory.source_message_id == message.id
        assert memory.source_turn_id == turn.id
        assert memory.source_session_id == voice_session.id
        assert stored_user is not None and stored_user.memory_version == 1
        assert stored_job is not None and stored_job.status == "completed"
    await engine.dispose()


def test_excluded_session_source_is_filtered_from_rag() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run RAG lifecycle integration checks.")
    settings = _memory_settings()
    if not settings.database_url:
        pytest.skip("Database is not configured.")
    email = _email("phase6-rag-excluded-source")

    async def run() -> None:
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            async with factory() as session:
                user = User(
                    id=user_id,
                    email=email,
                    password_hash=hash_password("excluded-source-test-password"),
                    memory_enabled=True,
                )
                device = Device(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    device_identifier=f"excluded-{uuid.uuid4().hex}",
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
                    client_metadata={"memory_excluded": False},
                    status="completed",
                    started_at=now,
                    last_activity_at=now,
                    ended_at=now,
                )
                session.add(user)
                await session.flush()
                session.add(device)
                await session.flush()
                session.add(auth_session)
                await session.flush()
                session.add(voice_session)
                await session.flush()
                memory, created = await MemoryWriter(settings).write_candidate(
                    session,
                    user_id=user_id,
                    candidate=ExtractionCandidate(
                        content="My hidden orchard is called Windfall Grove.",
                        memory_type=MemoryType.FACT,
                        subject="user",
                        predicate="orchard",
                        object_json={"value": "Windfall Grove"},
                        confidence=1.0,
                        salience=1.0,
                    ),
                    source_kind=MemorySourceKind.MANUAL_API,
                    source_session_id=voice_session.id,
                )
                assert created
                await session.commit()
                memory_id = memory.id

                retrieval = MemoryRetrievalService(settings)
                before = await retrieval.retrieve(
                    session, user_id=user_id, query="hidden orchard", now=now
                )
                assert memory_id in {item.memory_id for item in before.memories}

                voice_session.client_metadata = {"memory_excluded": True}
                await OkfLifecycleService(
                    policy_version=settings.okf_policy_version,
                    sync_enabled=False,
                ).exclude_session(session, user_id=user_id, session_id=voice_session.id)
                await session.commit()

                after = await retrieval.retrieve(
                    session, user_id=user_id, query="hidden orchard", now=now
                )
                assert memory_id not in {item.memory_id for item in after.memories}
        finally:
            async with factory() as session:
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_rag_fts_matches_reordered_query_terms_without_matching_unrelated_project() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run RAG lexical integration checks.")
    settings = _memory_settings()
    if not settings.database_url:
        pytest.skip("Database is not configured.")
    email = _email("phase6-rag-reordered-fts")

    async def run() -> None:
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            async with factory() as session:
                user = User(
                    id=user_id,
                    email=email,
                    password_hash=hash_password("reordered-fts-test-password"),
                    memory_enabled=True,
                )
                device = Device(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    device_identifier=f"fts-{uuid.uuid4().hex}",
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
                    client_metadata={"memory_excluded": False},
                    status="completed",
                    started_at=now,
                    last_activity_at=now,
                    ended_at=now,
                )
                session.add(user)
                await session.flush()
                session.add(device)
                await session.flush()
                session.add(auth_session)
                await session.flush()
                session.add(voice_session)
                await session.flush()
                expected, _ = await MemoryWriter(settings).write_candidate(
                    session,
                    user_id=user_id,
                    candidate=ExtractionCandidate(
                        content="My Willow Beacon project uses the FastAPI framework.",
                        memory_type=MemoryType.PROJECT,
                        subject="Willow Beacon",
                        predicate="framework",
                        object_json={"value": "FastAPI"},
                        confidence=1.0,
                        salience=1.0,
                    ),
                    source_kind=MemorySourceKind.MANUAL_API,
                    source_session_id=voice_session.id,
                )
                unrelated, _ = await MemoryWriter(settings).write_candidate(
                    session,
                    user_id=user_id,
                    candidate=ExtractionCandidate(
                        content="My Maple Harbor project uses the Flask framework.",
                        memory_type=MemoryType.PROJECT,
                        subject="Maple Harbor",
                        predicate="framework",
                        object_json={"value": "Flask"},
                        confidence=1.0,
                        salience=1.0,
                    ),
                    source_kind=MemorySourceKind.MANUAL_API,
                    source_session_id=voice_session.id,
                )
                await session.flush()
                plan = build_memory_query_plan(
                    "Which framework am I using for my Willow Beacon project?", now=now
                )
                candidates = await fts_retrieve(
                    session,
                    user_id=user_id,
                    plan=plan,
                    limit=8,
                )
                candidate_ids = {item.memory_id for item in candidates}
                assert expected.id in candidate_ids
                assert unrelated.id not in candidate_ids
        finally:
            async with factory() as session:
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
