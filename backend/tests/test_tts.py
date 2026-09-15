import json
import struct

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.tts.remote import RemoteTTSEngine
from app.tts.segmentation import SentenceSegmenter


def _wav_pcm(payload: bytes, *, sample_rate_hz: int = 24_000) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, sample_rate_hz, sample_rate_hz * 2, 2, 16)
    junk = b"JUNK" + struct.pack("<I", 3) + b"abc" + b"\0"
    data = b"data" + struct.pack("<I", len(payload)) + payload
    return (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + len(fmt) + len(junk) + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + junk
        + data
    )


def test_sentence_segmenter_emits_complete_and_bounded_chunks() -> None:
    segmenter = SentenceSegmenter(max_chars=12)

    assert segmenter.push("Hello world. This") == ["Hello world."]
    assert segmenter.push(" is a longer sentence") == ["This is a", "longer"]
    assert segmenter.flush() == ["sentence"]


@pytest.mark.asyncio
async def test_remote_tts_uses_configured_speech_endpoint_and_extracts_wav_pcm() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            content=_wav_pcm(b"\x00\x01\x02\x03"),
            headers={"content-type": "audio/wav", "x-sample-rate": "24000"},
        )

    settings = Settings(
        _env_file=None,
        tts_api_url="https://notify-towers-shipment-bookstore.trycloudflare.com/v1/audio/speech",
        tts_api_key=SecretStr("test-only-key"),
        tts_api_model="kokoro",
        tts_api_voice="af_heart",
        tts_api_response_format="wav",
        tts_api_sample_rate_hz=24_000,
    )
    engine = RemoteTTSEngine(settings, transport=httpx.MockTransport(handler))
    try:
        await engine.initialize()
        chunks = [chunk async for chunk in engine.stream(text="Hello.", response_id="r1")]
    finally:
        await engine.close()

    assert chunks == [b"\x00\x01\x02\x03"]
    assert len(calls) == 1
    assert str(calls[0].url) == settings.tts_api_url
    assert calls[0].headers["authorization"] == "Bearer test-only-key"
    assert engine.last_stream_metrics is not None
    assert engine.last_stream_metrics.pcm_bytes == 4
    assert engine.last_stream_metrics.audio_duration_ms == pytest.approx(0.083333, rel=1e-4)
    assert engine.last_stream_metrics.rtf is not None
    assert json.loads(calls[0].content) == {
        "model": "kokoro",
        "voice": "af_heart",
        "input": "Hello.",
        "response_format": "wav",
        "sample_rate_hz": 24_000,
    }


@pytest.mark.asyncio
async def test_remote_tts_rejects_non_wav_provider_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01\x02\x03")

    settings = Settings(
        _env_file=None,
        tts_api_url="https://tts.example.test/v1/audio/speech",
        tts_api_key=SecretStr("test-only-key"),
        tts_api_response_format="wav",
    )
    engine = RemoteTTSEngine(settings, transport=httpx.MockTransport(handler))
    await engine.initialize()
    with pytest.raises(Exception, match="audio/wav"):
        _ = [chunk async for chunk in engine.stream(text="Hello.", response_id="r1")]
    await engine.close()


@pytest.mark.asyncio
async def test_remote_tts_is_disabled_without_endpoint() -> None:
    engine = RemoteTTSEngine(Settings(_env_file=None))

    info = await engine.initialize()

    assert info.enabled is False
    assert engine.enabled is False
    await engine.close()
