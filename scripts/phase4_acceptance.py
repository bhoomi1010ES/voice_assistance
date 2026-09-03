"""Strict, side-effect-free predicates for Phase 4 physical acceptance.

The interactive runner owns device/process I/O.  This module owns the parts
that must be deterministic and testable: transcript normalization, WER,
mandatory turn evidence, aggregate statistics, and the final gate decision.
"""

from __future__ import annotations

import math
from typing import Any

from app.stt.evaluation import calculate_wer, normalize_transcript

NOT_AVAILABLE = "NOT_AVAILABLE"

PHASE4_REFERENCE_SENTENCES = (
    "The quick brown fox jumps over the lazy dog.",
    "Please remind me to call the doctor tomorrow morning.",
    "What was the last time I visited Mumbai?",
    "The weather today is warm and slightly cloudy.",
    "I would like to schedule a meeting for Friday afternoon.",
    "My voice assistant should respond quickly and accurately.",
    "Please find my recent notes about the project architecture.",
    "Turn off the bedroom lights after ten minutes.",
    "Artificial intelligence can help automate repetitive tasks.",
    "This is the final sentence for the speech recognition test.",
)

MANDATORY_TURN_FIELDS = (
    "turn_number",
    "reference_text",
    "session_id",
    "turn_id",
    "response_id",
    "turn_start_monotonic_ms",
    "first_pcm_timestamp",
    "first_pcm_monotonic_ms",
    "vad_end_timestamp",
    "vad_end_monotonic_ms",
    "speech_end_monotonic_ms",
    "final_transcript_timestamp",
    "final_transcript_monotonic_ms",
    "turn_end_timestamp",
    "turn_end_monotonic_ms",
    "final_transcript",
    "final_delivered_monotonic_ms",
    "partial_transcripts",
    "partial_supported",
    "partial_count",
    "no_partial_reason",
    "audio_duration_ms",
    "speech_end_to_final_ms",
    "speech_end_to_client_delivery_ms",
    "speech_end_to_request_ms",
    "remote_request_start_monotonic_ms",
    "remote_response_monotonic_ms",
    "remote_request_latency_ms",
    "remote_http_status",
    "audio_bytes",
    "pcm_frames",
    "websocket_session_id",
    "stt_engine",
    "stt_provider",
    "language",
    "per_turn_wer",
    "error",
)


def is_present(value: Any) -> bool:
    return value not in (None, "", NOT_AVAILABLE, "N/A", "NOT_DEFINED")


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def calculate_turn_wer(turn: dict[str, Any]) -> dict[str, Any] | None:
    """Calculate and persist the complete raw/normalized per-turn WER record."""

    reference_raw = turn.get("reference_raw") or turn.get("reference_text")
    hypothesis_raw = turn.get("hypothesis_raw")
    if not is_present(hypothesis_raw):
        hypothesis_raw = turn.get("final_transcript")
    if not isinstance(reference_raw, str) or not isinstance(hypothesis_raw, str):
        return None
    if not reference_raw.strip() or not hypothesis_raw.strip() or hypothesis_raw == NOT_AVAILABLE:
        return None

    result = calculate_wer(reference_raw, hypothesis_raw)
    wer = {
        "reference_raw": reference_raw,
        "reference_normalized": normalize_transcript(reference_raw),
        "hypothesis_raw": hypothesis_raw,
        "hypothesis_normalized": normalize_transcript(hypothesis_raw),
        "substitutions": result.substitutions,
        "deletions": result.deletions,
        "insertions": result.insertions,
        "reference_word_count": result.reference_words,
        "wer": result.wer,
    }
    turn.update(
        {
            "reference_raw": reference_raw,
            "reference_normalized": wer["reference_normalized"],
            "hypothesis_raw": hypothesis_raw,
            "hypothesis_normalized": wer["hypothesis_normalized"],
            "wer_substitutions": result.substitutions,
            "wer_deletions": result.deletions,
            "wer_insertions": result.insertions,
            "wer_reference_words": result.reference_words,
            "wer": result.wer,
            "per_turn_wer": wer,
        }
    )
    return wer


def validate_turn_evidence(
    turn: dict[str, Any],
    *,
    expected_engine: str = "remote",
    expected_provider: str | None = None,
    expected_language: str = "en",
) -> list[str]:
    """Return every missing or invalid mandatory evidence item for a turn."""

    errors: list[str] = []
    for field in MANDATORY_TURN_FIELDS:
        if field not in turn:
            errors.append(f"missing field: {field}")
        elif field != "error" and not is_present(turn[field]):
            errors.append(f"missing value: {field}")

    if turn.get("turn_number") not in range(1, 11):
        errors.append("turn_number is outside 1..10")
    else:
        expected_reference = PHASE4_REFERENCE_SENTENCES[turn["turn_number"] - 1]
        if turn.get("reference_raw") != expected_reference:
            errors.append("reference_raw does not match the fixed Phase 4 reference")
        if turn.get("reference_text") != expected_reference:
            errors.append("reference_text does not match the fixed Phase 4 reference")
        if turn.get("reference_normalized") != normalize_transcript(expected_reference):
            errors.append("reference_normalized does not match the fixed reference")
    if turn.get("stt_engine") != expected_engine:
        errors.append(f"wrong STT engine: {turn.get('stt_engine')!r}")
    if expected_engine == "windows":
        errors.append("Windows Speech Recognition is no longer a Phase 4 engine")
    if not is_present(turn.get("stt_provider")):
        errors.append("missing remote STT provider")
    elif expected_provider and turn.get("stt_provider") != expected_provider:
        errors.append(f"wrong STT provider: {turn.get('stt_provider')!r}")
    if turn.get("language", "").casefold() != expected_language.casefold():
        errors.append(f"wrong language: {turn.get('language')!r}")

    partials = turn.get("partial_transcripts")
    if not isinstance(partials, list):
        errors.append("partial_transcripts must be a list")
        partials = []
    if turn.get("partial_count") != len(partials):
        errors.append("partial_count does not match partial_transcripts")
    if partials:
        for index, partial in enumerate(partials, start=1):
            if not isinstance(partial, dict):
                errors.append(f"partial {index} is not an object")
                continue
            for field in ("turn", "partial_index", "text", "monotonic_timestamp", "utc_timestamp"):
                if not is_present(partial.get(field)):
                    errors.append(f"partial {index} missing {field}")
            if not is_number(partial.get("monotonic_timestamp")):
                errors.append(f"partial {index} missing monotonic timestamp")
        if not is_number(turn.get("first_partial_monotonic_ms")):
            errors.append("partial exists but first partial monotonic timestamp is missing")
        if not is_number(turn.get("first_audio_to_first_partial_ms")):
            errors.append("partial exists but first audio to first partial latency is missing")
    elif not is_present(turn.get("no_partial_reason")):
        errors.append("partial_count is zero without no_partial_reason")

    monotonic_fields = (
        "turn_start_monotonic_ms",
        "first_pcm_monotonic_ms",
        "vad_end_monotonic_ms",
        "final_transcript_monotonic_ms",
        "turn_end_monotonic_ms",
    )
    for field in monotonic_fields:
        if not is_number(turn.get(field)):
            errors.append(f"missing monotonic timestamp: {field}")
    monotonic_values = [turn.get(field) for field in monotonic_fields]
    if all(is_number(value) for value in monotonic_values) and monotonic_values != sorted(
        monotonic_values
    ):
        errors.append("monotonic timestamps are not ordered")

    for field in (
        "audio_duration_ms",
        "speech_end_to_final_ms",
        "speech_end_to_client_delivery_ms",
        "speech_end_to_request_ms",
        "remote_request_start_monotonic_ms",
        "remote_response_monotonic_ms",
        "remote_request_latency_ms",
        "audio_bytes",
        "pcm_frames",
        "wer",
    ):
        if not is_number(turn.get(field)):
            errors.append(f"missing numeric evidence: {field}")
    for field in (
        "speech_end_to_final_ms",
        "speech_end_to_client_delivery_ms",
        "speech_end_to_request_ms",
        "remote_request_latency_ms",
    ):
        if is_number(turn.get(field)) and turn[field] < 0:
            errors.append(f"{field} is negative")
    if turn.get("remote_http_status") != 200:
        errors.append(f"remote_http_status is not 200: {turn.get('remote_http_status')!r}")
    if turn.get("partial_supported") is not False:
        errors.append("partial_supported must be false for the remote final-only contract")
    if not isinstance(turn.get("error"), (str, type(None))):
        errors.append("error must be null or a string")

    per_turn_wer = turn.get("per_turn_wer")
    if not isinstance(per_turn_wer, dict):
        errors.append("per_turn_wer is missing")
    else:
        for field in (
            "reference_raw",
            "reference_normalized",
            "hypothesis_raw",
            "hypothesis_normalized",
            "substitutions",
            "deletions",
            "insertions",
            "reference_word_count",
            "wer",
        ):
            if field not in per_turn_wer or not is_present(per_turn_wer[field]):
                errors.append(f"per_turn_wer missing {field}")

    return errors


def corpus_wer(turns: list[dict[str, Any]]) -> dict[str, Any]:
    substitutions = sum(int(turn.get("wer_substitutions") or 0) for turn in turns)
    deletions = sum(int(turn.get("wer_deletions") or 0) for turn in turns)
    insertions = sum(int(turn.get("wer_insertions") or 0) for turn in turns)
    reference_words = sum(int(turn.get("wer_reference_words") or 0) for turn in turns)
    errors = substitutions + deletions + insertions
    return {
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_word_count": reference_words,
        "wer": round(errors / reference_words, 6) if reference_words else None,
    }


def latency_statistics(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "max": None, "mean": None, "median": None, "p50": None, "p95": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    median = ordered[(len(ordered) - 1) // 2]
    return {
        "min": round(ordered[0], 1),
        "max": round(ordered[-1], 1),
        "mean": round(sum(ordered) / len(ordered), 1),
        "median": round(median, 1),
        "p50": round(median, 1),
        "p95": round(ordered[p95_index], 1),
    }


def evaluate_acceptance_gates(
    turns: list[dict[str, Any]],
    *,
    required_turns: int,
    evidence: dict[str, Any],
    expected_engine: str = "remote",
    expected_provider: str | None = None,
    expected_language: str = "en",
) -> dict[str, Any]:
    """Evaluate the non-bypassable Phase 4 acceptance predicate."""

    turn_errors = {
        str(turn.get("turn_number", turn.get("turn"))): validate_turn_evidence(
            turn,
            expected_engine=expected_engine,
            expected_provider=expected_provider,
            expected_language=expected_language,
        )
        for turn in turns
    }
    all_turns_valid = bool(turns) and all(not errors for errors in turn_errors.values())
    required_numbers = list(range(1, required_turns + 1))
    actual_numbers = [turn.get("turn_number") for turn in turns]
    latency_values = [
        float(turn["speech_end_to_final_ms"])
        for turn in turns
        if is_number(turn.get("speech_end_to_final_ms"))
    ]
    remote_latency_values = [
        float(turn["remote_request_latency_ms"])
        for turn in turns
        if is_number(turn.get("remote_request_latency_ms"))
    ]
    client_latency_values = [
        float(turn["speech_end_to_client_delivery_ms"])
        for turn in turns
        if is_number(turn.get("speech_end_to_client_delivery_ms"))
    ]
    partial_count = sum(int(turn.get("partial_count", 0)) for turn in turns)
    turns_with_partials = sum(1 for turn in turns if int(turn.get("partial_count", 0)) > 0)
    resources_ok = bool(evidence.get("backend_resource_samples"))
    if expected_engine == "remote":
        resources_ok = resources_ok and bool(evidence.get("remote_request_samples"))
    else:
        resources_ok = resources_ok and bool(evidence.get("worker_resource_samples"))
    reconnect = evidence.get("reconnect") or {}
    reconnect_ok = bool(
        reconnect.get("disconnect_observed")
        and reconnect.get("reconnect_observed")
        and reconnect.get("audio_accepted_after_reconnect")
        and reconnect.get("stt_continued_after_reconnect")
        and reconnect.get("backend_cleanup_observed")
        and reconnect.get("redis_cleanup_observed")
        and reconnect.get("new_session_id")
        and reconnect.get("initial_session_id") != reconnect.get("new_session_id")
        and reconnect.get("post_reconnect_remote_http_status") == 200
        and reconnect.get("post_reconnect_final_delivered")
    )
    gates = {
        "preflight_complete": bool(evidence.get("preflight_pass")),
        "rotated_remote_credential_configured": bool(
            evidence.get("rotated_remote_credential_configured")
        ),
        "tracked_secret_scan": bool(evidence.get("tracked_secret_scan_pass")),
        "mandatory_evidence_files": bool(evidence.get("mandatory_evidence_files")),
        "completed_turns": len(turns) == required_turns and actual_numbers == required_numbers,
        "references_stored_before_recognition": bool(
            evidence.get("references_stored_before_recognition")
        )
        and actual_numbers == required_numbers,
        "final_hypotheses_captured": len(turns) == required_turns
        and all(is_present(turn.get("final_transcript")) for turn in turns),
        "mandatory_turn_evidence": all_turns_valid,
        "monotonic_timestamps": all_turns_valid
        and all(is_number(turn.get("turn_start_monotonic_ms")) for turn in turns),
        "wer_measured": len(turns) == required_turns
        and all(is_number(turn.get("wer")) for turn in turns)
        and corpus_wer(turns)["wer"] is not None,
        "physical_latency_measured": len(latency_values) == required_turns
        and len(remote_latency_values) == required_turns
        and len(client_latency_values) == required_turns,
        "remote_final_only_contract": len(turns) == required_turns
        and all(
            turn.get("partial_supported") is False
            and turn.get("partial_count") == 0
            and is_present(turn.get("no_partial_reason"))
            for turn in turns
        ),
        "resource_measurements": resources_ok,
        "physical_reconnect": reconnect_ok,
        "physical_device_visible": bool(evidence.get("android_device")),
        "apk_install_launch": bool(evidence.get("apk_install_launch")),
        "websocket_physical_path": bool(evidence.get("websocket_physical_path")),
        "remote_stt_deployment": expected_engine == "remote"
        and bool(evidence.get("remote_stt_deployment")),
        "automated_validation": bool(evidence.get("automated_validation_passed")),
        "redis_session_cleanup": bool(evidence.get("redis_session_cleanup_pass")),
        "remote_stt_only": expected_engine == "remote"
        and all(
            turn.get("stt_engine") == "remote"
            and is_present(turn.get("stt_provider"))
            and (
                expected_provider is None
                or turn.get("stt_provider") == expected_provider
            )
            and turn.get("language", "").casefold() == expected_language.casefold()
            for turn in turns
        ),
        "no_turn_failures": len(turns) == required_turns
        and all(turn.get("status") == "PASS" and not turn.get("error") for turn in turns),
    }
    return {
        "gates": gates,
        "pass": all(gates.values()),
        "turn_errors": turn_errors,
        "turns_with_partials": turns_with_partials,
        "total_partials": partial_count,
        "partial_coverage_percentage": round(100 * turns_with_partials / required_turns, 1)
        if required_turns
        else 0.0,
        "latency": latency_statistics(latency_values),
        "remote_request_latency": latency_statistics(remote_latency_values),
        "speech_end_to_client_delivery": latency_statistics(client_latency_values),
        "corpus_wer": corpus_wer(turns),
    }
