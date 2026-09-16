import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.websocket.cancellation import CancellationGuard
from app.websocket.gateway import VoiceGateway, tts_pacing_delay_seconds


def test_tts_pacing_allows_startup_prebuffer_without_delay() -> None:
    assert (
        tts_pacing_delay_seconds(
            sent_pcm_bytes=9_600,
            started_at=100.0,
            now=100.0,
            sample_rate_hz=24_000,
        )
        == 0.0
    )


def test_tts_pacing_releases_following_audio_at_playback_rate() -> None:
    assert (
        tts_pacing_delay_seconds(
            sent_pcm_bytes=19_200,
            started_at=100.0,
            now=100.0,
            sample_rate_hz=24_000,
        )
        == 0.2
    )


def test_tts_pacing_does_not_delay_when_network_is_already_behind() -> None:
    assert (
        tts_pacing_delay_seconds(
            sent_pcm_bytes=19_200,
            started_at=100.0,
            now=100.3,
            sample_rate_hz=24_000,
        )
        == 0.0
    )


@pytest.mark.asyncio
async def test_tts_timing_captures_first_valid_pcm_without_android_playback_claim() -> None:
    class FakeTTS:
        async def stream(self, *, text: str, response_id: str, **kwargs):
            assert text == "hello."
            assert response_id
            yield b"\x00\x00" * 4_800

    gateway = object.__new__(VoiceGateway)
    gateway.settings = SimpleNamespace(tts_api_sample_rate_hz=24_000)
    gateway.tts_service = FakeTTS()
    gateway.cancel_guard = CancellationGuard()
    gateway._closing = asyncio.Event()
    gateway._turn_timings = {}
    sent_audio: list[bytes] = []

    async def send_event(*args, **kwargs) -> None:
        return None

    async def send_audio(*, payload: bytes, **kwargs) -> None:
        sent_audio.append(payload)

    gateway._send_tts_event = send_event
    gateway._send_tts_audio = send_audio

    turn_id = uuid.uuid4()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    await queue.put("hello.")
    await queue.put(None)

    await gateway._run_tts_queue(
        session_id=uuid.uuid4(),
        turn_id=turn_id,
        response_id=response_id,
        queue=queue,
    )

    points = gateway._turn_timings[turn_id].points
    assert "tts_requested_at" in points
    assert "tts_first_audio_at" in points
    assert points["tts_requested_at"].monotonic <= points["tts_first_audio_at"].monotonic
    assert sent_audio[0] == b"\x00\x00" * 4_800
    assert gateway._turn_timings[turn_id].tts == {
        "sample_rate": 24_000,
        "channels": 1,
        "encoding": "pcm16",
        "prebuffer_ms": 200,
        "prebuffer_bytes": 9_600,
        "frames_sent": 1,
        "pcm_bytes_sent": 9_600,
        "tts_generation_ms": None,
        "tts_audio_duration_ms": None,
        "tts_rtf": None,
        "sequence_gaps": 0,
        "duplicate_frames": 0,
        "stale_frames": None,
        "underrun_delta": None,
    }
