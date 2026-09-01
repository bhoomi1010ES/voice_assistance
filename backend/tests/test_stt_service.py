from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.stt.service import (
    STTAudioError,
    STTCancelledError,
    STTConfigurationError,
    STTInferenceError,
    STTService,
)

REQUIRED_MODEL_FILES = (
    "model.bin",
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)


def _model_dir(tmp_path):
    model_dir = tmp_path / "whisper-large-v3-turbo-ct2"
    model_dir.mkdir()
    for filename in REQUIRED_MODEL_FILES:
        (model_dir / filename).write_bytes(b"test")
    return model_dir


def _settings(model_dir, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "stt_model_path": str(model_dir),
        "stt_device": "cpu",
        "stt_compute_type": "int8",
        "stt_partial_interval_seconds": 0.01,
        "stt_partial_window_seconds": 30,
        "stt_timeout": 2,
        "stt_workers": 2,
        "stt_max_active_turns": 4,
    }
    values.update(overrides)
    return Settings(**values)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class FakeModel:
    def __init__(self, *, slow: float = 0, fail_count: int = 0) -> None:
        self.slow = slow
        self.fail_count = fail_count
        self.calls = 0

    def transcribe(self, audio, **options):
        self.calls += 1
        if self.slow:
            time.sleep(self.slow)
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("test inference failure")
        text = "alpha" if audio[0] > 0 else "beta"
        language = options.get("language") or "en"
        return iter([SimpleNamespace(text=f" {text}")]), SimpleNamespace(language=language)


class FakeModelWithInfo:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, **options):
        self.calls += 1
        return iter([SimpleNamespace(text=" hello")]), SimpleNamespace(
            language=options.get("language") or "fr"
        )


async def _close(service: STTService) -> None:
    await service.close()


@pytest.mark.asyncio
async def test_model_is_initialized_once_and_reused(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    model = FakeModel()
    factory_calls = 0

    def factory(*args, **kwargs):
        nonlocal factory_calls
        factory_calls += 1
        assert kwargs["device"] == "cpu"
        assert kwargs["compute_type"] == "int8"
        return model

    service = STTService(_settings(model_dir), model_factory=factory)
    try:
        first = await service.initialize()
        second = await service.initialize()
        assert first is second
        session_id, turn_id, response_id = _ids()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await turn.accept_audio(b"\x00\x10" * 320)
        result = await turn.finalize()
        await turn.close()
        assert factory_calls == 1
        assert model.calls == 1
        assert result.event.final is True
        assert result.event.text == "alpha"
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_partial_and_final_events_are_ordered_and_associated(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    service = STTService(_settings(model_dir), model_factory=lambda *args, **kwargs: FakeModel())
    session_id, turn_id, response_id = _ids()
    try:
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await turn.accept_audio(b"\x00\x10" * 320)
        partial = await asyncio.wait_for(turn.events.get(), timeout=1)
        assert partial is not None
        assert partial.event_type == "transcript.partial"
        assert partial.final is False
        assert partial.session_id == session_id
        assert partial.turn_id == turn_id
        assert partial.response_id == response_id

        final = await turn.finalize()
        assert final.event.event_type == "transcript.final"
        assert final.event.final is True
        assert final.event.transcript_sequence > partial.transcript_sequence
        assert final.metrics["speech_end_to_final_transcript_ms"] is not None
        await turn.close()
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_language_is_explicit_or_detected_and_invalid_language_is_rejected(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    model = FakeModelWithInfo()
    service = STTService(_settings(model_dir), model_factory=lambda *args, **kwargs: model)
    try:
        session_id, turn_id, response_id = _ids()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            language="fr-FR",
        )
        await turn.accept_audio(b"\x00\x10" * 320)
        result = await turn.finalize()
        assert result.event.language == "fr"
        await turn.close()

        with pytest.raises(STTConfigurationError, match="unsupported STT language"):
            await service.start_turn(
                session_id=session_id,
                turn_id=uuid.uuid4(),
                response_id=uuid.uuid4(),
                language="not-a-language",
            )
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_empty_odd_and_oversized_audio_are_handled_safely(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    service = STTService(
        _settings(model_dir, stt_max_audio_seconds=1),
        model_factory=lambda *args, **kwargs: FakeModel(),
    )
    try:
        session_id, turn_id, response_id = _ids()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await turn.accept_audio(b"")
        with pytest.raises(STTAudioError):
            await turn.accept_audio(b"\x00")
        with pytest.raises(STTAudioError):
            await turn.accept_audio(b"\x00\x00" * (16_000 + 1))
        result = await turn.finalize()
        assert result.event.text == ""
        assert result.event.final is True
        await turn.close()
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_near_silence_does_not_invoke_model_or_emit_hallucination(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    model = FakeModel()
    service = STTService(_settings(model_dir), model_factory=lambda *args, **kwargs: model)
    try:
        session_id, turn_id, response_id = _ids()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await turn.accept_audio(b"\x00" * 32000)
        result = await turn.finalize()
        assert result.event.text == ""
        assert model.calls == 0
        await turn.close()
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_cancellation_discards_active_work_and_next_turn_recovers(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    service = STTService(
        _settings(model_dir, stt_workers=1, stt_timeout=2),
        model_factory=lambda *args, **kwargs: FakeModel(slow=0.25),
    )
    try:
        session_id, turn_id, response_id = _ids()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await turn.accept_audio(b"\x00\x10" * 320)
        final_task = asyncio.create_task(turn.finalize())
        await asyncio.sleep(0.03)
        await turn.cancel()
        with pytest.raises(STTCancelledError):
            await final_task
        assert service.active_turn_count == 0

        await asyncio.sleep(0.3)
        next_turn = await service.start_turn(
            session_id=session_id,
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
        )
        await next_turn.accept_audio(b"\x00\x10" * 320)
        result = await next_turn.finalize()
        assert result.event.text == "alpha"
        await next_turn.close()
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_concurrent_turns_keep_audio_and_transcripts_isolated(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    service = STTService(
        _settings(model_dir, stt_workers=2),
        model_factory=lambda *args, **kwargs: FakeModel(slow=0.02),
    )
    try:
        session_a, turn_a, response_a = _ids()
        session_b, turn_b, response_b = _ids()
        first = await service.start_turn(
            session_id=session_a,
            turn_id=turn_a,
            response_id=response_a,
        )
        second = await service.start_turn(
            session_id=session_b,
            turn_id=turn_b,
            response_id=response_b,
        )
        await first.accept_audio(b"\x00\x10" * 320)
        await second.accept_audio(b"\x00\xe0" * 320)
        first_result, second_result = await asyncio.gather(first.finalize(), second.finalize())
        assert first_result.event.text == "alpha"
        assert second_result.event.text == "beta"
        assert first_result.event.session_id == session_a
        assert second_result.event.session_id == session_b
        await first.close()
        await second.close()
    finally:
        await _close(service)


@pytest.mark.asyncio
async def test_failed_inference_does_not_poison_following_turn(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    model = FakeModel(fail_count=1)
    service = STTService(_settings(model_dir), model_factory=lambda *args, **kwargs: model)
    try:
        session_id, turn_id, response_id = _ids()
        first = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        )
        await first.accept_audio(b"\x00\x10" * 320)
        with pytest.raises(STTInferenceError):
            await first.finalize()
        await first.close()

        second = await service.start_turn(
            session_id=session_id,
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
        )
        await second.accept_audio(b"\x00\x10" * 320)
        result = await second.finalize()
        assert result.event.text == "alpha"
        await second.close()
    finally:
        await _close(service)
