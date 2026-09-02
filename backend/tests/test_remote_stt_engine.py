from __future__ import annotations

import asyncio
import io
import uuid
import wave

import httpx
import pytest

from app.core.config import Settings
from app.stt.base import STTAudioError, STTCancelledError, STTConfigurationError, STTInferenceError
from app.stt.remote_engine import RemoteTranscriptionEngine
from app.stt.service import STTService


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "stt_engine": "remote",
        "stt_api_url": "https://stt.example.test/v1/audio/transcriptions",
        "stt_api_key": "test-api-key",
        "stt_api_model": "test-model",
        "stt_api_timeout_seconds": 2,
        "stt_api_connect_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


async def _noop_partial(_partial) -> None:
    return None


@pytest.mark.asyncio
async def test_remote_engine_submits_exact_pcm_as_wav_and_returns_text() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = await request.aread()
        return httpx.Response(200, json={"text": " hello world "}, request=request)

    settings = _settings()
    engine = RemoteTranscriptionEngine(settings, transport=httpx.MockTransport(handler))
    session_id, turn_id, response_id = _ids()
    pcm = b"\x01\x02" * 320

    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en-US",
        on_partial=_noop_partial,
    )
    await engine.push_audio(handle, pcm, generation=0)
    result = await engine.finish_turn(handle, generation=1)

    assert result.text == "hello world"
    assert result.language == "en-US"
    headers = seen["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["authorization"] == "Bearer test-api-key"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'filename="audio.wav"' in body
    assert b"response_format" in body
    assert b"test-model" in body
    assert b"RIFF" in body
    assert b"WAVEfmt " in body

    wav_bytes = engine._to_wav(pcm)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
        assert wav.readframes(wav.getnframes()) == pcm

    await engine.close_turn(handle)
    await engine.close()


@pytest.mark.asyncio
async def test_remote_engine_does_not_emit_synthetic_partials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "final text"}, request=request)

    partials: list[object] = []

    async def on_partial(partial) -> None:
        partials.append(partial)

    engine = RemoteTranscriptionEngine(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=on_partial,
    )
    await engine.push_audio(handle, b"\x00\x10" * 320, generation=0)
    result = await engine.finish_turn(handle, generation=1)

    assert result.text == "final text"
    assert partials == []
    await engine.close()


@pytest.mark.asyncio
async def test_remote_engine_requires_api_key_before_client_startup() -> None:
    engine = RemoteTranscriptionEngine(
        _settings(stt_api_key=None),
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
    )

    with pytest.raises(STTConfigurationError, match="STT_API_KEY"):
        await engine.initialize()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, STTConfigurationError),
        (413, STTAudioError),
        (429, STTInferenceError),
        (500, STTInferenceError),
    ],
)
async def test_remote_engine_maps_provider_statuses(
    status: int,
    error_type: type[Exception],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "test"}, request=request)

    engine = RemoteTranscriptionEngine(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=_noop_partial,
    )
    await engine.push_audio(handle, b"\x00\x10" * 320, generation=0)

    with pytest.raises(error_type):
        await engine.finish_turn(handle, generation=1)
    await engine.close()


@pytest.mark.asyncio
async def test_remote_engine_cancellation_cancels_inflight_request() -> None:
    request_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        request_started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    engine = RemoteTranscriptionEngine(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=_noop_partial,
    )
    await engine.push_audio(handle, b"\x00\x10" * 320, generation=0)
    final_task = asyncio.create_task(engine.finish_turn(handle, generation=1))
    await asyncio.wait_for(request_started.wait(), timeout=1)

    await engine.cancel_turn(handle, generation=2)
    with pytest.raises(STTCancelledError):
        await final_task
    await engine.close()


@pytest.mark.asyncio
async def test_stt_service_selects_remote_engine_without_windows_worker() -> None:
    service = STTService(_settings())
    try:
        assert isinstance(service.engine, RemoteTranscriptionEngine)
    finally:
        await service.close()
