from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import from_url
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.models import ConversationTurn, User, VoiceSession
from app.services.voice_registry import VoiceRegistry, VoiceRegistryOwner
from app.websocket.binary import encode_pcm_frame

pytestmark = pytest.mark.integration


def _email(label: str) -> str:
    return f"phase3-{label}-{uuid.uuid4().hex}@example.test"


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _cleanup(settings: Settings, emails: set[str]) -> None:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_ids = list(
            (await session.scalars(select(User.id).where(User.email.in_(emails)))).all()
        )
        if user_ids:
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
    await engine.dispose()


@pytest.fixture
def voice_client():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run Phase 3 integration checks.")

    settings = Settings(
        jwt_secret_key="phase3-integration-secret-key-do-not-use-in-production",
        access_token_expire_minutes=15,
        refresh_token_expire_days=1,
    )
    emails: set[str] = set()
    with TestClient(create_app(settings=settings)) as client:
        readiness = client.get("/ready")
        if readiness.status_code != 200:
            pytest.skip(f"Infrastructure unavailable: {readiness.json()}")
        yield client, settings, emails
    asyncio.run(_cleanup(settings, emails))


def _create_account(client: TestClient, email: str, device: str) -> dict:
    registration = client.post(
        "/auth/register",
        json={"email": email, "password": "phase3-correct-password"},
    )
    assert registration.status_code == 201, registration.text
    login = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "phase3-correct-password",
            "device_identifier": device,
            "platform": "android",
        },
    )
    assert login.status_code == 200, login.text
    return login.json()


def _session_start_message() -> dict:
    return {
        "type": "client.session.start",
        "protocol_version": 1,
        "audio": {
            "sample_rate_hz": 16000,
            "channels": 1,
            "frame_samples": 320,
            "frame_bytes": 640,
        },
        "client_metadata": {"client_version": "phase3-test", "platform": "android"},
    }


def test_voice_gateway_streams_binary_audio_and_persists_metadata(voice_client) -> None:
    client, settings, emails = voice_client
    email = _email("binary-flow")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-binary-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        ready = socket.receive_json()
        assert ready["type"] == "server.session.ready", ready
        session_id = ready["session_id"]

        socket.send_json({"type": "client.turn.start"})
        turn_ready = socket.receive_json()
        assert turn_ready["type"] == "server.turn.ready"

        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=0,
                client_timestamp_ms=1000,
                payload=b"\x00" * settings.voice_frame_bytes,
            )
        )
        socket.send_json(
            {
                "type": "client.audio.commit",
                "last_sequence_no": 0,
                "frame_count": 1,
                "byte_count": settings.voice_frame_bytes,
                "duration_ms": 20,
            }
        )
        completed = socket.receive_json()
        assert completed["type"] == "server.turn.completed"
        assert completed["frame_count"] == 1
        assert completed["byte_count"] == settings.voice_frame_bytes

        socket.send_json({"type": "client.ping", "client_timestamp_ms": 1010})
        pong = socket.receive_json()
        assert pong["type"] == "server.pong"

        socket.send_json({"type": "client.session.end", "reason": "test_complete"})
        assert socket.receive_json()["type"] == "server.session.ending"
        assert socket.receive_json()["type"] == "server.session.ended"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()

    async def read_metadata() -> tuple[str, int, int, str, int, int | None]:
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            for _ in range(20):
                async with factory() as session:
                    voice_session = await session.get(VoiceSession, uuid.UUID(session_id))
                    turn = await session.scalar(
                        select(ConversationTurn).where(
                            ConversationTurn.session_id == uuid.UUID(session_id)
                        )
                    )
                    if voice_session is not None and turn is not None:
                        result = (
                            voice_session.status,
                            voice_session.total_frames,
                            voice_session.total_bytes,
                            turn.status,
                            turn.frame_count,
                            turn.last_sequence,
                        )
                        if voice_session.status != "active":
                            return result
                await asyncio.sleep(0.05)
            raise AssertionError("voice session was not finalized")
        finally:
            await engine.dispose()

    status, total_frames, total_bytes, turn_status, frame_count, last_sequence = asyncio.run(
        read_metadata()
    )
    assert status == "completed"
    assert total_frames == 1
    assert total_bytes == settings.voice_frame_bytes
    assert turn_status == "committed"
    assert frame_count == 1
    assert last_sequence == 0


def test_voice_gateway_rejects_sequence_gap_without_replaying_audio(voice_client) -> None:
    client, settings, emails = voice_client
    email = _email("sequence-gap")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-gap-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        session_ready = socket.receive_json()
        assert session_ready["type"] == "server.session.ready", session_ready
        socket.send_json({"type": "client.turn.start"})
        assert socket.receive_json()["type"] == "server.turn.ready"
        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=1,
                client_timestamp_ms=1000,
                payload=b"\x00" * settings.voice_frame_bytes,
            )
        )
        error = socket.receive_json()
        assert error["type"] == "server.error"
        assert error["code"] == "sequence_gap"
        with pytest.raises(WebSocketDisconnect) as disconnect:
            socket.receive_json()
        assert disconnect.value.code == 1002


def test_voice_gateway_cancels_active_turn_and_closes_cleanly(voice_client) -> None:
    client, _, emails = voice_client
    email = _email("cancel")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-cancel-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        assert socket.receive_json()["type"] == "server.session.ready"
        socket.send_json({"type": "client.turn.start"})
        turn_ready = socket.receive_json()
        assert turn_ready["type"] == "server.turn.ready"
        socket.send_json(
            {
                "type": "client.response.cancel",
                "response_id": turn_ready["response_id"],
                "reason": "test_cancel",
            }
        )
        cancelled = socket.receive_json()
        assert cancelled["type"] == "response.cancelled"
        assert cancelled["turn_id"] == turn_ready["turn_id"]

        socket.send_json({"type": "client.session.end", "reason": "test_complete"})
        assert socket.receive_json()["type"] == "server.session.ending"
        assert socket.receive_json()["type"] == "server.session.ended"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_voice_gateway_resume_is_scoped_to_authenticated_user(voice_client) -> None:
    client, _, emails = voice_client
    email_a = _email("resume-a")
    email_b = _email("resume-b")
    emails.update({email_a, email_b})
    tokens_a = _create_account(client, email_a, "phase3-resume-device-a")
    tokens_b = _create_account(client, email_b, "phase3-resume-device-b")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens_a["access_token"])) as socket_a:
        socket_a.send_json(_session_start_message())
        session_ready = socket_a.receive_json()
        assert session_ready["type"] == "server.session.ready", session_ready
        session_id = session_ready["session_id"]

    with client.websocket_connect("/v1/voice", headers=_auth(tokens_b["access_token"])) as socket_b:
        message = _session_start_message()
        message["resume_session_id"] = session_id
        socket_b.send_json(message)
        error = socket_b.receive_json()
        assert error["type"] == "server.error"
        assert error["code"] == "session_not_available"
        with pytest.raises(WebSocketDisconnect) as disconnect:
            socket_b.receive_json()
        assert disconnect.value.code == 1008


def test_voice_gateway_rejects_missing_or_invalid_credentials(voice_client) -> None:
    client, _, _ = voice_client

    with pytest.raises(WebSocketDisconnect) as missing:
        with client.websocket_connect("/v1/voice"):
            pass
    assert missing.value.code == 1008

    with pytest.raises(WebSocketDisconnect) as invalid:
        with client.websocket_connect(
            "/v1/voice", headers={"Authorization": "Bearer invalid-token"}
        ):
            pass
    assert invalid.value.code == 1008


def test_voice_gateway_heartbeat_keeps_healthy_connection_open(voice_client) -> None:
    client, settings, emails = voice_client
    settings.voice_heartbeat_interval_seconds = 1
    settings.voice_heartbeat_timeout_seconds = 3
    settings.voice_idle_timeout_seconds = 30
    email = _email("heartbeat-healthy")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-heartbeat-healthy-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        ready = socket.receive_json()
        assert ready["type"] == "server.session.ready"
        assert ready["heartbeat_interval_seconds"] == 1

        for timestamp in (1000, 2000):
            socket.send_json({"type": "client.ping", "client_timestamp_ms": timestamp})
            pong = socket.receive_json()
            assert pong["type"] == "server.pong"
            assert pong["client_timestamp_ms"] == timestamp
            time.sleep(1.1)

        socket.send_json({"type": "client.session.end", "reason": "heartbeat_test_complete"})
        assert socket.receive_json()["type"] == "server.session.ending"
        assert socket.receive_json()["type"] == "server.session.ended"


def test_voice_gateway_heartbeat_timeout_closes_stale_connection(voice_client) -> None:
    client, settings, emails = voice_client
    settings.voice_heartbeat_interval_seconds = 1
    settings.voice_heartbeat_timeout_seconds = 2
    settings.voice_idle_timeout_seconds = 30
    email = _email("heartbeat-stale")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-heartbeat-stale-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        assert socket.receive_json()["type"] == "server.session.ready"
        time.sleep(3.5)
        error = socket.receive_json()
        assert error["type"] == "server.error"
        assert error["code"] == "voice_heartbeat_timeout"
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_voice_gateway_cancellation_marker_is_released_after_disconnect(voice_client) -> None:
    client, settings, emails = voice_client
    email = _email("cancellation-cleanup")
    emails.add(email)
    tokens = _create_account(client, email, "phase3-cancellation-cleanup-device")

    with client.websocket_connect("/v1/voice", headers=_auth(tokens["access_token"])) as socket:
        socket.send_json(_session_start_message())
        ready = socket.receive_json()
        assert ready["type"] == "server.session.ready"
        session_id = ready["session_id"]
        socket.send_json({"type": "client.turn.start"})
        turn_ready = socket.receive_json()
        assert turn_ready["type"] == "server.turn.ready"
        socket.send_json(
            {
                "type": "client.response.cancel",
                "response_id": turn_ready["response_id"],
                "reason": "cleanup_test",
            }
        )
        cancelled = socket.receive_json()
        assert cancelled["type"] == "response.cancelled"
        socket.send_json({"type": "client.session.end", "reason": "cleanup_test"})
        assert socket.receive_json()["type"] == "server.session.ending"
        assert socket.receive_json()["type"] == "server.session.ended"

    async def remaining_markers() -> list[str]:
        redis = from_url(settings.redis_dsn, decode_responses=True)
        try:
            return [
                key
                async for key in redis.scan_iter(match=f"voice:session:{session_id}:cancelled:*")
            ]
        finally:
            await redis.aclose()

    assert asyncio.run(remaining_markers()) == []


def test_voice_registry_global_cleanup_is_owner_scoped_and_ttl_bounded(voice_client) -> None:
    client, settings, emails = voice_client
    del emails
    owners = [
        VoiceRegistryOwner(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()) for _ in range(3)
    ]
    session_ids = [uuid.uuid4() for _ in owners]
    turn_ids = [uuid.uuid4() for _ in owners]
    response_ids = [uuid.uuid4() for _ in owners]

    async def exercise() -> None:
        redis = from_url(settings.redis_dsn, decode_responses=True)
        registry = VoiceRegistry(redis, ttl_seconds=30)
        try:
            for owner, session_id in zip(owners, session_ids, strict=True):
                assert await registry.acquire(owner, session_id)
                await registry.set_turn(owner, session_id, uuid.uuid4(), uuid.uuid4())

            await registry.set_turn(owners[0], session_ids[0], turn_ids[0], response_ids[0])
            assert await registry.cancel_response(owners[0], session_ids[0], response_ids[0])

            await registry.release(owners[0], session_ids[0])
            assert not await redis.exists(registry._session_key(session_ids[0]))
            assert not await redis.exists(registry._turn_key(session_ids[0]))
            assert not await redis.exists(registry._response_key(session_ids[0]))
            assert not await redis.exists(registry._cancel_key(session_ids[0], response_ids[0]))
            assert await redis.exists(registry._session_key(session_ids[1]))
            assert await redis.exists(registry._session_key(session_ids[2]))

            await registry.release(owners[0], session_ids[1])
            assert await redis.exists(registry._session_key(session_ids[1]))

            await registry.release(owners[1], session_ids[1])
            await registry.release(owners[2], session_ids[2])
            for session_id in session_ids:
                assert not await redis.exists(registry._session_key(session_id))

            stale_owner = VoiceRegistryOwner(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
            stale_session = uuid.uuid4()
            stale_turn = uuid.uuid4()
            stale_response = uuid.uuid4()
            expiring_registry = VoiceRegistry(redis, ttl_seconds=5)
            assert await expiring_registry.acquire(stale_owner, stale_session)
            await expiring_registry.set_turn(stale_owner, stale_session, stale_turn, stale_response)
            assert await expiring_registry.cancel_response(
                stale_owner, stale_session, stale_response
            )
            assert await redis.ttl(expiring_registry._session_key(stale_session)) > 0
            await asyncio.sleep(6)
            assert not await redis.exists(expiring_registry._device_key(stale_owner.device_id))
            assert not await redis.exists(expiring_registry._session_key(stale_session))
            assert not await redis.exists(expiring_registry._turn_key(stale_session))
            assert not await redis.exists(expiring_registry._response_key(stale_session))
            assert not await redis.exists(
                expiring_registry._cancel_key(stale_session, stale_response)
            )
        finally:
            for owner, session_id in zip(owners, session_ids, strict=True):
                await registry.release(owner, session_id)
            await redis.aclose()

    asyncio.run(exercise())
