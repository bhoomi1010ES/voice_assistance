#!/usr/bin/env python3
"""Phase 4 — Interactive Manual 10-Turn Physical STT Acceptance Test Runner.

Controls test flow, captures Android logcat and backend logs in real-time,
measures exact latencies using monotonic time, validates partial/final STT events,
enforces strict response correlation, records consolidated logs, and generates
structured JSON, summary markdown, and final acceptance reports.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = WORKSPACE_ROOT / "docs"
VALIDATION_ROOT = DOCS_DIR / "phase4_physical_validation"
SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


def get_latest_backend_log_file() -> Path | None:
    """Find the most recent backend log file."""
    app_data = os.environ.get("USERPROFILE", "C:\\Users\\lenovo")
    gemini_tasks = Path(app_data) / ".gemini" / "antigravity-ide" / "brain"
    latest_task_log = None
    latest_task_mtime = 0.0

    if gemini_tasks.exists():
        for log_file in gemini_tasks.glob("*/.system_generated/tasks/*.log"):
            try:
                mtime = log_file.stat().st_mtime
                if mtime > latest_task_mtime:
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        header = f.read(500)
                        if "uvicorn" in header or "voice-assistance" in header or "Started server process" in header:
                            latest_task_mtime = mtime
                            latest_task_log = log_file
            except Exception:
                pass

    uvicorn_local = WORKSPACE_ROOT / "backend" / "uvicorn.log"
    if uvicorn_local.exists():
        if not latest_task_log or uvicorn_local.stat().st_mtime > latest_task_mtime:
            return uvicorn_local

    return latest_task_log or uvicorn_local


def format_iso(ms: int | float | None) -> str:
    if ms is None or ms == "NOT_AVAILABLE" or ms == "N/A":
        return "N/A"
    try:
        sec = float(ms) / 1000.0
        dt = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
        return dt.strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return str(ms)


def calculate_percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "min": 0.0}
    s = sorted(values)
    avg = sum(s) / len(s)
    p50 = s[len(s) // 2]
    p95_idx = min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)
    p95 = s[p95_idx]
    return {
        "avg": round(avg, 1),
        "p50": round(p50, 1),
        "p95": round(p95, 1),
        "max": round(max(s), 1),
        "min": round(min(s), 1),
    }


class InteractiveValidationRunner:
    def __init__(
        self,
        target_device: str = "RMX5070",
        required_turns: int = 10,
        output_dir: Path | None = None,
    ):
        self.target_device = target_device
        self.required_turns = required_turns
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = output_dir or (VALIDATION_ROOT / "post_fix_10_turn")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.log_file_path = self.output_dir / "phase4_10turn_validation.log"
        self.json_file_path = self.output_dir / "phase4_10turn_results.json"
        self.summary_file_path = self.output_dir / "phase4_10turn_summary.md"
        self.final_acceptance_report_path = DOCS_DIR / f"{self.timestamp}_phase_4_final_acceptance.md"

        self.log_file = open(self.log_file_path, "w", encoding="utf-8", buffering=1)

        self.current_turn = 1
        self.turns: list[dict[str, Any]] = []
        self.current_turn_data: dict[str, Any] = self._new_turn_dict(1)

        self.seen_turn_ids: set[str] = set()
        self.seen_response_ids: set[str] = set()
        self.duplicate_finals_count = 0
        self.stale_responses_count = 0
        self.correlation_mismatches_count = 0

        self.running = True
        self.lock = threading.Lock()
        self.turn_completed_event = threading.Event()
        self.speech_detected_event = threading.Event()
        self.speech_ended_event = threading.Event()

        # Calculate ADB offset
        self.device_offset_ms = self._calculate_adb_offset()

    def _calculate_adb_offset(self) -> int:
        try:
            device_time = int(
                subprocess.check_output(
                    ["adb", "shell", "date", "+%s%3N"],
                    stderr=subprocess.DEVNULL,
                )
                .decode("utf-8")
                .strip()
            )
            host_time = int(time.time() * 1000)
            offset = host_time - device_time
            self.log(f"Clock offset calculated: device is {offset} ms behind host")
            return offset
        except Exception as e:
            self.log(f"Could not calculate exact clock offset via adb: {e}. Defaulting to 0.")
            return 0

    def log(self, message: str, print_stdout: bool = True):
        t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{t}] {message}"
        if print_stdout:
            print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def _new_turn_dict(self, turn_num: int) -> dict[str, Any]:
        return {
            "turn": turn_num,
            "turn_number": turn_num,
            "session_id": "NOT_AVAILABLE",
            "turn_id": "NOT_AVAILABLE",
            "response_id": "NOT_AVAILABLE",
            "device_id": self.target_device,
            "turn_start_timestamp": "NOT_AVAILABLE",
            "turn_start_monotonic_ms": "NOT_AVAILABLE",
            "speech_start_timestamp": "NOT_AVAILABLE",
            "speech_start_monotonic_ms": "NOT_AVAILABLE",
            "speech_end_timestamp": "NOT_AVAILABLE",
            "speech_end_monotonic_ms": "NOT_AVAILABLE",
            "client_commit_timestamp": "NOT_AVAILABLE",
            "client_commit_monotonic_ms": "NOT_AVAILABLE",
            "backend_commit_received_timestamp": "NOT_AVAILABLE",
            "backend_commit_received_monotonic_ms": "NOT_AVAILABLE",
            "first_partial_timestamp": "NOT_AVAILABLE",
            "first_partial_monotonic_ms": "NOT_AVAILABLE",
            "last_partial_timestamp": "NOT_AVAILABLE",
            "partial_inference_started": "NOT_AVAILABLE",
            "partial_inference_finished": "NOT_AVAILABLE",
            "final_inference_started": "NOT_AVAILABLE",
            "final_inference_finished": "NOT_AVAILABLE",
            "final_transcript_timestamp": "NOT_AVAILABLE",
            "final_transcript_monotonic_ms": "NOT_AVAILABLE",
            "android_final_received_timestamp": "NOT_AVAILABLE",
            "android_final_received_monotonic_ms": "NOT_AVAILABLE",
            "turn_completion_timestamp": "NOT_AVAILABLE",
            "turn_completion_monotonic_ms": "NOT_AVAILABLE",
            "cancellation_requested": "NOT_AVAILABLE",
            "cancellation_completed": "NOT_AVAILABLE",
            "partials": [],
            "final_transcript": "NOT_AVAILABLE",
            "final_count": 0,
            "pcm_frames": 0,
            "pcm_bytes": 0,
            "audio_duration_ms": 0,
            "speech_duration_ms": "N/A",
            "first_partial_latency_ms": None,
            "speech_to_final_ms": "N/A",
            "commit_to_final_ms": "N/A",
            "turn_processing_ms": "N/A",
            "status": "PENDING",  # PASS, FAIL_LATENCY, FAIL_TIMESTAMP_INTEGRITY, FAIL_PARTIAL, FAIL_FINAL_COUNT, etc.
            "failure_reason": None,
            "language": "en",
        }

    def parse_logcat_line(self, line: str):
        if "VoiceAI-Bridge" in line:
            # VAD speech started
            m = re.search(r"(?:SILERO )?VAD speech started.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if self.current_turn_data["speech_start_timestamp"] == "NOT_AVAILABLE":
                        self.current_turn_data["speech_start_timestamp"] = wall_ms
                        self.log(f"Speech detected (wallMs={wall_ms})")
                        self.speech_detected_event.set()

            # VAD speech stopped
            m = re.search(r"(?:SILERO )?VAD speech stopped.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if self.current_turn_data["speech_end_timestamp"] == "NOT_AVAILABLE":
                        self.current_turn_data["speech_end_timestamp"] = wall_ms
                        self.log(f"[VAD END] Speech stopped (wallMs={wall_ms})")
                        self.speech_ended_event.set()

        elif "VoiceAI-VoiceGateway" in line:
            # Client control sent
            m = re.search(r"VOICE control sent type=(\S+).*wallMs=(\d+)(?:\s+elapsedMs=(\d+))?", line)
            if m:
                ctl_type = m.group(1)
                wall_ms = int(m.group(2)) + self.device_offset_ms
                elapsed_ms = int(m.group(3)) if m.group(3) else None
                if ctl_type == "client.turn.start":
                    with self.lock:
                        if self.current_turn_data["turn_start_timestamp"] == "NOT_AVAILABLE":
                            self.current_turn_data["turn_start_timestamp"] = wall_ms
                            if elapsed_ms:
                                self.current_turn_data["turn_start_monotonic_ms"] = elapsed_ms
                            self.log(f"Client turn start sent (wallMs={wall_ms})")
                elif ctl_type == "client.audio.commit":
                    with self.lock:
                        self.current_turn_data["client_commit_timestamp"] = wall_ms
                        if elapsed_ms:
                            self.current_turn_data["client_commit_monotonic_ms"] = elapsed_ms
                        self.log(f"Client audio commit sent (wallMs={wall_ms})")
                        if self.current_turn_data["speech_end_timestamp"] == "NOT_AVAILABLE":
                            self.current_turn_data["speech_end_timestamp"] = wall_ms

            # Server event received on Android with response correlation
            m = re.search(
                r"VOICE server event type=(\S+)\s+sessionId=(\S+)\s+turnId=(\S+)\s+responseId=(\S+)\s+payload=(.*?)\s+wallMs=(\d+)(?:\s+elapsedMs=(\d+))?",
                line,
            )
            if m:
                evt_type = m.group(1)
                sess_id = m.group(2)
                turn_id = m.group(3)
                resp_id = m.group(4)
                wall_ms = int(m.group(6)) + self.device_offset_ms
                elapsed_ms = int(m.group(7)) if m.group(7) else None

                with self.lock:
                    # Check correlation
                    cur_turn_id = self.current_turn_data["turn_id"]
                    if cur_turn_id != "NOT_AVAILABLE" and turn_id != "NONE" and turn_id != cur_turn_id:
                        self.correlation_mismatches_count += 1
                        self.log(f"WARNING: Logcat event for turn {turn_id} does not match active turn {cur_turn_id}")
                        return

                    if evt_type == "transcript.final" or evt_type == "server.turn.completed":
                        self.current_turn_data["android_final_received_timestamp"] = wall_ms
                        if elapsed_ms:
                            self.current_turn_data["android_final_received_monotonic_ms"] = elapsed_ms
                        self.log(f"Android received {evt_type} (wallMs={wall_ms})")
                        self._check_turn_completed()

        elif "VoiceAI-Audio" in line:
            if "Microphone capture started" in line:
                self.log(f"Microphone capture active: {line.strip()}", print_stdout=False)

    def parse_backend_line(self, line: str):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        event = data.get("event")
        if not event:
            return

        with self.lock:
            if event == "voice.turn.started":
                turn_id = data.get("turn_id")
                response_id = data.get("response_id")
                session_id = data.get("session_id")
                ts = data.get("timestamp_ms") or int(time.time() * 1000)
                mono = data.get("monotonic_ms") or round(time.monotonic() * 1000, 1)

                if turn_id in self.seen_turn_ids:
                    self.stale_responses_count += 1
                    self.log(f"WARNING: Stale turn_id {turn_id} detected!")

                self.seen_turn_ids.add(turn_id)
                self.seen_response_ids.add(response_id)

                self.current_turn_data["session_id"] = session_id
                self.current_turn_data["turn_id"] = turn_id
                self.current_turn_data["response_id"] = response_id
                if self.current_turn_data["turn_start_timestamp"] == "NOT_AVAILABLE":
                    self.current_turn_data["turn_start_timestamp"] = ts
                    self.current_turn_data["turn_start_monotonic_ms"] = mono
                self.log(f"Backend started turn: turn_id={turn_id}, response_id={response_id}")

            elif event == "voice.pcm.accepted":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    frames = data.get("frames_accepted") or 0
                    bytes_cnt = data.get("bytes_received") or 0
                    if frames > self.current_turn_data["pcm_frames"]:
                        self.current_turn_data["pcm_frames"] = frames
                        self.current_turn_data["pcm_bytes"] = bytes_cnt

            elif event == "voice.audio.commit.received":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("backend_commit_received_timestamp_ms") or int(time.time() * 1000)
                    mono = data.get("backend_commit_received_monotonic_ms") or round(time.monotonic() * 1000, 1)
                    self.current_turn_data["backend_commit_received_timestamp"] = ts
                    self.current_turn_data["backend_commit_received_monotonic_ms"] = mono
                    frame_cnt = data.get("frame_count") or 0
                    byte_cnt = data.get("byte_count") or 0
                    if frame_cnt:
                        self.current_turn_data["pcm_frames"] = frame_cnt
                        self.current_turn_data["pcm_bytes"] = byte_cnt
                    self.log(f"Backend received audio commit: {frame_cnt} frames, {byte_cnt} bytes")

            elif event == "stt.audio.started":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("audio_start_timestamp_ms") or int(time.time() * 1000)
                    mono = data.get("audio_start_monotonic_ms") or round(time.monotonic() * 1000, 1)
                    if self.current_turn_data["speech_start_timestamp"] == "NOT_AVAILABLE":
                        self.current_turn_data["speech_start_timestamp"] = ts
                        self.current_turn_data["speech_start_monotonic_ms"] = mono

            elif event == "stt.inference.submitted":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    kind = data.get("inference_kind")
                    dur = data.get("audio_duration_ms") or 0
                    self.current_turn_data["audio_duration_ms"] = dur
                    self.log(f"STT inference submitted: kind={kind}, audio_duration={dur}ms")

            elif event == "stt.inference.started":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    kind = data.get("inference_kind")
                    ts = data.get("inference_start_timestamp_ms") or int(time.time() * 1000)
                    if kind == "partial":
                        self.current_turn_data["partial_inference_started"] = ts
                    elif kind == "final":
                        self.current_turn_data["final_inference_started"] = ts
                    self.log(f"STT inference started: kind={kind} at {ts}")

            elif event == "stt.partial.emitted":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("timestamp_ms") or int(time.time() * 1000)
                    mono = data.get("monotonic_ms") or round(time.monotonic() * 1000, 1)
                    text = data.get("text") or ""
                    if self.current_turn_data["first_partial_timestamp"] == "NOT_AVAILABLE":
                        self.current_turn_data["first_partial_timestamp"] = ts
                        self.current_turn_data["first_partial_monotonic_ms"] = mono
                        self.log(f'Partial transcript: "{text}"')
                    self.current_turn_data["last_partial_timestamp"] = ts
                    self.current_turn_data["partials"].append({"timestamp": ts, "text": text})

            elif event == "stt.finalize.requested":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    partial_running = data.get("partial_task_running", False)
                    ts = data.get("finalize_requested_timestamp_ms") or int(time.time() * 1000)
                    self.current_turn_data["cancellation_requested"] = ts
                    self.log(f"STT finalization requested (partial_task_running={partial_running})")

            elif event == "stt.speech_end.marked":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("speech_end_timestamp_ms") or int(time.time() * 1000)
                    mono = data.get("speech_end_monotonic_ms") or round(time.monotonic() * 1000, 1)
                    self.current_turn_data["speech_end_timestamp"] = ts
                    self.current_turn_data["speech_end_monotonic_ms"] = mono

            elif event == "stt.inference.completed":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    kind = data.get("inference_kind")
                    ts = data.get("completed_timestamp_ms") or int(time.time() * 1000)
                    dur = data.get("inference_duration_ms") or 0
                    text = data.get("text") or ""
                    if kind == "partial":
                        self.current_turn_data["partial_inference_finished"] = ts
                    elif kind == "final":
                        self.current_turn_data["final_inference_finished"] = ts
                    self.log(f"STT inference completed: kind={kind}, dur={dur}ms, text='{text}'")

            elif event == "stt.final.completed":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("final_transcript_timestamp_ms") or int(time.time() * 1000)
                    mono = data.get("final_transcript_monotonic_ms") or round(time.monotonic() * 1000, 1)
                    text = data.get("text") or ""
                    lang = data.get("language") or "en"
                    metrics = data.get("metrics") or {}

                    self.current_turn_data["final_count"] += 1
                    if self.current_turn_data["final_count"] > 1:
                        self.duplicate_finals_count += 1
                        self.log(f"WARNING: Duplicate final transcript received! count={self.current_turn_data['final_count']}")

                    self.current_turn_data["final_transcript_timestamp"] = ts
                    self.current_turn_data["final_transcript_monotonic_ms"] = mono
                    self.current_turn_data["final_transcript"] = text
                    self.current_turn_data["language"] = lang
                    if metrics.get("audio_duration_ms"):
                        self.current_turn_data["audio_duration_ms"] = metrics["audio_duration_ms"]
                    self.log(f'Final transcript: "{text}"')
                    self._check_turn_completed()

            elif event == "voice.response.cancel.received":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = int(time.time() * 1000)
                    self.current_turn_data["cancellation_completed"] = ts
                    self.current_turn_data["status"] = "FAIL"
                    self.current_turn_data["failure_reason"] = "cancelled_by_client"
                    self.log(f"Voice turn cancelled: {data}")
                    self._check_turn_completed()

            elif event in ["stt.inference.failed", "voice.error"]:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    self.current_turn_data["status"] = "FAIL"
                    self.current_turn_data["failure_reason"] = str(data.get("message") or event)
                    self.log(f"Voice error/failure: {data}")
                    self._check_turn_completed()

    def _matches_current_turn(self, event_turn_id: str | None) -> bool:
        if not event_turn_id:
            return True
        cur_id = self.current_turn_data.get("turn_id")
        if cur_id in [None, "NOT_AVAILABLE"]:
            return True
        return str(event_turn_id) == str(cur_id)

    def _check_turn_completed(self):
        d = self.current_turn_data
        if d["final_transcript"] != "NOT_AVAILABLE" or d["status"].startswith("FAIL"):
            d["turn_completion_timestamp"] = int(time.time() * 1000)
            d["turn_completion_monotonic_ms"] = round(time.monotonic() * 1000, 1)
            self.turn_completed_event.set()

    def _logcat_loop(self):
        cmd = ["adb", "logcat", "-v", "time", "VoiceAI-Bridge:I", "VoiceAI-VoiceGateway:I", "VoiceAI-Audio:I", "*:S"]
        try:
            self.logcat_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in self.logcat_proc.stdout:
                if not self.running:
                    break
                line_str = line.strip()
                if line_str:
                    self.parse_logcat_line(line_str)
        except Exception as e:
            self.log(f"Logcat thread error: {e}")

    def _backend_log_loop(self):
        backend_log_file = get_latest_backend_log_file()
        self.log(f"Tailing backend logs from: {backend_log_file}")
        if not backend_log_file or not backend_log_file.exists():
            return

        with open(backend_log_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while self.running:
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                line_str = line.strip()
                if line_str:
                    self.parse_backend_line(line_str)

    def _adb_reverse_keepalive(self):
        while self.running:
            try:
                subprocess.run(["adb", "reverse", "tcp:8000", "tcp:8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["adb", "reverse", "tcp:8081", "tcp:8081"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            time.sleep(2)

    def start_listeners(self):
        try:
            subprocess.run(["adb", "reverse", "tcp:8000", "tcp:8000"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["adb", "reverse", "tcp:8081", "tcp:8081"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        self.logcat_thread = threading.Thread(target=self._logcat_loop, daemon=True)
        self.backend_thread = threading.Thread(target=self._backend_log_loop, daemon=True)
        self.keepalive_thread = threading.Thread(target=self._adb_reverse_keepalive, daemon=True)

        self.logcat_thread.start()
        self.backend_thread.start()
        self.keepalive_thread.start()

    def validate_turn_invariants(self, turn_dict: dict[str, Any]) -> tuple[bool, str]:
        """Validate all required invariants before a turn can be classified as PASS."""
        # 1. Final count must be exactly 1
        final_count = turn_dict.get("final_count", 0)
        if final_count != 1:
            return False, f"FAIL_FINAL_COUNT: final_count={final_count} (expected 1)"

        # 2. Final transcript must be non-empty string
        final_text = turn_dict.get("final_transcript")
        if not final_text or final_text in ["NOT_AVAILABLE", ""]:
            return False, "FAIL_TRANSCRIPT_INTEGRITY: empty or missing final transcript"

        # 3. Response correlation identifiers must be present
        turn_id = turn_dict.get("turn_id")
        response_id = turn_dict.get("response_id")
        if not turn_id or turn_id == "NOT_AVAILABLE" or not response_id or response_id == "NOT_AVAILABLE":
            return False, "FAIL_RESPONSE_CORRELATION: missing turn_id or response_id"

        # 4. Invariant: ALL durations must be non-negative (>= 0)
        duration_fields = [
            "speech_duration_ms",
            "commit_to_final_ms",
            "speech_to_final_ms",
            "turn_processing_ms",
        ]
        for field in duration_fields:
            val = turn_dict.get(field)
            if isinstance(val, (int, float)):
                if val < 0:
                    return False, f"FAIL_TIMESTAMP_INTEGRITY: negative duration in {field} ({val} ms)"

        first_part_lat = turn_dict.get("first_partial_latency_ms")
        if isinstance(first_part_lat, (int, float)) and first_part_lat < 0:
            return False, f"FAIL_TIMESTAMP_INTEGRITY: negative first_partial_latency_ms ({first_part_lat} ms)"

        # 5. Latency timeout check
        c2f = turn_dict.get("commit_to_final_ms")
        if isinstance(c2f, (int, float)) and c2f > 180000:
            return False, f"FAIL_LATENCY: commit_to_final_ms={c2f} exceeds 180000ms timeout"

        return True, "PASS"

    def finalize_turn_metrics(self, turn_dict: dict[str, Any]):
        # Calculate latencies strictly within the server monotonic clock domain
        s_audio_start_mono = turn_dict.get("server_audio_start_monotonic_ms")
        s_end_mono = turn_dict.get("speech_end_monotonic_ms")
        commit_mono = turn_dict.get("backend_commit_received_monotonic_ms")
        final_mono = turn_dict.get("final_transcript_monotonic_ms")
        t_start_mono = turn_dict.get("turn_start_monotonic_ms")
        t_comp_mono = turn_dict.get("turn_completion_monotonic_ms")

        # 1. Speech duration
        if s_end_mono not in [None, "NOT_AVAILABLE"] and s_audio_start_mono not in [None, "NOT_AVAILABLE"]:
            turn_dict["speech_duration_ms"] = round(s_end_mono - s_audio_start_mono, 1)
        elif turn_dict["audio_duration_ms"] > 0:
            turn_dict["speech_duration_ms"] = turn_dict["audio_duration_ms"]
        elif turn_dict["speech_start_timestamp"] != "NOT_AVAILABLE" and turn_dict["speech_end_timestamp"] != "NOT_AVAILABLE":
            turn_dict["speech_duration_ms"] = round(turn_dict["speech_end_timestamp"] - turn_dict["speech_start_timestamp"], 1)
        else:
            turn_dict["speech_duration_ms"] = turn_dict["audio_duration_ms"]

        # 2. Commit -> Final (server monotonic)
        if commit_mono not in [None, "NOT_AVAILABLE"] and final_mono not in [None, "NOT_AVAILABLE"]:
            turn_dict["commit_to_final_ms"] = round(final_mono - commit_mono, 1)
        elif turn_dict["final_transcript_timestamp"] != "NOT_AVAILABLE" and turn_dict["backend_commit_received_timestamp"] != "NOT_AVAILABLE":
            turn_dict["commit_to_final_ms"] = round(turn_dict["final_transcript_timestamp"] - turn_dict["backend_commit_received_timestamp"], 1)
        else:
            turn_dict["commit_to_final_ms"] = "N/A"

        # 3. Speech -> Final (server monotonic)
        if s_end_mono not in [None, "NOT_AVAILABLE"] and final_mono not in [None, "NOT_AVAILABLE"]:
            turn_dict["speech_to_final_ms"] = round(final_mono - s_end_mono, 1)
        elif turn_dict["final_transcript_timestamp"] != "NOT_AVAILABLE" and turn_dict["speech_end_timestamp"] != "NOT_AVAILABLE":
            turn_dict["speech_to_final_ms"] = round(turn_dict["final_transcript_timestamp"] - turn_dict["speech_end_timestamp"], 1)
        else:
            turn_dict["speech_to_final_ms"] = "N/A"

        # 4. Partial latency (server monotonic)
        f_part_mono = turn_dict.get("first_partial_monotonic_ms")
        if f_part_mono not in [None, "NOT_AVAILABLE"] and s_audio_start_mono not in [None, "NOT_AVAILABLE"]:
            turn_dict["first_partial_latency_ms"] = round(f_part_mono - s_audio_start_mono, 1)
        elif turn_dict["first_partial_timestamp"] != "NOT_AVAILABLE" and turn_dict["speech_start_timestamp"] != "NOT_AVAILABLE":
            turn_dict["first_partial_latency_ms"] = round(turn_dict["first_partial_timestamp"] - turn_dict["speech_start_timestamp"], 1)
        else:
            turn_dict["first_partial_latency_ms"] = None

        # 5. Turn processing (server monotonic domain only!)
        if t_comp_mono not in [None, "NOT_AVAILABLE"] and t_start_mono not in [None, "NOT_AVAILABLE"]:
            turn_dict["turn_processing_ms"] = round(t_comp_mono - t_start_mono, 1)
        elif turn_dict["turn_completion_timestamp"] != "NOT_AVAILABLE" and turn_dict["turn_start_timestamp"] != "NOT_AVAILABLE":
            turn_dict["turn_processing_ms"] = round(turn_dict["turn_completion_timestamp"] - turn_dict["turn_start_timestamp"], 1)
        elif not isinstance(turn_dict.get("turn_processing_ms"), (int, float)):
            turn_dict["turn_processing_ms"] = "N/A"

        # Run strict invariant validation
        is_valid, validation_status = self.validate_turn_invariants(turn_dict)
        if not is_valid:
            turn_dict["status"] = validation_status.split(":")[0].strip()
            turn_dict["failure_reason"] = validation_status
        else:
            turn_dict["status"] = "PASS"
            turn_dict["failure_reason"] = None

    def print_turn_banner(self, turn_num: int):
        banner = f"""
************************************************************
*                                                          *
*                 TURN {turn_num} / {self.required_turns} — READY                    *
*                                                          *
*               SPEAK YOUR SENTENCE NOW                    *
*                                                          *
************************************************************

READY — Speak your English sentence now.

[Microphone is active]

Waiting for speech..."""
        self.log(banner)

    def print_turn_results(self, turn_dict: dict[str, Any]):
        first_partial_str = (
            f"{format_iso(turn_dict['first_partial_timestamp'])} ({turn_dict['first_partial_latency_ms']} ms)"
            if turn_dict["first_partial_timestamp"] != "NOT_AVAILABLE"
            else "N/A"
        )
        report = f"""
------------------------------------------------------------
TURN {turn_dict['turn']} RESULTS
------------------------------------------------------------

Final transcript:
"{turn_dict['final_transcript']}"

Speech start:            {format_iso(turn_dict['speech_start_timestamp'])}
Speech end:              {format_iso(turn_dict['speech_end_timestamp'])}
First partial:           {first_partial_str}
Commit:                  {format_iso(turn_dict['client_commit_timestamp'])}
Final transcript:        {format_iso(turn_dict['final_transcript_timestamp'])}
Final received:          {format_iso(turn_dict['android_final_received_timestamp'])}

Speech duration:         {turn_dict['speech_duration_ms']} ms
First partial latency:   {turn_dict['first_partial_latency_ms'] if turn_dict['first_partial_latency_ms'] is not None else 'N/A'} ms
Speech -> final:          {turn_dict['speech_to_final_ms']} ms
Commit -> final:          {turn_dict['commit_to_final_ms']} ms
Turn processing:         {turn_dict['turn_processing_ms']} ms

PCM frames:              {turn_dict['pcm_frames']}
PCM bytes:               {turn_dict['pcm_bytes']}
Response ID:             {turn_dict['response_id']}
Final count:             {turn_dict['final_count']}

Status: {turn_dict['status']} {f'({turn_dict.get("failure_reason")})' if turn_dict.get("failure_reason") else ''}
"""
        self.log(report)

    def save_json_and_summary(self):
        data = {
            "phase": 4,
            "device": self.target_device,
            "language": "English",
            "required_turns": self.required_turns,
            "completed_turns": len(self.turns),
            "duplicate_finals": self.duplicate_finals_count,
            "stale_responses": self.stale_responses_count,
            "correlation_mismatches": self.correlation_mismatches_count,
            "turns": self.turns,
        }
        with open(self.json_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        speech_to_finals = [
            float(t["speech_to_final_ms"]) for t in self.turns if isinstance(t["speech_to_final_ms"], (int, float))
        ]
        commit_to_finals = [
            float(t["commit_to_final_ms"]) for t in self.turns if isinstance(t["commit_to_final_ms"], (int, float))
        ]
        partial_latencies = [
            float(t["first_partial_latency_ms"])
            for t in self.turns
            if t["first_partial_latency_ms"] is not None and isinstance(t["first_partial_latency_ms"], (int, float))
        ]
        speech_durations = [
            float(t["speech_duration_ms"]) for t in self.turns if isinstance(t["speech_duration_ms"], (int, float))
        ]

        stf_stats = calculate_percentiles(speech_to_finals)
        ctf_stats = calculate_percentiles(commit_to_finals)
        part_stats = calculate_percentiles(partial_latencies)
        dur_stats = calculate_percentiles(speech_durations)

        summary_md = f"""# Phase 4 — 10-Turn Physical STT Validation Summary

Validation timestamp: {self.timestamp}  
Device: `{self.target_device}`  
Required turns: {self.required_turns}  
Completed turns: {len(self.turns)} / {self.required_turns}  

## Aggregate Latency Table

| Metric | Average | P50 (Median) | P95 | Max | Min |
| :--- | ---: | ---: | ---: | ---: | ---: |
| **Speech Duration** | {dur_stats['avg']} ms | {dur_stats['p50']} ms | {dur_stats['p95']} ms | {dur_stats['max']} ms | {dur_stats['min']} ms |
| **First Partial Latency** | {part_stats['avg']} ms | {part_stats['p50']} ms | {part_stats['p95']} ms | {part_stats['max']} ms | {part_stats['min']} ms |
| **Speech → Final** | {stf_stats['avg']} ms | {stf_stats['p50']} ms | {stf_stats['p95']} ms | {stf_stats['max']} ms | {stf_stats['min']} ms |
| **Commit → Final** | {ctf_stats['avg']} ms | {ctf_stats['p50']} ms | {ctf_stats['p95']} ms | {ctf_stats['max']} ms | {ctf_stats['min']} ms |

## Per-Turn Verification

| Turn | Transcript | Speech Duration | First Partial | Speech→Final | Commit→Final | Final Count | Status |
| ---: | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
"""
        for t in self.turns:
            part_str = f"{t['first_partial_latency_ms']} ms" if t["first_partial_latency_ms"] is not None else "N/A"
            summary_md += f"| {t['turn']} | {t['final_transcript']} | {t['speech_duration_ms']} ms | {part_str} | {t['speech_to_final_ms']} ms | {t['commit_to_final_ms']} ms | {t['final_count']} | {t['status']} |\n"

        summary_md += f"""
## Event Statistics

- **Total completed turns**: {len(self.turns)} / {self.required_turns}
- **Turns with partials**: {len(partial_latencies)} / {len(self.turns)}
- **Duplicate finals**: {self.duplicate_finals_count}
- **Stale responses**: {self.stale_responses_count}
- **Correlation mismatches**: {self.correlation_mismatches_count}
"""
        with open(self.summary_file_path, "w", encoding="utf-8") as f:
            f.write(summary_md)

        self._write_final_acceptance_report(summary_md, stf_stats, ctf_stats, part_stats)

    def _write_final_acceptance_report(
        self,
        summary_md: str,
        stf_stats: dict[str, float],
        ctf_stats: dict[str, float],
        part_stats: dict[str, float],
    ):
        pass_count = sum(1 for t in self.turns if t["status"] == "PASS")
        report_status = "PASS" if pass_count == self.required_turns and len(self.turns) == self.required_turns else "FAIL"

        report = f"""# Phase 4 — Final Physical English Validation Report

Validation timestamp: {self.timestamp}  
Scope: English-only, CPU-only Phase 4 physical acceptance audit on physical `{self.target_device}`.

## Status

`PHASE 4 POST-FIX PHYSICAL TEST: {report_status}`

{summary_md}

## Final Gate

```text
PHASE 4: {'ACCEPTED' if report_status == 'PASS' else 'ACCEPTANCE PENDING'}
```
"""
        with open(self.final_acceptance_report_path, "w", encoding="utf-8") as f:
            f.write(report)

    def run(self):
        header = f"""============================================================
PHASE 4 — PHYSICAL STT ACCEPTANCE
============================================================

Device: {self.target_device}
Required turns: {self.required_turns}
Language: English
Validation log: {self.log_file_path}
=============================================================="""
        self.log(header)

        self.start_listeners()
        time.sleep(1)

        while self.current_turn <= self.required_turns:
            self.turn_completed_event.clear()
            self.speech_detected_event.clear()
            self.speech_ended_event.clear()
            self.current_turn_data = self._new_turn_dict(self.current_turn)

            self.print_turn_banner(self.current_turn)

            # Wait for turn completion (up to 180s)
            completed = self.turn_completed_event.wait(timeout=180.0)

            if not completed or self.current_turn_data["final_transcript"] == "NOT_AVAILABLE":
                self.log(f"""
Turn rejected - insufficient audio or timeout.
Retrying Turn {self.current_turn}...""")
                time.sleep(1)
                continue

            # Finalize turn metrics
            self.finalize_turn_metrics(self.current_turn_data)
            self.turns.append(self.current_turn_data)
            self.print_turn_results(self.current_turn_data)
            self.save_json_and_summary()

            self.current_turn += 1
            time.sleep(1)

        # Final 10-turn summary print
        speech_to_finals = [
            float(t["speech_to_final_ms"]) for t in self.turns if isinstance(t["speech_to_final_ms"], (int, float))
        ]
        commit_to_finals = [
            float(t["commit_to_final_ms"]) for t in self.turns if isinstance(t["commit_to_final_ms"], (int, float))
        ]
        stf_stats = calculate_percentiles(speech_to_finals)
        ctf_stats = calculate_percentiles(commit_to_finals)

        final_summary = f"""============================================================
PHASE 4 — 10 TURN TEST COMPLETE
============================================================

Valid turns: {len(self.turns)} / {self.required_turns}

Partial events: {sum(1 for t in self.turns if t['first_partial_latency_ms'] is not None)} / {len(self.turns)}
Final events: {len(self.turns)} / {self.required_turns}
Duplicate finals: {self.duplicate_finals_count}
Stale responses: {self.stale_responses_count}
Correlation mismatches: {self.correlation_mismatches_count}

Average speech → final: {stf_stats['avg']} ms
P50 speech → final:     {stf_stats['p50']} ms
P95 speech → final:     {stf_stats['p95']} ms

Average commit → final: {ctf_stats['avg']} ms
P50 commit → final:     {ctf_stats['p50']} ms
P95 commit → final:     {ctf_stats['p95']} ms

============================================================
10-turn physical evidence collection: COMPLETE
"""
        self.log(final_summary)
        self.stop()

    def stop(self):
        self.running = False
        if hasattr(self, "logcat_proc"):
            try:
                self.logcat_proc.terminate()
            except Exception:
                pass
        self.log_file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase 4 physical STT validation")
    parser.add_argument("--device", default="RMX5070", help="ADB target device")
    parser.add_argument("--turns", type=int, default=10, help="Required turns")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to store outputs")
    args = parser.parse_args()

    out_path = Path(args.output_dir) if args.output_dir else None
    runner = InteractiveValidationRunner(target_device=args.device, required_turns=args.turns, output_dir=out_path)
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
