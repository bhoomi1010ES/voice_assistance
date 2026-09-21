"""Validate metadata-only Phase 8 acoustic acceptance evidence.

The validator is deliberately evidence-driven. Missing measurements remain
``pending``; a failed target is only accepted when a route/device fallback is
explicitly documented. This file never reads or stores PCM, transcripts,
tokens, prompts, or provider credentials.
"""

# Evidence detail strings intentionally preserve threshold context in one line.
# The repository's Ruff E501 setting otherwise flags those diagnostic strings.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "backend" / "tests" / "fixtures" / "phase8_acoustic_fixtures_v1.json"
FORBIDDEN_KEYS = {
    "transcript",
    "pcm",
    "pcm_data",
    "audio_bytes",
    "tokens",
    "provider_secrets",
    "authorization",
    "access_token",
    "refresh_token",
}


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records


def _redaction_errors(value: Any, path: str = "$", errors: list[str] | None = None) -> list[str]:
    errors = errors if errors is not None else []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_KEYS and child not in (None, "", [], {}):
                errors.append(f"{path}.{key} contains forbidden acoustic content")
            _redaction_errors(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _redaction_errors(child, f"{path}[{index}]", errors)
    return errors


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _metric(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _target(name: str, passed: bool | None, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if passed is True else "fail" if passed is False else "pending",
        "detail": detail,
    }


def _zero_target(
    payload: dict[str, Any], name: str, path: tuple[str, ...], detail: str
) -> dict[str, Any]:
    value = _number(_metric(payload, *path))
    return _target(
        name,
        None if value is None else value == 0,
        detail if value is None else f"observed={int(value)}; {detail}",
    )


def evaluate_evidence(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    redaction_errors = _redaction_errors(payload)
    if payload and payload.get("metadata_only") is not True:
        redaction_errors.append("$.metadata_only must be true")
    targets: list[dict[str, Any]] = []

    expected_fixtures = {
        fixture["id"]: fixture["expected"]["classification"]
        for fixture in manifest.get("fixtures", [])
    }
    raw_fixture_results = payload.get("fixture_results", [])
    if not isinstance(raw_fixture_results, list):
        raw_fixture_results = []
    observed_fixtures = {
        result.get("fixture_id"): result
        for result in raw_fixture_results
        if isinstance(result, dict) and isinstance(result.get("fixture_id"), str)
    }
    missing_fixture_ids = [
        fixture_id for fixture_id in expected_fixtures if fixture_id not in observed_fixtures
    ]
    mismatched_fixture_ids = [
        fixture_id
        for fixture_id, expected_classification in expected_fixtures.items()
        if fixture_id in observed_fixtures
        and observed_fixtures[fixture_id].get("classification") != expected_classification
    ]
    uncorrelated_fixture_ids = [
        fixture_id
        for fixture_id, result in observed_fixtures.items()
        if fixture_id in expected_fixtures
        and any(
            result.get(field) in (None, "")
            for field in ("session_id", "response_id", "reason", "frame_start", "frame_end")
        )
    ]
    fixture_pass = None
    if observed_fixtures:
        fixture_pass = (
            not missing_fixture_ids and not mismatched_fixture_ids and not uncorrelated_fixture_ids
        )
    targets.append(
        _target(
            "recorded_fixture_classification_and_correlation",
            fixture_pass,
            f"manifest={len(expected_fixtures)}; observed={len(observed_fixtures)}; "
            f"missing={len(missing_fixture_ids)}; mismatched={len(mismatched_fixture_ids)}; "
            f"uncorrelated={len(uncorrelated_fixture_ids)}",
        )
    )

    responses = _number(_metric(payload, "tts_only", "completed_responses"))
    false_barge_ins = _number(_metric(payload, "tts_only", "false_barge_ins"))
    tts_pass = (
        None
        if responses is None or false_barge_ins is None
        else responses >= 100 and false_barge_ins == 0
    )
    targets.append(
        _target(
            "tts_only_false_barge_in",
            tts_pass,
            f"responses={responses}; false_barge_ins={false_barge_ins}; required responses>=100 and false_barge_ins=0",
        )
    )

    soak_minutes = _number(_metric(payload, "soak", "duration_minutes"))
    soak_false = _number(_metric(payload, "soak", "false_barge_ins"))
    soak_pass = (
        None
        if soak_minutes is None or soak_false is None
        else soak_minutes >= 30 and soak_false == 0
    )
    targets.append(
        _target(
            "continuous_tts_only_soak",
            soak_pass,
            f"duration_minutes={soak_minutes}; false_barge_ins={soak_false}; required duration>=30 and false_barge_ins=0",
        )
    )

    attempts = _number(_metric(payload, "true_barge_in", "eligible_attempts"))
    detected = _number(_metric(payload, "true_barge_in", "detected"))
    detection_rate = None if attempts in (None, 0) or detected is None else detected / attempts
    true_pass = None if detection_rate is None else attempts > 0 and detection_rate >= 0.95
    targets.append(
        _target(
            "true_barge_in_detection",
            true_pass,
            f"eligible_attempts={attempts}; detected={detected}; rate={detection_rate}; required rate>=0.95",
        )
    )

    early_attempts = _number(_metric(payload, "early_interruption", "attempted"))
    early_failures = _number(_metric(payload, "early_interruption", "failures"))
    early_pass = (
        None
        if early_attempts is None or early_failures is None
        else early_attempts > 0 and early_failures == 0
    )
    targets.append(
        _target(
            "early_interruption",
            early_pass,
            f"attempted={early_attempts}; failures={early_failures}; required failures=0",
        )
    )

    local_p95 = _number(_metric(payload, "latency_ms", "local_stop_p95"))
    local_pass = None if local_p95 is None else local_p95 <= 350
    targets.append(
        _target("local_stop_latency_p95", local_pass, f"p95_ms={local_p95}; required <=350")
    )

    confirmation_p95 = _number(_metric(payload, "latency_ms", "post_confirmation_stop_request_p95"))
    confirmation_pass = None if confirmation_p95 is None else confirmation_p95 <= 50
    targets.append(
        _target(
            "post_confirmation_stop_latency_p95",
            confirmation_pass,
            f"p95_ms={confirmation_p95}; required <=50",
        )
    )

    first_word_attempts = _number(_metric(payload, "replacement_turns", "attempted"))
    first_word_retained = _number(_metric(payload, "replacement_turns", "first_word_retained"))
    first_word_pass = (
        None
        if first_word_attempts in (None, 0) or first_word_retained is None
        else first_word_retained == first_word_attempts
    )
    targets.append(
        _target(
            "first_word_retention",
            first_word_pass,
            f"attempted={first_word_attempts}; retained={first_word_retained}; required 100%",
        )
    )

    targets.extend(
        [
            _zero_target(
                payload,
                "duplicate_replacement_turns",
                ("replacement_turns", "duplicates"),
                "required zero",
            ),
            _zero_target(
                payload,
                "stale_old_response_playback",
                ("playback", "stale_old_response_count"),
                "required zero",
            ),
            _zero_target(payload, "pcm_pipeline_drops", ("drops", "pcm_pipeline"), "required zero"),
            _zero_target(payload, "silero_processing_drops", ("drops", "silero"), "required zero"),
            _zero_target(payload, "aec_processing_drops", ("drops", "aec"), "required zero"),
        ]
    )

    turns = _number(_metric(payload, "runtime", "duplex_turns"))
    stability_fields = ("crashes", "anrs", "audio_state_leaks", "stuck_microphone")
    stability_values = [_number(_metric(payload, "runtime", field)) for field in stability_fields]
    stability_pass = (
        None
        if turns is None or any(value is None for value in stability_values)
        else turns >= 20 and all(value == 0 for value in stability_values)
    )
    targets.append(
        _target(
            "runtime_stability",
            stability_pass,
            f"duplex_turns={turns}; counters={dict(zip(stability_fields, stability_values, strict=True))}; required turns>=20 and all counters=0",
        )
    )

    profiles = _number(_metric(payload, "speech_quality", "profiles_tested"))
    quality_failures = _number(_metric(payload, "speech_quality", "failures"))
    quality_pass = (
        None
        if profiles is None or quality_failures is None
        else profiles >= 4 and quality_failures == 0
    )
    targets.append(
        _target(
            "speech_quality_profiles",
            quality_pass,
            f"profiles_tested={profiles}; failures={quality_failures}; required profiles>=4 and failures=0",
        )
    )

    failed_targets = [item for item in targets if item["status"] == "fail"]
    pending_targets = [item for item in targets if item["status"] == "pending"]
    fallback = payload.get("fallback") if isinstance(payload.get("fallback"), dict) else {}
    documented_fallback = bool(fallback.get("documented")) and bool(
        fallback.get("route_or_profile")
    )
    undocumented_failure = bool(failed_targets) and not documented_fallback
    if redaction_errors or undocumented_failure:
        gate_status = "blocked"
    elif failed_targets:
        gate_status = "fallback_documented"
    elif pending_targets:
        gate_status = "pending"
    else:
        gate_status = "pass"

    return {
        "schema": "phase8-acceptance-report-v1",
        "metadata_only": True,
        "manifest_version": manifest.get("manifest_version"),
        "redaction_errors": redaction_errors,
        "target_results": targets,
        "fixture_validation": {
            "missing_fixture_ids": missing_fixture_ids,
            "mismatched_fixture_ids": mismatched_fixture_ids,
            "uncorrelated_fixture_ids": uncorrelated_fixture_ids,
        },
        "gate_status": gate_status,
        "fallback": {
            "documented": documented_fallback,
            "route_or_profile": fallback.get("route_or_profile"),
            "reason": fallback.get("reason"),
        },
        "fixture_count": len(manifest.get("fixtures", [])),
        "physical_matrix": manifest.get("physical_matrix", {}),
    }


def template_payload() -> dict[str, Any]:
    return {
        "schema": "phase8-acceptance-evidence-v1",
        "metadata_only": True,
        "run_id": "replace-with-session-owned-run-id",
        "device": {"model": None, "serial_alias": None, "os_api": None},
        "matrix_case": {
            "route": None,
            "volume_percent": None,
            "distance_m": None,
            "environment": None,
            "speech_timing": None,
            "effect_profile": None,
            "voice": None,
        },
        "tts_only": {"completed_responses": None, "false_barge_ins": None},
        "soak": {"duration_minutes": None, "false_barge_ins": None},
        "true_barge_in": {"eligible_attempts": None, "detected": None},
        "early_interruption": {"attempted": None, "failures": None},
        "latency_ms": {"local_stop_p95": None, "post_confirmation_stop_request_p95": None},
        "replacement_turns": {"attempted": None, "first_word_retained": None, "duplicates": None},
        "playback": {"stale_old_response_count": None},
        "drops": {"pcm_pipeline": None, "silero": None, "aec": None},
        "runtime": {
            "duplex_turns": None,
            "crashes": None,
            "anrs": None,
            "audio_state_leaks": None,
            "stuck_microphone": None,
        },
        "speech_quality": {"profiles_tested": None, "failures": None},
        "fallback": {"documented": False, "route_or_profile": None, "reason": None},
        "fixture_results": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write-template", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.write_template:
        args.write_template.parent.mkdir(parents=True, exist_ok=True)
        args.write_template.write_text(
            json.dumps(template_payload(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote metadata-only evidence template: {args.write_template}")
    payload: dict[str, Any] = {}
    if args.evidence:
        loaded = load_json_or_jsonl(args.evidence)
        if isinstance(loaded, list):
            payload = {"metadata_only": True, "records": loaded}
        elif isinstance(loaded, dict):
            payload = loaded
        else:
            raise SystemExit("Evidence must be a JSON object or JSONL record list")
    report = evaluate_evidence(payload, manifest)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["gate_status"] in {"pass", "pending", "fallback_documented"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
