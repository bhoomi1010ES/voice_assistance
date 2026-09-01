from __future__ import annotations

import asyncio
import os
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.main import create_app
from app.models import ConversationTurn, User
from app.stt.service import STTService
from app.websocket.binary import encode_pcm_frame

pytestmark = pytest.mark.integration


def _email(label: str) -> str:
    return f"phase4-stt-{label}-{uuid.uuid4().hex}@example.test"


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


class FakeGatewayModel:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds

    def transcribe(self, audio, **options):
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        language = options.get("language") or "en"
        return iter([SimpleNamespace(text=" hello world")]), SimpleNamespace(language=language)


def _session_start(language: str | None = "en") -> dict:
    return {
        "type": "client.session.start",
        "protocol_version": 1,
        "audio": {
            "sample_rate_hz": 16000,
            "channels": 1,
            "frame_samples": 320,
            "frame_bytes": 640,
        },
        "stt": {"enabled": True, "language": language},
    }


def _create_account(client: TestClient, email: str) -> dict:
    registration = client.post(
        "/auth/register",
        json={"email": email, "password": "phase4-correct-password"},
    )
    assert registration.status_code == 201, registration.text
    login = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "phase4-correct-password",
            "device_identifier": "phase4-stt-device",
            "platform": "android",
        },
    )
    assert login.status_code == 200, login.text
    return login.json()


@pytest.fixture
def stt_client(tmp_path, request):
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run STT gateway checks.")

    settings = Settings(
        jwt_secret_key="phase4-stt-integration-secret-key-do-not-use",
        access_token_expire_minutes=15,
        refresh_token_expire_days=1,
        stt_model_path=str(tmp_path),
        stt_partial_interval_seconds=0.01,
        stt_partial_window_seconds=30,
        stt_timeout=5,
        stt_workers=1,
    )
    for filename in STTService._REQUIRED_MODEL_FILES:
        (tmp_path / filename).write_bytes(b"test")
    marker = request.node.get_closest_marker("slow_stt_gateway")
    delay_seconds = float(marker.args[0]) if marker and marker.args else 0
    service = STTService(
        settings,
        model_factory=lambda *args, **kwargs: FakeGatewayModel(delay_seconds=delay_seconds),
    )
    emails: set[str] = set()
    with TestClient(create_app(settings=settings, stt_service=service)) as client:
        readiness = client.get("/ready")
        if readiness.status_code != 200:
            pytest.skip(f"Infrastructure unavailable: {readiness.json()}")
        yield client, settings, emails
    asyncio.run(_cleanup(settings, emails))


def test_stt_gateway_emits_partial_final_and_persists_metadata(stt_client) -> None:
    client, settings, emails = stt_client
    email = _email("transcript")
    emails.add(email)
    tokens = _create_account(client, email)

    with client.websocket_connect(
        "/v1/voice", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ) as socket:
        socket.send_json(_session_start())
        ready = socket.receive_json()
        assert ready["type"] == "server.session.ready"
        assert ready["stt"] == {"enabled": True, "language": "en"}
        session_id = ready["session_id"]

        socket.send_json({"type": "client.turn.start"})
        turn_ready = socket.receive_json()
        assert turn_ready["type"] == "server.turn.ready"
        turn_id = turn_ready["turn_id"]
        response_id = turn_ready["response_id"]

        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=0,
                client_timestamp_ms=1000,
                payload=b"\x00\x10" * 320,
            )
        )
        partial = socket.receive_json()
        assert partial["type"] == "transcript.partial"
        assert partial["final"] is False
        assert partial["text"] == "hello world"
        assert partial["session_id"] == session_id
        assert partial["turn_id"] == turn_id
        assert partial["response_id"] == response_id

        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=1,
                client_timestamp_ms=1020,
                payload=b"\x00\x10" * 320,
            )
        )
        socket.send_json(
            {
                "type": "client.audio.commit",
                "last_sequence_no": 1,
                "frame_count": 2,
                "byte_count": 2 * settings.voice_frame_bytes,
                "duration_ms": 40,
            }
        )
        final = socket.receive_json()
        assert final["type"] == "transcript.final"
        assert final["final"] is True
        assert final["text"] == "hello world"
        assert final["session_id"] == session_id
        assert final["turn_id"] == turn_id
        assert final["response_id"] == response_id
        assert final["metrics"]["speech_end_to_final_transcript_ms"] >= 0

        completed = socket.receive_json()
        assert completed["type"] == "server.turn.completed"
        socket.send_json({"type": "client.session.end", "reason": "test_complete"})
        assert socket.receive_json()["type"] == "server.session.ending"
        assert socket.receive_json()["type"] == "server.session.ended"

    async def read_turn() -> ConversationTurn:
        engine = create_async_engine(settings.database_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return await session.scalar(
                    select(ConversationTurn).where(ConversationTurn.id == uuid.UUID(turn_id))
                )
        finally:
            await engine.dispose()

    turn = asyncio.run(read_turn())
    assert turn.status == "committed"
    assert turn.metadata_json["transcript"] == "hello world"
    assert turn.metadata_json["language"] == "en"
    assert turn.metadata_json["stt"]["audio_duration_ms"] == 40


def test_stt_gateway_cancellation_allows_next_turn(stt_client) -> None:
    client, _, emails = stt_client
    email = _email("cancel")
    emails.add(email)
    tokens = _create_account(client, email)

    with client.websocket_connect(
        "/v1/voice", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ) as socket:
        socket.send_json(_session_start())
        assert socket.receive_json()["type"] == "server.session.ready"
        socket.send_json({"type": "client.turn.start"})
        first = socket.receive_json()
        socket.send_json({"type": "client.response.cancel", "response_id": first["response_id"]})
        cancelled = socket.receive_json()
        assert cancelled["type"] == "response.cancelled"
        assert cancelled["turn_id"] == first["turn_id"]

        socket.send_json({"type": "client.turn.start"})
        second = socket.receive_json()
        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=0,
                client_timestamp_ms=2000,
                payload=b"\x00\x10" * 320,
            )
        )
        partial = socket.receive_json()
        assert partial["type"] == "transcript.partial"
        socket.send_json(
            {
                "type": "client.audio.commit",
                "last_sequence_no": 0,
                "frame_count": 1,
                "byte_count": 640,
                "duration_ms": 20,
            }
        )
        final = socket.receive_json()
        completed = socket.receive_json()
        assert final["type"] == "transcript.final"
        assert completed["type"] == "server.turn.completed"
        assert final["turn_id"] == second["turn_id"]
        assert final["turn_id"] != first["turn_id"]


@pytest.mark.slow_stt_gateway(1.5)
def test_stt_gateway_cancels_finalization_without_blocking_protocol(stt_client) -> None:
    client, settings, emails = stt_client
    email = _email("active-cancel")
    emails.add(email)
    tokens = _create_account(client, email)

    with client.websocket_connect(
        "/v1/voice", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ) as socket:
        socket.send_json(_session_start())
        assert socket.receive_json()["type"] == "server.session.ready"
        socket.send_json({"type": "client.turn.start"})
        turn = socket.receive_json()
        socket.send_bytes(
            encode_pcm_frame(
                sequence_no=0,
                client_timestamp_ms=3000,
                payload=b"\x00\x10" * 320,
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
        time.sleep(0.1)
        socket.send_json({"type": "client.response.cancel", "response_id": turn["response_id"]})
        cancelled = socket.receive_json()
        assert cancelled["type"] == "response.cancelled"
        assert cancelled["turn_id"] == turn["turn_id"]

        socket.send_json({"type": "client.turn.start"})
        next_turn = socket.receive_json()
        assert next_turn["type"] == "server.turn.ready"
        assert next_turn["turn_id"] != turn["turn_id"]
