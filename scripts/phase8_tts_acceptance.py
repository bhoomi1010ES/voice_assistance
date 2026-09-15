"""Run the Phase 8 physical TTS evidence capture on one Android device.

The script never records PCM, transcripts, tokens, or authorization headers. It
only retains the diagnostic event lines emitted by the native TTS transport.
The human operator must confirm audibility separately.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERIAL = "9b0ea196"
PACKAGE = "com.voiceaipoc"
EVENT_TAGS = ("VoiceAI-VoiceGateway", "VoiceAI-TTS")
RESPONSE_RE = re.compile(r"response_id=([0-9a-f-]{36})")
FIELD_RE = re.compile(r"([a-zA-Z_]+)=([^ ]+)")


def run_adb(serial: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["adb", "-s", serial, *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def check_http(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def parse_line(line: str) -> dict[str, object] | None:
    if not any(tag in line for tag in EVENT_TAGS):
        return None
    event: str | None = None
    if "WS_FAILURE" in line:
        event = "WS_FAILURE"
    elif "WS_CLOSING" in line:
        event = "WS_CLOSING"
    elif "WS_CLOSED" in line:
        event = "WS_CLOSED"
    else:
        for candidate in (
            "TTS_RESPONSE_SUMMARY",
            "TTS_FIRST_PCM_WRITE",
            "TTS_PLAY_STARTED",
            "TTS_PREBUFFER_READY",
            "TTS_FRAME_ENQUEUED",
            "TTS_FIRST_AUDIO_RECEIVED",
            "TTS_FRAME_RECEIVED",
            "TTS_START",
            "TTS_PLAYBACK_COMPLETED",
            "TTS_PLAYBACK_ERROR",
            "TTS_END_RECEIVED",
        ):
            if candidate in line:
                event = candidate
                break
    if event is None:
        return None
    record: dict[str, object] = {"event": event, "raw_sanitized": line.strip()}
    response_match = RESPONSE_RE.search(line)
    if response_match:
        record["response_id"] = response_match.group(1)
    for key, value in FIELD_RE.findall(line):
        if key in {"seq", "bytes", "written_bytes", "requested_bytes", "frames_received", "frames_enqueued", "frames_written", "pcm_bytes_received", "pcm_bytes_enqueued", "pcm_bytes_written", "sequence_gaps", "duplicates", "stale_frames", "partial_writes", "write_errors", "underrun_delta", "elapsedMs", "wallMs", "http_code"}:
            try:
                record[key] = int(value)
            except ValueError:
                pass
        elif key in {"initiator", "reason", "exception", "message", "last_server_event", "sample_rate", "encoding", "channels", "session_id", "turn_id"}:
            record[key] = value
        elif key == "ws_connected_after_playback":
            record[key] = value.lower() == "true"
    return record


def summarize(
    events: list[dict[str, object]],
    output_dir: Path,
    backend_metrics: dict[str, dict[str, object]],
) -> None:
    by_response: dict[str, list[dict[str, object]]] = {}
    for event in events:
        response_id = event.get("response_id")
        if isinstance(response_id, str):
            by_response.setdefault(response_id, []).append(event)

    records: list[dict[str, object]] = []
    for response_id, response_events in by_response.items():
        names = {event["event"] for event in response_events}
        session_id = next(
            (event.get("session_id") for event in response_events if event.get("session_id")),
            None,
        )
        turn_id = next(
            (event.get("turn_id") for event in response_events if event.get("turn_id")),
            None,
        )
        summary = next(
            (event for event in response_events if event["event"] == "TTS_RESPONSE_SUMMARY"),
            {},
        )
        first_audio = next(
            (event for event in response_events if event["event"] == "TTS_FIRST_AUDIO_RECEIVED"),
            {},
        )
        play_started = next(
            (event for event in response_events if event["event"] == "TTS_PLAY_STARTED"),
            {},
        )
        metrics = backend_metrics.get(response_id, {})
        first_audio_elapsed = first_audio.get("elapsedMs")
        play_started_elapsed = play_started.get("elapsedMs")
        first_audio_to_playback = None
        if isinstance(first_audio_elapsed, int) and isinstance(play_started_elapsed, int):
            first_audio_to_playback = max(0, play_started_elapsed - first_audio_elapsed)
        record = {
            "session_id": session_id,
            "turn_id": turn_id,
            "response_id": response_id,
            "prompt": None,
            "tts": {
                "sample_rate": 24000,
                "channels": 1,
                "encoding": "pcm16",
                "prebuffer_ms": 200,
                "frames_received": summary.get("frames_received"),
                "frames_written": summary.get("frames_written"),
                "sequence_gaps": summary.get("sequence_gaps"),
                "duplicate_frames": summary.get("duplicates"),
                "stale_frames": summary.get("stale_frames"),
                "partial_writes": summary.get("partial_writes"),
                "write_errors": summary.get("write_errors"),
                "underrun_delta": summary.get("underrun_delta"),
            },
            "latency_ms": {
                "tts_request_to_first_audio": None,
                "first_audio_to_playback": first_audio_to_playback,
                "llm_complete_to_playback": None,
                "speech_end_to_playback": None,
                "tts_generation": metrics.get("tts_generation_ms"),
                "tts_audio_duration": metrics.get("tts_audio_duration_ms"),
            },
            "rtf": metrics.get("tts_rtf"),
            "websocket": {
                "connected_at_start": "TTS_START" in names,
                "connected_after_playback": summary.get("ws_connected_after_playback"),
                "unexpected_close": "WS_FAILURE" in names,
                "socket_exception": next(
                    (event.get("exception") for event in response_events if event["event"] == "WS_FAILURE"),
                    None,
                ),
            },
            "physical_audio_confirmed": None,
            "result": "pending",
        }
        records.append(record)

    output_path = output_dir / "tts_acceptance.jsonl"
    output_path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    print(f"Observed TTS responses: {len(records)}")
    print(f"Evidence JSONL: {output_path}")
    if not records:
        print("FIRST PCM PATH: PENDING — no TTS response was observed")
        return
    for record in records:
        tts = record["tts"]
        first_path = {"TTS_FIRST_AUDIO_RECEIVED", "TTS_FRAME_ENQUEUED", "TTS_PREBUFFER_READY", "TTS_PLAY_STARTED", "TTS_FIRST_PCM_WRITE"}
        observed = {event["event"] for event in by_response[record["response_id"]]}
        result = "PASS" if first_path <= observed else "PENDING"
        print(f"response={record['response_id']} FIRST PCM PATH: {result}")
        if isinstance(tts, dict):
            print(
                "  frames_received={frames_received} frames_written={frames_written} "
                "gaps={sequence_gaps} duplicates={duplicate_frames} stale={stale_frames} "
                "write_errors={write_errors} underrun_delta={underrun_delta}".format(**tts)
            )


def capture_backend_evidence(output_dir: Path) -> dict[str, dict[str, object]]:
    candidates = sorted(
        (ROOT / "backend").glob("server_*.err.log"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        return {}
    source = candidates[-1]
    safe_events = {
        "voice.connection.opened",
        "voice.connection.closed",
        "voice.session.started",
        "tts.start",
        "tts.frame.sent",
        "tts.response.metrics",
        "tts.send.failed",
        "voice.auth.revalidation.failed",
    }
    selected: list[str] = []
    metrics_by_response: dict[str, dict[str, object]] = {}
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("event") not in safe_events:
            continue
        safe = {
            key: item[key]
            for key in (
                "timestamp",
                "event",
                "session_id",
                "turn_id",
                "response_id",
                "sequence",
                "payload_bytes",
                "flags",
                "sample_rate_hz",
                "tts_generation_ms",
                "tts_audio_duration_ms",
                "tts_rtf",
                "pcm_bytes",
                "exception",
            )
            if key in item
        }
        selected.append(json.dumps(safe, separators=(",", ":")))
        response_id = item.get("response_id")
        if item.get("event") == "tts.response.metrics" and isinstance(response_id, str):
            metrics_by_response[response_id] = safe
    (output_dir / "backend_tts_events.jsonl").write_text(
        "\n".join(selected) + ("\n" if selected else ""),
        encoding="utf-8",
    )
    print(f"Backend evidence source: {source}")
    return metrics_by_response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    serial = args.serial
    state = run_adb(serial, "get-state")
    if state != "device":
        print(f"ADB device is not ready: {state or 'missing'}", file=sys.stderr)
        return 2
    model = run_adb(serial, "shell", "getprop", "ro.product.model")
    print(f"Device: {model} ({serial})")
    for port in ("8000", "8081"):
        run_adb(serial, "reverse", f"tcp:{port}", f"tcp:{port}")
    for path in ("/health", "/ready"):
        status, body = check_http(path)
        print(f"{path}: {status}")
        if status != 200:
            print(body, file=sys.stderr)
            return 3

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or ROOT / "docs" / "evidence" / "phase8" / f"physical_acceptance_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_adb(serial, "logcat", "-c")
    if not args.no_launch:
        run_adb(serial, "shell", "am", "force-stop", PACKAGE)
        run_adb(serial, "shell", "monkey", "-p", PACKAGE, "1")

    log_path = output_dir / "android.log"
    logcat_filters = [part for tag in EVENT_TAGS for part in ("-s", tag + ":V")]
    logcat = subprocess.Popen(
        ["adb", "-s", serial, "logcat", "-v", "threadtime", *logcat_filters],
        cwd=ROOT,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )
    print("Speak the Phase 8 sequence in the running app:")
    print('1) "What time is it?"  2) one moon fact  3) five-sentence robot story')
    print('4) explain AI in three sentences  5) "What is today\'s date?"')
    print('Then say another robot story, press Stop voice output, and ask "What time is it?".')
    print(f"Capturing for {args.duration_seconds}s; stop with Ctrl+C after the sequence.")
    try:
        time.sleep(args.duration_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        logcat.terminate()
        try:
            logcat.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logcat.kill()
        if logcat.stdout:
            logcat.stdout.close()

    events = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_line(line)
        if parsed is not None:
            events.append(parsed)
    backend_metrics = capture_backend_evidence(output_dir)
    summarize(events, output_dir, backend_metrics)
    print("physical_audio_confirmed remains null until the human operator confirms audibility.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
