"""Run metadata-only Phase 7 acoustic trials on an Android device.

The runner divides one session into TTS-only, user-only, and double-talk
windows. It prints instructions for the operator, captures only allow-listed
native diagnostic fields, and writes redacted JSON evidence. It never stores
logcat lines, transcript text, PCM, tokens, or provider credentials.
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "com.voiceaipoc"
DEFAULT_SERIAL = "9b0ea196"
SCENARIOS = ("tts-only", "user-only", "double-talk")
LOGCAT_TAGS = ("VoiceAI-Bridge", "VoiceAI-Audio", "VoiceAI-Route", "VoiceAI-TTS")
FIELD_RE = re.compile(r"([a-zA-Z_]+)=([^\s]+)")
SOURCE_FRAMES_RE = re.compile(r"^(\d+)-(-?\d+)$")
CAPTURE_RE = re.compile(r"^(-?\d+)-(-?\d+)$")


def run_adb(serial: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["adb", "-s", serial, *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _integer(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def parse_diagnostic_line(line: str, *, scenario: str) -> dict[str, Any] | None:
    """Parse one allow-listed native line without retaining the original line."""

    if "BARGE_IN_DECISION" in line:
        event = "BARGE_IN_DECISION"
        marker = line.split("BARGE_IN_DECISION", 1)[1]
    elif "SILERO_VAD_SPEECH_STARTED" in line:
        event = "SILERO_VAD_SPEECH_STARTED"
        marker = line.split("SILERO_VAD_SPEECH_STARTED", 1)[1]
    elif "SILERO_VAD_SPEECH_STOPPED" in line:
        event = "SILERO_VAD_SPEECH_STOPPED"
        marker = line.split("SILERO_VAD_SPEECH_STOPPED", 1)[1]
    else:
        return None

    fields = dict(FIELD_RE.findall(marker))
    result: dict[str, Any] = {
        "scenario": scenario,
        "event": event,
    }
    diagnostic_match = re.search(r"\bdiagnostic_session_id=([^\s]+)", line)
    diagnostic_session_id = diagnostic_match.group(1) if diagnostic_match else fields.get("diagnostic_session_id")
    if diagnostic_session_id:
        result["diagnostic_session_id"] = diagnostic_session_id
    if event == "BARGE_IN_DECISION":
        if "event" in fields:
            result["event_name"] = fields["event"]
        for key in ("state", "reason", "response_id", "timestamp_confidence"):
            if key in fields:
                result[key] = fields[key]
        source_match = SOURCE_FRAMES_RE.match(fields.get("source_frames", ""))
        if source_match:
            result["source_frame_sequence_start"] = int(source_match.group(1))
            result["source_frame_sequence_end"] = int(source_match.group(2))
        capture_match = CAPTURE_RE.match(fields.get("capture", ""))
        if capture_match:
            result["capture_start_ns"] = capture_match.group(1)
            result["capture_end_ns"] = capture_match.group(2)
        for key in ("inference", "local_stop_latency_ms"):
            if key in fields and fields[key] != "NONE":
                parsed = _integer(fields[key])
                if parsed is not None:
                    result[
                        "inference_index"
                        if key == "inference"
                        else "local_stop_latency_ms"
                    ] = parsed
        if "reference_ready" in fields:
            result["reference_ready"] = fields["reference_ready"].lower() == "true"
        result["correlation_complete"] = all(
            key in result
            for key in (
                "diagnostic_session_id",
                "response_id",
                "source_frame_sequence_start",
                "source_frame_sequence_end",
                "inference_index",
                "capture_start_ns",
                "capture_end_ns",
            )
        )
    return result


def summarize_trial(scenario: str, events: Iterable[dict[str, Any]], fallback_id: str) -> dict[str, Any]:
    selected = [event for event in events if event.get("scenario") == scenario]
    session_ids = sorted(
        {
            value
            for event in selected
            if isinstance((value := event.get("diagnostic_session_id")), str)
            and value
        }
    )
    decisions = [event for event in selected if event["event"] == "BARGE_IN_DECISION"]
    counts = {name: 0 for name in ("BARGE_IN_CANDIDATE", "BARGE_IN_REJECTED_ECHO", "BARGE_IN_CONFIRMED", "BARGE_IN_DEGRADED")}
    for decision in decisions:
        event_name = decision.get("event_name")
        if event_name in counts:
            counts[event_name] += 1
    # The parser records the marker as BARGE_IN_DECISION; extract the actual
    # decision name from a future event field when it is present.
    for decision in decisions:
        if decision.get("state") in counts:
            counts[decision["state"]] += 1
    diagnostic_session_id = session_ids[0] if session_ids else "UNKNOWN"
    return {
        "trial_id": f"{diagnostic_session_id}:{scenario}" if diagnostic_session_id != "UNKNOWN" else fallback_id,
        "scenario": scenario,
        "diagnostic_session_id": diagnostic_session_id,
        "event_count": len(selected),
        "decision_count": len(decisions),
        "decision_counts": counts,
        "correlated_decision_count": sum(
            1 for decision in decisions if decision.get("correlation_complete") is True
        ),
        "all_decisions_correlated": bool(decisions)
        and all(decision.get("correlation_complete") is True for decision in decisions),
        "speech_started": any(
            event["event"] == "SILERO_VAD_SPEECH_STARTED" for event in selected
        ),
        "events": selected,
    }


def build_evidence(events: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    return {
        "metadata_only": True,
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "redacted_fields": ["transcript", "pcm", "tokens", "provider_secrets", "raw_logcat"],
        "trials": [
            summarize_trial(
                scenario,
                events,
                f"{run_id}:{scenario}",
            )
            for scenario in SCENARIOS
        ],
    }


def read_log_file(path: Path, *, scenario: str = "double-talk") -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        event = parse_diagnostic_line(line, scenario=scenario)
        if event is not None:
            events.append(event)
    return events


def capture_live(serial: str, duration_seconds: int) -> list[dict[str, Any]]:
    command = ["adb", "-s", serial, "logcat", "-v", "threadtime"]
    for tag in LOGCAT_TAGS:
        command.extend(("-s", f"{tag}:V"))
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    lines: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            lines.put(line)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    events: list[dict[str, Any]] = []
    try:
        for index, scenario in enumerate(SCENARIOS):
            print(f"[{scenario}] {trial_instruction(scenario)}")
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                event = parse_diagnostic_line(line, scenario=scenario)
                if event is not None:
                    events.append(event)
            if index < len(SCENARIOS) - 1:
                print("Next trial begins now.")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return events


def trial_instruction(scenario: str) -> str:
    return {
        "tts-only": "trigger one assistant response, remain silent, and observe playback",
        "user-only": "keep assistant playback idle and speak one short sentence",
        "double-talk": "trigger assistant playback and speak over it for one sentence",
    }[scenario]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--trial-seconds", type=int, default=15)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--input-log", type=Path, help="Parse an existing sanitized/logcat fixture.")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()
    if args.trial_seconds <= 0:
        parser.error("--trial-seconds must be positive")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_id = f"phase7-{timestamp}"
    output_dir = args.output_dir or ROOT / "docs" / "evidence" / "phase7" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_log:
        events = read_log_file(args.input_log)
    else:
        state = run_adb(args.serial, "get-state")
        if state != "device":
            print(f"ADB device is not ready: {state or 'missing'}", file=sys.stderr)
            return 2
        run_adb(args.serial, "logcat", "-c")
        if not args.no_launch:
            run_adb(args.serial, "shell", "am", "force-stop", PACKAGE)
            run_adb(args.serial, "shell", "monkey", "-p", PACKAGE, "1")
        events = capture_live(args.serial, args.trial_seconds)

    evidence = build_evidence(events, run_id=run_id)
    evidence_path = output_dir / "phase7_trials.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Metadata-only evidence: {evidence_path}")
    for trial in evidence["trials"]:
        print(
            f"{trial['scenario']}: events={trial['event_count']} "
            f"decisions={trial['decision_count']} "
            f"correlated={trial['correlated_decision_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
