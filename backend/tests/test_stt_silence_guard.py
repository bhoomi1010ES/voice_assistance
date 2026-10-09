"""Tests for server-side STT silence guard (QA-VOICE-001)."""

from __future__ import annotations

import asyncio
import gc
import math
import os
import struct
import time
import uuid
import warnings
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.llm.service import LLMService
from app.llm.types import LLMCapabilities, LLMEvent, LLMProviderInfo, LLMRequest
from app.main import create_app
from app.models import ConversationTurn, User
from app.stt.audio_energy import AudioEnergyResult, evaluate_pcm16_speech_energy
from app.stt.base import STTEmptyAudioError, STTEmptyTranscriptError
from app.stt.remote_engine import RemoteTranscriptionEngine
from app.stt.service import STTService
from app.websocket.binary import encode_pcm_frame


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "stt_engine": "remote",
        "stt_api_url": "https://stt.example.test/v1/audio/transcriptions",
        "stt_api_key": "test-api-key",
        "stt_api_model": "test-model",
        "stt_api_timeout_seconds": 2,
        "stt_api_connect_timeout_seconds": 1,
        "stt_silence_guard_enabled": True,
        "stt_silence_guard_min_rms": 25.0,
        "stt_silence_guard_min_peak": 100,
        "stt_silence_guard_window_ms": 100,
    }
    values.update(overrides)
    return Settings(**values)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


async def _noop_partial(_partial: Any) -> None:
    return None


def _sine_pcm(
    duration_s: float,
    amplitude: int = 1000,
    frequency_hz: float = 440.0,
    sample_rate_hz: int = 16_000,
) -> bytes:
    """Generate deterministic sine wave PCM16 audio."""
    sample_count = int(duration_s * sample_rate_hz)
    samples = [
        int(amplitude * math.sin(2 * math.pi * frequency_hz * i / sample_rate_hz))
        for i in range(sample_count)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


# ==============================================================================
# Unit Tests: evaluate_pcm16_speech_energy
# ==============================================================================


def test_audio_energy_digital_silence() -> None:
    # 5 seconds of pure digital zeros
    silence = b"\x00\x00" * 80_000
    result = evaluate_pcm16_speech_energy(silence, min_rms=25.0, min_peak=100)

    assert result.has_speech is False
    assert result.global_rms == 0.0
    assert result.peak_window_rms == 0.0
    assert result.peak_amplitude == 0
    assert result.sample_count == 80_000
    assert result.duration_ms == 5000.0


def test_audio_energy_empty_and_malformed() -> None:
    # Empty bytes
    empty = evaluate_pcm16_speech_energy(b"")
    assert empty.has_speech is False
    assert empty.sample_count == 0

    # Single byte (cannot form a 16-bit sample)
    single = evaluate_pcm16_speech_energy(b"\x00")
    assert single.has_speech is False
    assert single.sample_count == 0

    # Odd-length bytes (safely truncated to even length without error)
    odd = evaluate_pcm16_speech_energy(b"\x00\x10\x05")
    assert odd.sample_count == 1
    assert odd.duration_ms == 0.1


def test_audio_energy_ambient_noise_floor_rejected() -> None:
    # Low-level noise fluctuating between -15 and +15 (RMS ~ 8-10, peak = 15)
    noise_samples = [int(15 * math.sin(i)) for i in range(16_000)]
    noise_pcm = struct.pack(f"<{len(noise_samples)}h", *noise_samples)

    result = evaluate_pcm16_speech_energy(noise_pcm, min_rms=25.0, min_peak=100)
    assert result.has_speech is False
    assert result.global_rms < 25.0
    assert result.peak_window_rms < 25.0
    assert result.peak_amplitude < 100


def test_audio_energy_isolated_spike_does_not_trigger_speech() -> None:
    # 2 seconds of silence with a single isolated sample click (e.g. amplitude 50)
    samples = [0] * 32_000
    samples[16_000] = 50  # Isolated click below speech threshold
    pcm = struct.pack(f"<{len(samples)}h", *samples)

    result = evaluate_pcm16_speech_energy(pcm, min_rms=25.0, min_peak=100)
    assert result.has_speech is False


def test_audio_energy_quiet_speech_passes() -> None:
    # Synthetic quiet speech (1.0 second, amplitude 250, window RMS ~ 176)
    quiet_pcm = _sine_pcm(duration_s=1.0, amplitude=250, frequency_hz=300.0)

    result = evaluate_pcm16_speech_energy(quiet_pcm, min_rms=25.0, min_peak=100)
    assert result.has_speech is True
    assert result.peak_window_rms > 25.0
    assert result.peak_amplitude >= 100


def test_audio_energy_short_speech_in_long_turn_passes() -> None:
    # 5 seconds of silence, 0.3 seconds of quiet speech (amplitude 300), 5 seconds of silence
    silence_5s = b"\x00\x00" * (5 * 16_000)
    short_speech = _sine_pcm(duration_s=0.3, amplitude=300, frequency_hz=400.0)
    combined = silence_5s + short_speech + silence_5s

    result = evaluate_pcm16_speech_energy(combined, min_rms=25.0, min_peak=100)
    # Windowed RMS must capture the 300 ms burst even though global RMS across
    # 10.3 seconds is diluted!
    assert result.has_speech is True
    assert result.peak_window_rms > 25.0
    assert result.peak_amplitude >= 100


def test_audio_energy_normal_speech_passes() -> None:
    # Ordinary speech (amplitude 1500, window RMS > 1000)
    normal_pcm = _sine_pcm(duration_s=2.0, amplitude=1500, frequency_hz=440.0)

    result = evaluate_pcm16_speech_energy(normal_pcm, min_rms=25.0, min_peak=100)
    assert result.has_speech is True
    assert result.peak_window_rms > 500.0
    assert result.peak_amplitude >= 1000


# ==============================================================================
# Remote Engine Tests: Silence Guard Integration
# ==============================================================================


@pytest.mark.asyncio
async def test_remote_engine_silence_guard_rejects_silence_before_http() -> None:
    http_called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"text": "Thank you."}, request=request)

    settings = _settings(stt_silence_guard_enabled=True)
    engine = RemoteTranscriptionEngine(settings, transport=httpx.MockTransport(handler))

    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=_noop_partial,
    )
    # Push 3 seconds of digital silence
    await engine.push_audio(handle, b"\x00\x00" * 48_000, generation=0)

    with pytest.raises(STTEmptyTranscriptError, match="No speech detected"):
        await engine.finish_turn(handle, generation=1)

    # HTTP client must NOT be called
    assert http_called is False
    await engine.close()


@pytest.mark.asyncio
async def test_remote_engine_silence_guard_allows_speech_to_call_http() -> None:
    http_called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"text": "valid speech"}, request=request)

    settings = _settings(stt_silence_guard_enabled=True)
    engine = RemoteTranscriptionEngine(settings, transport=httpx.MockTransport(handler))

    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=_noop_partial,
    )
    # Push speech audio
    speech = _sine_pcm(duration_s=1.0, amplitude=500)
    await engine.push_audio(handle, speech, generation=0)

    result = await engine.finish_turn(handle, generation=1)
    assert result.text == "valid speech"
    assert http_called is True
    await engine.close()


@pytest.mark.asyncio
async def test_remote_engine_silence_guard_disabled_by_default() -> None:
    http_called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"text": "whisper hallucination"}, request=request)

    # stt_silence_guard_enabled=False (default behavior)
    settings = _settings(stt_silence_guard_enabled=False)
    engine = RemoteTranscriptionEngine(settings, transport=httpx.MockTransport(handler))

    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=_noop_partial,
    )
    # Push silence
    await engine.push_audio(handle, b"\x00\x00" * 32_000, generation=0)

    result = await engine.finish_turn(handle, generation=1)
    # When guard is disabled, audio is submitted to provider as before
    assert result.text == "whisper hallucination"
    assert http_called is True
    await engine.close()


# ==============================================================================
# Gateway WebSocket Integration Tests: Turn Rejection & Subsequent Turn Recovery
# ==============================================================================


class DeterministicLLMProvider:
    def __init__(self) -> None:
        self.stream_calls = 0
        self.closed = False

    async def initialize(self) -> LLMProviderInfo:
        return LLMProviderInfo(
            provider="deterministic_test",
            api_family="test",
            host="https://llm.invalid",
            configured_model="deterministic-test-model",
            capabilities=LLMCapabilities(streaming=True, text_generation=True, cancellation=True),
        )

    async def stream(self, request: LLMRequest, *, attempt: int = 1):
        self.stream_calls += 1
        values = {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "response_id": request.response_id,
            "provider": "deterministic_test",
            "configured_model": "deterministic-test-model",
            "attempt": attempt,
        }
        yield LLMEvent(
            event_type="request_started",
            monotonic_seconds=time.monotonic(),
            sequence=0,
            **values,
        )
        yield LLMEvent(
            event_type="text_delta",
            monotonic_seconds=time.monotonic(),
            sequence=1,
            delta="Deterministic response.",
            **values,
        )
        yield LLMEvent(
            event_type="response_completed",
            monotonic_seconds=time.monotonic(),
            sequence=2,
            text="Deterministic response.",
            finish_reason="stop",
            **values,
        )

    async def close(self) -> None:
        self.closed = True


def _email(label: str) -> str:
    return f"silence-guard-{label}-{uuid.uuid4().hex}@example.test"


async def _cleanup(settings: Settings, emails: set[str]) -> None:
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user_ids = list((await session.scalars(select(User.id).where(User.email.in_(emails)))).all())
        if user_ids:
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
    await engine.dispose()


def _create_account(client: TestClient, email: str) -> dict:
    device_id = f"device-{uuid.uuid4().hex[:12]}"
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
            "device_identifier": device_id,
            "platform": "android",
        },
    )
    assert login.status_code == 200, login.text
    return login.json()


@pytest.mark.integration
def test_gateway_silence_guard_rejects_turn_and_allows_subsequent_turn() -> None:
    """End-to-end WebSocket test for QA-VOICE-001.

    Verifies:
    1. Digital silence is rejected by the silence guard.
    2. Server emits server.turn.failed with code 'stt_empty_transcript'.
    3. No user message, memory extraction, or LLM call is made for the silent turn.
    4. A subsequent valid speech turn immediately succeeds in the same session.
    """
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run gateway integration tests.")

    stt_request_count = 0

    async def stt_handler(request: httpx.Request) -> httpx.Response:
        nonlocal stt_request_count
        stt_request_count += 1
        return httpx.Response(200, json={"text": "hello from valid turn"}, request=request)

    infrastructure_settings = Settings()
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=infrastructure_settings.database_url,
        redis_url=infrastructure_settings.redis_url,
        jwt_secret_key="silence-guard-test-secret-key-12345",
        access_token_expire_minutes=15,
        refresh_token_expire_days=1,
        stt_engine="remote",
        stt_api_url="https://stt.example.test/v1/audio/transcriptions",
        stt_api_key="test-key",
        stt_silence_guard_enabled=True,
        stt_silence_guard_min_rms=25.0,
        stt_silence_guard_min_peak=100,
        llm_provider="nvidia",
        llm_base_url="https://llm.invalid/v1",
        llm_api_key="placeholder-key",
        llm_model="test-model",
    )

    remote_engine = RemoteTranscriptionEngine(
        settings, transport=httpx.MockTransport(stt_handler)
    )
    stt_service = STTService(settings, engine=remote_engine)
    llm_provider = DeterministicLLMProvider()
    llm_service = LLMService(settings, provider=llm_provider)

    emails: set[str] = set()
    email = _email("turn")
    emails.add(email)

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always", SAWarning)
        with TestClient(
            create_app(settings=settings, stt_service=stt_service, llm_service=llm_service)
        ) as client:
            readiness = client.get("/ready")
            if readiness.status_code != 200:
                pytest.skip(f"Infrastructure unavailable: {readiness.json()}")

            tokens = _create_account(client, email)

            with client.websocket_connect(
                "/v1/voice", headers={"Authorization": f"Bearer {tokens['access_token']}"}
            ) as socket:
                # 1. Start Session
                socket.send_json(
                    {
                        "type": "client.session.start",
                        "protocol_version": 1,
                        "audio": {
                            "sample_rate_hz": 16000,
                            "channels": 1,
                            "frame_samples": 320,
                            "frame_bytes": 640,
                        },
                        "stt": {"enabled": True, "language": "en"},
                    }
                )
                ready = socket.receive_json()
                assert ready["type"] == "server.session.ready"
                session_id = ready["session_id"]

                # -------------------------------------------------------------
                # Turn 1: Pure Silence (Must be rejected by silence guard)
                # -------------------------------------------------------------
                socket.send_json({"type": "client.turn.start"})
                turn1_ready = socket.receive_json()
                assert turn1_ready["type"] == "server.turn.ready"
                turn1_id = turn1_ready["turn_id"]

                # Send 5 frames of pure digital silence
                for seq in range(5):
                    socket.send_bytes(
                        encode_pcm_frame(
                            sequence_no=seq,
                            client_timestamp_ms=1000 + seq * 20,
                            payload=b"\x00\x00" * 320,
                        )
                    )

                socket.send_json(
                    {
                        "type": "client.audio.commit",
                        "last_sequence_no": 4,
                        "frame_count": 5,
                        "byte_count": 5 * 640,
                        "duration_ms": 100,
                    }
                )

                # Silence guard must trigger server.turn.failed with stt_empty_transcript
                failed_event = socket.receive_json()
                assert failed_event["type"] == "server.turn.failed"
                assert failed_event["code"] == "stt_empty_transcript"
                assert failed_event["turn_id"] == turn1_id

                # Remote STT and LLM must NOT have been called for Turn 1
                assert stt_request_count == 0
                assert llm_provider.stream_calls == 0

                # -------------------------------------------------------------
                # Turn 2: Valid Speech (Must succeed immediately in same session)
                # -------------------------------------------------------------
                socket.send_json({"type": "client.turn.start"})
                turn2_ready = socket.receive_json()
                assert turn2_ready["type"] == "server.turn.ready"
                turn2_id = turn2_ready["turn_id"]

                # Send 5 frames of speech (amplitude 1000)
                speech_pcm = _sine_pcm(duration_s=0.1, amplitude=1000)
                for seq in range(5):
                    socket.send_bytes(
                        encode_pcm_frame(
                            sequence_no=seq,
                            client_timestamp_ms=2000 + seq * 20,
                            payload=speech_pcm[seq * 640 : (seq + 1) * 640],
                        )
                    )

                socket.send_json(
                    {
                        "type": "client.audio.commit",
                        "last_sequence_no": 4,
                        "frame_count": 5,
                        "byte_count": 5 * 640,
                        "duration_ms": 100,
                    }
                )

                # Turn 2 STT must pass the silence guard and complete
                transcript_final = socket.receive_json()
                assert transcript_final["type"] == "transcript.final"
                assert transcript_final["text"] == "hello from valid turn"
                assert transcript_final["turn_id"] == turn2_id

                # LLM response must complete for Turn 2
                while True:
                    event = socket.receive_json()
                    if event["type"] == "server.turn.completed":
                        assert event["turn_id"] == turn2_id
                        break

                assert stt_request_count == 1
                assert llm_provider.stream_calls == 1

                # Cleanly end session
                socket.send_json({"type": "client.session.end", "reason": "test_complete"})
                assert socket.receive_json()["type"] == "server.session.ending"
                assert socket.receive_json()["type"] == "server.session.ended"

        asyncio.run(_cleanup(settings, emails))
        gc.collect()
