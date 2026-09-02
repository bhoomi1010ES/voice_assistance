from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.phase4_acceptance import (  # noqa: E402
    PHASE4_REFERENCE_SENTENCES,
    calculate_turn_wer,
    evaluate_acceptance_gates,
    validate_turn_evidence,
)


def _valid_turn(number: int = 1) -> dict:
    reference = PHASE4_REFERENCE_SENTENCES[number - 1]
    turn = {
        "turn_number": number,
        "reference_text": reference,
        "reference_raw": reference,
        "turn_start_monotonic_ms": 1000.0,
        "first_pcm_timestamp": 1100,
        "first_pcm_monotonic_ms": 1100.0,
        "vad_end_timestamp": 2000,
        "vad_end_monotonic_ms": 2000.0,
        "final_transcript_timestamp": 2200,
        "final_transcript_monotonic_ms": 2200.0,
        "turn_end_timestamp": 2300,
        "turn_end_monotonic_ms": 2300.0,
        "final_transcript": reference,
        "partial_transcripts": [],
        "partial_count": 0,
        "no_partial_reason": "No partial event was observed before final",
        "speech_end_to_final_ms": 200.0,
        "audio_bytes": 640,
        "pcm_frames": 1,
        "websocket_session_id": "session",
        "stt_engine": "remote",
        "stt_provider": "stt.example.test",
        "language": "en",
        "error": None,
        "status": "PASS",
    }
    calculate_turn_wer(turn)
    return turn


def test_phase4_references_are_exact_and_wer_is_corpus_ready() -> None:
    assert len(PHASE4_REFERENCE_SENTENCES) == 10
    turn = _valid_turn()
    assert turn["reference_normalized"] == "the quick brown fox jumps over the lazy dog"
    assert turn["hypothesis_normalized"] == turn["reference_normalized"]
    assert turn["per_turn_wer"]["reference_word_count"] == 9
    assert turn["per_turn_wer"]["wer"] == 0.0


def test_missing_monotonic_and_partial_reason_cannot_pass() -> None:
    turn = _valid_turn()
    turn.pop("final_transcript_monotonic_ms")
    turn["no_partial_reason"] = None
    errors = validate_turn_evidence(turn)
    assert any("final_transcript_monotonic_ms" in error for error in errors)
    assert any("no_partial_reason" in error for error in errors)


def test_acceptance_requires_resources_reconnect_and_all_ten_turns() -> None:
    turns = [_valid_turn(number) for number in range(1, 11)]
    evidence = {
        "references_stored_before_recognition": True,
        "backend_resource_samples": [{"pid": 1}],
        "remote_request_samples": [{"status_code": 200}],
        "android_device": True,
        "apk_install_launch": True,
        "websocket_physical_path": True,
        "remote_stt_deployment": True,
        "automated_validation_passed": True,
        "reconnect": {
            "disconnect_observed": True,
            "reconnect_observed": True,
            "audio_accepted_after_reconnect": True,
            "stt_continued_after_reconnect": True,
        },
    }
    result = evaluate_acceptance_gates(turns, required_turns=10, evidence=evidence)
    assert result["pass"] is True

    evidence["reconnect"]["reconnect_observed"] = False
    blocked = evaluate_acceptance_gates(turns, required_turns=10, evidence=evidence)
    assert blocked["pass"] is False
    assert blocked["gates"]["physical_reconnect"] is False

    incomplete = evaluate_acceptance_gates(turns[:9], required_turns=10, evidence=evidence)
    assert incomplete["pass"] is False
    assert incomplete["gates"]["completed_turns"] is False
