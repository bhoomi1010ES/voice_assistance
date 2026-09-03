from __future__ import annotations

import asyncio
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from app.core.config import Settings  # noqa: E402
from app.stt.service import STTService  # noqa: E402

REQUIRED_MODEL_FILES = (
    "model.bin",
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)


def _model_dir(tmp_path):
    model_dir = tmp_path / "whisper-large-v3-turbo-ct2"
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_MODEL_FILES:
        (model_dir / filename).write_bytes(b"test")
    return model_dir


def _settings(model_dir, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "stt_model_path": str(model_dir),
        "stt_device": "cpu",
        "stt_compute_type": "int8",
        "stt_threads": 4,
        "stt_beam_size": 1,
        "stt_partial_interval_seconds": 0.01,
        "stt_partial_window_seconds": 30,
        "stt_timeout": 5,
        "stt_workers": 2,
        "stt_max_active_turns": 4,
    }
    values.update(overrides)
    return Settings(**values)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class NonBlockingFakeModel:
    def __init__(self, *, partial_delay: float = 0.5, final_delay: float = 0.02) -> None:
        self.partial_delay = partial_delay
        self.final_delay = final_delay
        self.calls = []

    def transcribe(self, audio, **options):
        thread_name = threading.current_thread().name
        is_partial = "partial" in thread_name
        kind = "partial" if is_partial else "final"
        self.calls.append((kind, options.get("beam_size")))
        if is_partial and self.partial_delay:
            time.sleep(self.partial_delay)
        elif not is_partial and self.final_delay:
            time.sleep(self.final_delay)
        return iter([SimpleNamespace(text=" transcribed text")]), SimpleNamespace(language="en")


@pytest.mark.asyncio
async def test_stt_model_factory_receives_cpu_threads(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    factory_calls = 0
    passed_threads = None

    def factory(path, **kwargs):
        nonlocal factory_calls, passed_threads
        factory_calls += 1
        passed_threads = kwargs.get("cpu_threads")
        return NonBlockingFakeModel()

    service = STTService(_settings(model_dir, stt_threads=4), model_factory=factory)
    info = await service.initialize()
    assert factory_calls == 2
    assert passed_threads in [1, 4]
    assert info.device == "cpu"
    assert info.compute_type == "int8"
    await service.close()


@pytest.mark.asyncio
async def test_partial_inference_does_not_starve_final_inference(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    # Simulate slow partial inference (0.5s) and fast final inference (0.02s)
    fake_model = NonBlockingFakeModel(partial_delay=0.5, final_delay=0.02)
    service = STTService(_settings(model_dir), model_factory=lambda *a, **k: fake_model)
    await service.initialize()

    session_id, turn_id, response_id = _ids()
    turn = await service.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
    )

    # 1 second of non-silent audio
    pcm = np.full(16000, 10000, dtype=np.int16).tobytes()
    await turn.accept_audio(pcm)

    # Wait for partial task to be scheduled and running
    await asyncio.sleep(0.05)

    # Now finalize immediately
    t0 = time.monotonic()
    result = await turn.finalize()
    final_latency = time.monotonic() - t0

    # Final inference runs on dedicated _final_executor and must NOT wait for the 0.5s partial!
    assert final_latency < 0.25, f"Final inference was blocked by partial: {final_latency}s"
    assert result.event.final is True
    assert result.event.text == "transcribed text"
    await service.close()


@pytest.mark.asyncio
async def test_turn_audio_isolation_no_leakage(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    fake_model = NonBlockingFakeModel(partial_delay=0.01, final_delay=0.01)
    service = STTService(_settings(model_dir), model_factory=lambda *a, **k: fake_model)
    await service.initialize()

    # Turn 1
    sess_id, turn1_id, resp1_id = _ids()
    turn1 = await service.start_turn(session_id=sess_id, turn_id=turn1_id, response_id=resp1_id)
    pcm1 = np.full(8000, 8000, dtype=np.int16).tobytes()
    await turn1.accept_audio(pcm1)
    assert turn1.audio_duration_ms == 500
    res1 = await turn1.finalize()
    assert res1.metrics["audio_duration_ms"] == 500

    # Turn 2
    sess_id2, turn2_id, resp2_id = _ids()
    turn2 = await service.start_turn(session_id=sess_id2, turn_id=turn2_id, response_id=resp2_id)
    assert turn2.audio_duration_ms == 0
    pcm2 = np.full(16000, 8000, dtype=np.int16).tobytes()
    await turn2.accept_audio(pcm2)
    assert turn2.audio_duration_ms == 1000
    res2 = await turn2.finalize()
    assert res2.metrics["audio_duration_ms"] == 1000

    await service.close()


@pytest.mark.asyncio
async def test_metrics_monotonic_integrity(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    fake_model = NonBlockingFakeModel(partial_delay=0.01, final_delay=0.01)
    service = STTService(_settings(model_dir), model_factory=lambda *a, **k: fake_model)
    await service.initialize()

    sess_id, turn_id, resp_id = _ids()
    turn = await service.start_turn(session_id=sess_id, turn_id=turn_id, response_id=resp_id)
    pcm = np.full(16000, 10000, dtype=np.int16).tobytes()
    await turn.accept_audio(pcm)
    res = await turn.finalize()

    metrics = res.metrics
    assert metrics["monotonic_audio_start_ms"] is not None
    assert metrics["monotonic_speech_end_ms"] is not None
    assert metrics["monotonic_final_ms"] is not None
    assert metrics["monotonic_final_ms"] >= metrics["monotonic_speech_end_ms"]
    assert metrics["speech_end_to_final_transcript_ms"] >= 0
    assert metrics["commit_to_final_transcript_ms"] >= 0

    await service.close()


def test_harness_timestamp_integrity_validation() -> None:
    from scripts.interactive_validation import InteractiveValidationRunner

    runner = InteractiveValidationRunner()

    # Case 1: Valid turn
    turn_valid = runner._new_turn_dict(1)
    turn_valid["turn_id"] = str(uuid.uuid4())
    turn_valid["response_id"] = str(uuid.uuid4())
    turn_valid["session_id"] = "session"
    turn_valid["server_audio_start_monotonic_ms"] = 1000.0
    turn_valid["first_pcm_timestamp"] = 1000
    turn_valid["first_pcm_monotonic_ms"] = 1000.0
    turn_valid["speech_end_monotonic_ms"] = 3000.0
    turn_valid["vad_end_timestamp"] = 3000
    turn_valid["vad_end_monotonic_ms"] = 3000.0
    turn_valid["backend_commit_received_monotonic_ms"] = 3100.0
    turn_valid["final_transcript_monotonic_ms"] = 4500.0
    turn_valid["final_transcript_timestamp"] = 4500
    turn_valid["final_delivered_monotonic_ms"] = 4600.0
    turn_valid["android_speech_end_monotonic_ms"] = 3000.0
    turn_valid["turn_end_timestamp"] = 4600
    turn_valid["turn_end_monotonic_ms"] = 4600.0
    turn_valid["turn_start_monotonic_ms"] = 1000.0
    turn_valid["turn_completion_monotonic_ms"] = 4600.0
    turn_valid["final_transcript"] = "Hello world"
    turn_valid["partial_count"] = 0
    turn_valid["no_partial_reason"] = "No partial event was observed before final"
    turn_valid["audio_bytes"] = 640
    turn_valid["pcm_frames"] = 1
    turn_valid["audio_duration_ms"] = 2000.0
    turn_valid["speech_end_to_request_ms"] = 10.0
    turn_valid["remote_request_start_monotonic_ms"] = 3010.0
    turn_valid["remote_response_monotonic_ms"] = 4500.0
    turn_valid["remote_request_latency_ms"] = 1490.0
    turn_valid["remote_http_status"] = 200
    turn_valid["speech_end_to_client_delivery_ms"] = 1600.0
    turn_valid["websocket_session_id"] = "session"
    turn_valid["stt_engine"] = "remote"
    turn_valid["stt_provider"] = "stt.example.test"
    turn_valid["recognizer_id"] = "NOT_APPLICABLE"
    turn_valid["language"] = "en"
    turn_valid["error"] = None
    turn_valid["final_count"] = 1
    runner.finalize_turn_metrics(turn_valid)
    assert turn_valid["status"] == "PASS"
    assert turn_valid["speech_duration_ms"] == 2000.0
    assert turn_valid["commit_to_final_ms"] == 1400.0
    assert turn_valid["turn_processing_ms"] == 3600.0

    # Case 2: Inverted speech timestamps (speech_end < speech_start)
    turn_inversion = runner._new_turn_dict(2)
    turn_inversion["turn_id"] = str(uuid.uuid4())
    turn_inversion["response_id"] = str(uuid.uuid4())
    turn_inversion["server_audio_start_monotonic_ms"] = 5000.0
    turn_inversion["speech_end_monotonic_ms"] = 4000.0  # Inverted!
    turn_inversion["backend_commit_received_monotonic_ms"] = 5100.0
    turn_inversion["final_transcript_monotonic_ms"] = 6000.0
    turn_inversion["turn_start_monotonic_ms"] = 5000.0
    turn_inversion["turn_completion_monotonic_ms"] = 6100.0
    turn_inversion["final_transcript"] = "Testing inversion"
    turn_inversion["final_count"] = 1
    runner.finalize_turn_metrics(turn_inversion)
    assert turn_inversion["status"] == "FAIL_TIMESTAMP_INTEGRITY"
    assert "speech_duration_ms" in turn_inversion["failure_reason"]

    # Case 3: Final transcript received before commit
    turn_commit_inv = runner._new_turn_dict(3)
    turn_commit_inv["turn_id"] = str(uuid.uuid4())
    turn_commit_inv["response_id"] = str(uuid.uuid4())
    turn_commit_inv["speech_start_monotonic_ms"] = 1000.0
    turn_commit_inv["speech_end_monotonic_ms"] = 3000.0
    turn_commit_inv["backend_commit_received_monotonic_ms"] = 4000.0
    turn_commit_inv["final_transcript_monotonic_ms"] = 3500.0  # Final before commit!
    turn_commit_inv["final_transcript"] = "Testing commit inversion"
    turn_commit_inv["final_count"] = 1
    runner.finalize_turn_metrics(turn_commit_inv)
    assert turn_commit_inv["status"] == "FAIL_TIMESTAMP_INTEGRITY"

    # Case 4: Massive negative turn_processing_ms (the exact bug from post-fix attempt 1)
    turn_neg = runner._new_turn_dict(4)
    turn_neg["turn_id"] = str(uuid.uuid4())
    turn_neg["response_id"] = str(uuid.uuid4())
    turn_neg["final_transcript"] = "Testing negative turn processing"
    turn_neg["final_count"] = 1
    turn_neg["commit_to_final_ms"] = 25000.0
    turn_neg["turn_processing_ms"] = -371153348.0  # Cross-domain negative bug!
    runner.finalize_turn_metrics(turn_neg)
    assert turn_neg["status"] == "FAIL_TIMESTAMP_INTEGRITY"
    assert "negative duration in turn_processing_ms" in turn_neg["failure_reason"]


@pytest.mark.asyncio
async def test_dual_model_separate_thread_allocation(tmp_path) -> None:
    model_dir = _model_dir(tmp_path)
    captured_calls = []

    def mock_factory(path, **kwargs):
        captured_calls.append(kwargs)
        return NonBlockingFakeModel(partial_delay=0.001, final_delay=0.001)

    service = STTService(_settings(model_dir), model_factory=mock_factory)
    await service.initialize()

    # Verify that two models were initialized: final (4 threads) and partial (1 thread)
    assert len(captured_calls) == 2
    assert captured_calls[0]["cpu_threads"] == 4
    assert captured_calls[1]["cpu_threads"] == 1

    await service.close()
