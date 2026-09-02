from __future__ import annotations

import asyncio
import json
import struct
import uuid
import wave

import pytest

from app.core.config import Settings
from app.stt.base import (
    EnginePartialCallback,
    STTEngine,
    STTEngineFinal,
    STTEngineInfo,
    STTEngineTurn,
)
from app.stt.diagnostic_capture import DiagnosticPcmCapture
from app.stt.service import STTService


class CaptureFakeEngine(STTEngine):
    name = "capture-test"

    async def initialize(self) -> STTEngineInfo:
        return STTEngineInfo(engine=self.name, runtime="test", available=True, language="en-US")

    async def start_turn(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        generation: int,
        language: str | None,
        on_partial: EnginePartialCallback,
    ) -> STTEngineTurn:
        return STTEngineTurn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            generation=generation,
            language=language,
            on_partial=on_partial,
        )

    async def push_audio(self, turn: STTEngineTurn, pcm_bytes: bytes, *, generation: int) -> None:
        return None

    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        return STTEngineFinal(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            response_id=turn.response_id,
            generation=generation,
            text="captured result",
            language="en-US",
            confidence=1.0,
            timestamp_ms=1,
            monotonic_timestamp=asyncio.get_running_loop().time(),
            inference_duration_ms=1.0,
        )

    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        return None

    async def close(self) -> None:
        return None


def test_diagnostic_capture_preserves_exact_pcm_and_wav_metadata(tmp_path) -> None:
    pcm = struct.pack("<hhhh", -32768, -64, 0, 32767)
    capture = DiagnosticPcmCapture(
        tmp_path,
        session_id="session",
        turn_id="turn",
        max_seconds=1,
    )
    capture.append(pcm)
    metadata = capture.finalize(status="final", hypothesis_raw="captured result")

    assert (tmp_path / "raw_input.pcm").read_bytes() == pcm
    with wave.open(str(tmp_path / "raw_input.wav"), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getframerate() == 16_000
        assert source.getsampwidth() == 2
        assert source.readframes(source.getnframes()) == pcm
    assert metadata["byte_count"] == len(pcm)
    assert metadata["sample_count"] == 4
    assert metadata["minimum_sample"] == -32768
    assert metadata["maximum_sample"] == 32767
    assert metadata["clipped_sample_count"] == 2
    assert json.loads((tmp_path / "metadata.json").read_text())["sha256"] == metadata["sha256"]


@pytest.mark.asyncio
async def test_service_claims_only_one_diagnostic_turn(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        stt_diagnostic_capture_enabled=True,
        stt_diagnostic_capture_dir=str(tmp_path),
    )
    service = STTService(settings, engine=CaptureFakeEngine())
    first = await service.start_turn(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
    )
    second = await service.start_turn(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
    )
    pcm = b"\x01\x00" * 320
    await first.accept_audio(pcm)
    await first.finalize()
    await second.accept_audio(pcm)
    await second.finalize()

    assert (tmp_path / "raw_input.pcm").read_bytes() == pcm
    await first.close()
    await second.close()
    await service.close()
