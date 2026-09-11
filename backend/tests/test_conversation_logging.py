from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import AuthSession, ConversationTurn, Device, Message, User, VoiceSession
from app.services.conversation_logging import ConversationLogger, should_persist_conversation

pytestmark = pytest.mark.integration


async def _database_factory(settings: Settings):
    if not settings.database_url:
        pytest.skip("DATABASE_URL is not configured")
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(select(1))
    except Exception as error:  # noqa: BLE001 - integration prerequisite
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {error}")
    return engine, factory


async def _create_voice_graph(factory, *, label: str, device_kind: str = "physical"):
    now = datetime.now(UTC)
    async with factory() as session:
        user = User(
            email=f"conversation-log-{label}-{uuid.uuid4().hex}@example.test",
            password_hash="not-a-real-password-hash",
            timezone="Asia/Kolkata",
        )
        session.add(user)
        await session.flush()
        device = Device(
            user_id=user.id,
            device_identifier=f"conversation-device-{uuid.uuid4().hex}",
            platform="android",
            device_kind=device_kind,
        )
        session.add(device)
        await session.flush()
        auth_session = AuthSession(
            user_id=user.id,
            device_id=device.id,
            refresh_token_hash=uuid.uuid4().hex,
            expires_at=now + timedelta(days=1),
        )
        session.add(auth_session)
        await session.flush()
        voice_session = VoiceSession(
            user_id=user.id,
            device_id=device.id,
            auth_session_id=auth_session.id,
            protocol_version=1,
        )
        session.add(voice_session)
        await session.flush()
        turn = ConversationTurn(
            session_id=voice_session.id,
            user_id=user.id,
            turn_number=1,
            status="committed",
            started_at=now,
            committed_at=now,
            ended_at=now,
        )
        session.add(turn)
        await session.flush()
        session.add_all(
            [
                Message(
                    turn_id=turn.id,
                    user_id=user.id,
                    role="user",
                    content="What time is it?",
                    sequence_no=0,
                ),
                Message(
                    turn_id=turn.id,
                    user_id=user.id,
                    role="assistant",
                    content="It is noon.",
                    content_json={"authorization": "must-not-be-written"},
                    sequence_no=0,
                ),
            ]
        )
        await session.commit()
        return user.id, device.id, voice_session.id, turn.id


async def _create_second_session(factory, graph):
    user_id, device_id, _, _ = graph
    now = datetime.now(UTC)
    async with factory() as session:
        auth_session = await session.scalar(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.device_id == device_id,
            )
        )
        assert auth_session is not None
        voice_session = VoiceSession(
            user_id=user_id,
            device_id=device_id,
            auth_session_id=auth_session.id,
            protocol_version=1,
        )
        session.add(voice_session)
        await session.flush()
        turn = ConversationTurn(
            session_id=voice_session.id,
            user_id=user_id,
            turn_number=1,
            status="committed",
            started_at=now,
            committed_at=now,
            ended_at=now,
        )
        session.add(turn)
        await session.flush()
        session.add(
            Message(
                turn_id=turn.id,
                user_id=user_id,
                role="user",
                content="What is my next appointment?",
                sequence_no=0,
            )
        )
        await session.commit()
        return user_id, device_id, voice_session.id, turn.id


async def _cleanup(factory, user_ids: set[uuid.UUID]) -> None:
    async with factory() as session:
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.commit()


@pytest.mark.asyncio
async def test_physical_sessions_are_separate_and_replay_idempotent(tmp_path) -> None:
    settings = Settings(conversation_logging_enabled=True, conversation_log_dir=str(tmp_path))
    engine, factory = await _database_factory(settings)
    user_ids: set[uuid.UUID] = set()
    try:
        first = await _create_voice_graph(factory, label="physical-a")
        user_id, device_id, session_a, turn_a = first
        second = await _create_second_session(factory, first)
        user_ids.add(user_id)
        logger = ConversationLogger(root_dir=tmp_path)

        async with factory() as session:
            assert await logger.persist_turn(
                session,
                user_id=user_id,
                device_id=device_id,
                session_id=session_a,
                turn_id=turn_a,
                enabled=True,
                timing_payload={
                    "timings": {
                        "turn_started_at": "2026-09-11T19:24:15.102134Z",
                        "turn_completed_at": "2026-09-11T19:24:20.102134Z",
                    },
                    "latency_ms": {"turn_total": 5000.0},
                    "tts": {
                        "sample_rate": 24000,
                        "channels": 1,
                        "encoding": "pcm16",
                        "prebuffer_ms": 200,
                        "prebuffer_bytes": 9600,
                        "sequence_gaps": 0,
                        "duplicate_frames": 0,
                        "stale_frames": None,
                        "underrun_delta": None,
                    },
                },
            )
            assert await logger.persist_turn(
                session,
                user_id=user_id,
                device_id=device_id,
                session_id=session_a,
                turn_id=turn_a,
                enabled=True,
            )

        path = tmp_path / str(user_id) / f"{session_a}.jsonl"
        assert path.exists()
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1
        first_record = json.loads(path.read_text(encoding="utf-8"))
        assert first_record["timings"]["turn_started_at"].endswith("Z")
        assert first_record["latency_ms"]["turn_total"] == 5000.0
        assert first_record["tts"]["underrun_delta"] is None
        async with factory() as session:
            assert await logger.persist_turn(
                session,
                user_id=second[0],
                device_id=second[1],
                session_id=second[2],
                turn_id=second[3],
                enabled=True,
            )
        assert (tmp_path / str(user_id) / f"{second[2]}.jsonl").exists()
    finally:
        await _cleanup(factory, user_ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_synthetic_revoked_and_cross_user_sessions_never_write(tmp_path) -> None:
    settings = Settings(conversation_logging_enabled=True, conversation_log_dir=str(tmp_path))
    engine, factory = await _database_factory(settings)
    user_ids: set[uuid.UUID] = set()
    try:
        physical = await _create_voice_graph(factory, label="physical")
        synthetic = await _create_voice_graph(factory, label="synthetic", device_kind="synthetic")
        revoked = await _create_voice_graph(factory, label="revoked")
        user_ids.update(item[0] for item in (physical, synthetic, revoked))
        async with factory() as session:
            device = await session.get(Device, revoked[1])
            assert device is not None
            device.revoked_at = datetime.now(UTC)
            await session.commit()

        logger = ConversationLogger(root_dir=tmp_path)
        for graph in (synthetic, revoked):
            async with factory() as session:
                assert not await logger.persist_turn(
                    session,
                    user_id=graph[0],
                    device_id=graph[1],
                    session_id=graph[2],
                    turn_id=graph[3],
                    enabled=True,
                )

        async with factory() as session:
            assert not await logger.persist_turn(
                session,
                user_id=synthetic[0],
                device_id=physical[1],
                session_id=physical[2],
                turn_id=physical[3],
                enabled=True,
            )
        assert list(tmp_path.rglob("*.jsonl")) == []
    finally:
        await _cleanup(factory, user_ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_session_reconnect_replaces_turn_and_new_session_gets_new_file(tmp_path) -> None:
    settings = Settings(conversation_logging_enabled=True, conversation_log_dir=str(tmp_path))
    engine, factory = await _database_factory(settings)
    user_ids: set[uuid.UUID] = set()
    try:
        first = await _create_voice_graph(factory, label="reconnect-a")
        second = await _create_voice_graph(factory, label="reconnect-b")
        user_ids.update({first[0], second[0]})
        logger = ConversationLogger(root_dir=tmp_path)
        for graph in (first, first):
            async with factory() as session:
                assert await logger.persist_turn(
                    session,
                    user_id=graph[0],
                    device_id=graph[1],
                    session_id=graph[2],
                    turn_id=graph[3],
                    enabled=True,
                )
        async with factory() as session:
            assert await logger.persist_turn(
                session,
                user_id=second[0],
                device_id=second[1],
                session_id=second[2],
                turn_id=second[3],
                enabled=True,
            )

        first_path = tmp_path / str(first[0]) / f"{first[2]}.jsonl"
        second_path = tmp_path / str(second[0]) / f"{second[2]}.jsonl"
        assert first_path.exists()
        assert second_path.exists()
        assert len(first_path.read_text(encoding="utf-8").splitlines()) == 1
        record = json.loads(first_path.read_text(encoding="utf-8"))
        assert record["user_id"] == str(first[0])
        assert record["session_id"] == str(first[2])
        assert "authorization" not in json.dumps(record).lower()
    finally:
        await _cleanup(factory, user_ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_eligibility_requires_global_switch_and_owned_physical_session(tmp_path) -> None:
    settings = Settings(conversation_logging_enabled=True, conversation_log_dir=str(tmp_path))
    engine, factory = await _database_factory(settings)
    user_ids: set[uuid.UUID] = set()
    try:
        graph = await _create_voice_graph(factory, label="eligibility")
        user_ids.add(graph[0])
        async with factory() as session:
            assert await should_persist_conversation(
                session,
                user_id=graph[0],
                device_id=graph[1],
                session_id=graph[2],
                enabled=True,
            )
            assert not await should_persist_conversation(
                session,
                user_id=graph[0],
                device_id=graph[1],
                session_id=graph[2],
                enabled=False,
            )
    finally:
        await _cleanup(factory, user_ids)
        await engine.dispose()
