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
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
DOCS_DIR = WORKSPACE_ROOT / "docs"
VALIDATION_ROOT = DOCS_DIR / "phase4_physical_validation"
SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)

BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.stt.evaluation import normalize_transcript

from scripts.phase4_acceptance import (
    PHASE4_REFERENCE_SENTENCES,
    calculate_turn_wer,
    evaluate_acceptance_gates,
    is_number,
    latency_statistics,
    validate_turn_evidence,
)


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
                    with open(log_file, encoding="utf-8", errors="ignore") as f:
                        header = f.read(500)
                        if (
                            "uvicorn" in header
                            or "voice-assistance" in header
                            or "Started server process" in header
                        ):
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
        dt = datetime.datetime.fromtimestamp(sec, tz=datetime.UTC)
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
        backend_log_path: Path | None = None,
        automated_validation_manifest_path: Path | None = None,
    ):
        if required_turns != len(PHASE4_REFERENCE_SENTENCES):
            raise ValueError("Phase 4 physical acceptance requires exactly 10 turns")
        self.target_device = target_device
        self.required_turns = required_turns
        self.source_backend_log_path = backend_log_path
        self.automated_validation_manifest_path = automated_validation_manifest_path
        self.timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        self.output_dir = output_dir or (VALIDATION_ROOT / self.timestamp)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.log_file_path = self.output_dir / "phase4_10turn_validation.log"
        self.json_file_path = self.output_dir / "phase4_10turn_results.json"
        self.summary_file_path = self.output_dir / "phase4_10turn_summary.md"
        self.final_acceptance_report_path = DOCS_DIR / (
            f"{self.timestamp}_phase_4_remote_stt_final_acceptance.md"
        )
        self.references_path = self.output_dir / "references.json"
        self.turns_path = self.output_dir / "turns.jsonl"
        self.partials_path = self.output_dir / "partials.jsonl"
        self.events_path = self.output_dir / "events.jsonl"
        self.latency_path = self.output_dir / "latency.json"
        self.wer_path = self.output_dir / "wer.json"
        self.resources_path = self.output_dir / "resources.jsonl"
        self.resource_summary_path = self.output_dir / "resource_summary.json"
        self.reconnect_path = self.output_dir / "reconnect.json"
        self.backend_log_path = self.output_dir / "backend.log"
        self.worker_log_path = self.output_dir / "stt_worker.log"
        self.android_log_path = self.output_dir / "android_or_adb.log"
        self.validation_summary_path = self.output_dir / "validation_summary.json"
        self.validation_status_path = self.output_dir / "validation_status.txt"

        self.test_start_utc = datetime.datetime.now(datetime.UTC).isoformat()
        self.test_start_monotonic_ms = round(time.monotonic() * 1000, 1)
        self.test_end_utc: str | None = None
        self.test_end_monotonic_ms: float | None = None

        self.log_file = open(self.log_file_path, "w", encoding="utf-8", buffering=1)
        self.backend_log = open(self.backend_log_path, "w", encoding="utf-8", buffering=1)
        self.worker_log = open(self.worker_log_path, "w", encoding="utf-8", buffering=1)
        self.android_log = open(self.android_log_path, "w", encoding="utf-8", buffering=1)
        self.resources_log = open(self.resources_path, "w", encoding="utf-8", buffering=1)
        self.events_path.touch()
        self._write_reference_bundle()

        self.current_turn = 1
        self.turns: list[dict[str, Any]] = []
        self.current_turn_data: dict[str, Any] = self._new_turn_dict(1)
        self.events: list[dict[str, Any]] = []
        self.backend_resource_samples: list[dict[str, Any]] = []
        self.worker_resource_samples: list[dict[str, Any]] = []
        self.remote_request_samples: list[dict[str, Any]] = []
        self.resource_thread: threading.Thread | None = None
        self.resource_stop_event = threading.Event()
        self.backend_pid: int | None = None
        self.worker_pid: int | None = None
        self.engine_info: dict[str, Any] = {}
        self.reconnect_evidence: dict[str, Any] = {
            "disconnect_observed": False,
            "reconnect_observed": False,
            "audio_accepted_after_reconnect": False,
            "stt_continued_after_reconnect": False,
            "events": [],
            "error": None,
        }
        self.reconnect_test_active = False
        self.adb_serial = target_device
        self.device_verified = False
        self.apk_install_launch = False
        self.websocket_physical_path = False
        self.windows_worker_deployment = False
        self.remote_stt_deployment = False
        self.automated_validation_passed = self._read_automated_validation_manifest()
        self._resource_previous: dict[int, tuple[float, float]] = {}

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

    def _write_reference_bundle(self) -> None:
        references = [
            {
                "turn_number": index,
                "reference_raw": text,
                "reference_normalized": normalize_transcript(text),
            }
            for index, text in enumerate(PHASE4_REFERENCE_SENTENCES, start=1)
        ]
        with open(self.references_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "phase": 4,
                    "stored_before_recognition": True,
                    "stored_utc": self.test_start_utc,
                    "references": references,
                },
                handle,
                indent=2,
            )

    def record_event(
        self,
        event: str,
        *,
        turn: int | None = None,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
        monotonic_ms: float | None = None,
    ) -> None:
        record = {
            "event": event,
            "turn": turn,
            "session_id": session_id,
            "monotonic_ns": round((monotonic_ms or time.monotonic() * 1000) * 1_000_000),
            "utc_timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "details": details or {},
        }
        self.events.append(record)
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    @staticmethod
    def _redact_log_line(line: str) -> str:
        line = re.sub(r"(?i)(authorization|access_token|refresh_token|password|secret|token)\s*[:=]\s*[^,}\s]+", r"\1=[REDACTED]", line)
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_JWT]", line)

    def _calculate_adb_offset(self) -> int:
        try:
            device_time = int(
                subprocess.check_output(
                    self._adb_command("shell", "date", "+%s%3N"),
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
            self.log(
                f"Could not calculate exact clock offset via adb: {e}. Defaulting to 0."
            )
            return 0

    def _adb_command(self, *arguments: str, include_serial: bool = True) -> list[str]:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WORKSPACE_ROOT / "scripts" / "adb_phase4.ps1"),
        ]
        if include_serial:
            command.extend(["-s", self.adb_serial])
        command.extend(arguments)
        return command

    def verify_device(self) -> bool:
        try:
            devices = subprocess.run(
                self._adb_command("devices", "-l", include_serial=False),
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.android_log.write(self._redact_log_line(devices.stdout))
            self.android_log.write(self._redact_log_line(devices.stderr))
            self.android_log.flush()
            for line in devices.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[1] == "device":
                    serial = fields[0]
                    if serial == self.target_device or "RMX5070" in serial:
                        self.adb_serial = serial
                        self.device_verified = True
                        self.record_event(
                            "android.device.verified",
                            details={"serial": serial, "raw": line},
                        )
                        self.apk_install_launch = self._install_and_launch_apk()
                        return self.apk_install_launch
            self.log("No authorized RMX5070/RMX5070IN device is visible to ADB")
        except (OSError, subprocess.SubprocessError) as error:
            self.log(f"ADB device verification failed: {error}")
        return False

    def _read_automated_validation_manifest(self) -> bool:
        if self.automated_validation_manifest_path is None:
            return False
        try:
            with open(self.automated_validation_manifest_path, encoding="utf-8") as handle:
                return json.load(handle).get("pass") is True
        except (OSError, ValueError, TypeError):
            return False

    def _install_and_launch_apk(self) -> bool:
        apk_path = WORKSPACE_ROOT / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        if not apk_path.is_file():
            self.log(f"APK missing: {apk_path}")
            return False
        try:
            install = subprocess.run(
                self._adb_command("install", "-r", str(apk_path)),
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            )
            launch = subprocess.run(
                self._adb_command("shell", "monkey", "-p", "com.voiceaipoc", "1"),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            self.android_log.write(self._redact_log_line(install.stdout))
            self.android_log.write(self._redact_log_line(launch.stdout))
            self.android_log.flush()
            self.record_event(
                "android.apk.install_launch",
                details={"apk": str(apk_path), "package": "com.voiceaipoc"},
            )
            return True
        except (OSError, subprocess.SubprocessError) as error:
            self.log(f"APK install/launch failed: {error}")
            return False

    def log(self, message: str, print_stdout: bool = True):
        t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{t}] {message}"
        if print_stdout:
            print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def _new_turn_dict(self, turn_num: int) -> dict[str, Any]:
        reference_sentence = (
            PHASE4_REFERENCE_SENTENCES[turn_num - 1]
            if 1 <= turn_num <= len(PHASE4_REFERENCE_SENTENCES)
            else "NOT_DEFINED"
        )
        return {
            "turn": turn_num,
            "turn_number": turn_num,
            "reference_sentence": reference_sentence,
            "reference_text": reference_sentence,
            "reference_raw": reference_sentence,
            "reference_normalized": normalize_transcript(reference_sentence),
            "session_id": "NOT_AVAILABLE",
            "websocket_session_id": "NOT_AVAILABLE",
            "turn_id": "NOT_AVAILABLE",
            "response_id": "NOT_AVAILABLE",
            "device_id": self.target_device,
            "turn_start_timestamp": "NOT_AVAILABLE",
            "turn_start_monotonic_ms": "NOT_AVAILABLE",
            "speech_start_timestamp": "NOT_AVAILABLE",
            "speech_start_monotonic_ms": "NOT_AVAILABLE",
            "server_audio_start_monotonic_ms": "NOT_AVAILABLE",
            "speech_end_timestamp": "NOT_AVAILABLE",
            "speech_end_monotonic_ms": "NOT_AVAILABLE",
            "vad_end_timestamp": "NOT_AVAILABLE",
            "vad_end_monotonic_ms": "NOT_AVAILABLE",
            "first_pcm_timestamp": "NOT_AVAILABLE",
            "first_pcm_monotonic_ms": "NOT_AVAILABLE",
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
            "turn_end_timestamp": "NOT_AVAILABLE",
            "turn_end_monotonic_ms": "NOT_AVAILABLE",
            "android_final_received_timestamp": "NOT_AVAILABLE",
            "android_final_received_monotonic_ms": "NOT_AVAILABLE",
            "turn_completion_timestamp": "NOT_AVAILABLE",
            "turn_completion_monotonic_ms": "NOT_AVAILABLE",
            "cancellation_requested": "NOT_AVAILABLE",
            "cancellation_completed": "NOT_AVAILABLE",
            "partials": [],
            "partial_transcripts": [],
            "partial_count": 0,
            "no_partial_reason": None,
            "final_transcript": "NOT_AVAILABLE",
            "hypothesis_raw": "NOT_AVAILABLE",
            "hypothesis_normalized": "NOT_AVAILABLE",
            "recognition_confidence": None,
            "wer_normalization": "NFKC, casefold, punctuation-to-space, whitespace collapse",
            "wer_substitutions": None,
            "wer_deletions": None,
            "wer_insertions": None,
            "wer_reference_words": None,
            "wer": None,
            "per_turn_wer": None,
            "final_count": 0,
            "pcm_frames": 0,
            "pcm_bytes": 0,
            "audio_bytes": 0,
            "audio_duration_ms": 0,
            "speech_duration_ms": "N/A",
            "first_partial_latency_ms": None,
            "speech_to_final_ms": "N/A",
            "speech_end_to_final_ms": "N/A",
            "first_audio_to_first_partial_ms": None,
            "commit_to_final_ms": "N/A",
            "turn_processing_ms": "N/A",
            "status": "PENDING",  # PASS, FAIL_LATENCY, FAIL_TIMESTAMP_INTEGRITY, FAIL_PARTIAL, FAIL_FINAL_COUNT, etc.
            "failure_reason": None,
            "error": None,
            "stt_engine": "NOT_AVAILABLE",
            "recognizer_id": "NOT_AVAILABLE",
            "stt_provider": "NOT_AVAILABLE",
            "runtime": "NOT_AVAILABLE",
            "language": "en-US",
        }

    def parse_logcat_line(self, line: str):
        self.android_log.write(self._redact_log_line(line) + "\n")
        self.android_log.flush()
        if "VoiceAI-Bridge" in line:
            # VAD speech started
            m = re.search(r"(?:SILERO )?VAD speech started.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if (
                        self.current_turn_data["speech_start_timestamp"]
                        == "NOT_AVAILABLE"
                    ):
                        self.current_turn_data["speech_start_timestamp"] = wall_ms
                        self.log(f"Speech detected (wallMs={wall_ms})")
                        self.speech_detected_event.set()

            # VAD speech stopped
            m = re.search(r"(?:SILERO )?VAD speech stopped.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if (
                        self.current_turn_data["speech_end_timestamp"]
                        == "NOT_AVAILABLE"
                    ):
                        self.current_turn_data["speech_end_timestamp"] = wall_ms
                        self.current_turn_data["vad_end_timestamp"] = wall_ms
                        self.log(f"[VAD END] Speech stopped (wallMs={wall_ms})")
                        self.speech_ended_event.set()

        elif "VoiceAI-VoiceGateway" in line:
            # Client control sent
            m = re.search(
                r"VOICE control sent type=(\S+).*wallMs=(\d+)(?:\s+elapsedMs=(\d+))?",
                line,
            )
            if m:
                ctl_type = m.group(1)
                wall_ms = int(m.group(2)) + self.device_offset_ms
                elapsed_ms = int(m.group(3)) if m.group(3) else None
                if ctl_type == "client.turn.start":
                    with self.lock:
                        if (
                            self.current_turn_data["turn_start_timestamp"]
                            == "NOT_AVAILABLE"
                        ):
                            self.current_turn_data["turn_start_timestamp"] = wall_ms
                            if elapsed_ms:
                                self.current_turn_data["turn_start_monotonic_ms"] = (
                                    elapsed_ms
                                )
                            self.log(f"Client turn start sent (wallMs={wall_ms})")
                elif ctl_type == "client.audio.commit":
                    with self.lock:
                        self.current_turn_data["client_commit_timestamp"] = wall_ms
                        if elapsed_ms:
                            self.current_turn_data["client_commit_monotonic_ms"] = (
                                elapsed_ms
                            )
                        self.log(f"Client audio commit sent (wallMs={wall_ms})")
                        if (
                            self.current_turn_data["speech_end_timestamp"]
                            == "NOT_AVAILABLE"
                        ):
                            self.current_turn_data["speech_end_timestamp"] = wall_ms

            # Server event received on Android with response correlation
            m = re.search(
                r"VOICE server event type=(\S+)\s+sessionId=(\S+)\s+turnId=(\S+)\s+responseId=(\S+)\s+payload=(.*?)\s+wallMs=(\d+)(?:\s+elapsedMs=(\d+))?",
                line,
            )
            if m:
                evt_type = m.group(1)
                session_id = m.group(2)
                turn_id = m.group(3)
                wall_ms = int(m.group(6)) + self.device_offset_ms
                elapsed_ms = int(m.group(7)) if m.group(7) else None

                with self.lock:
                    self.record_event(
                        "android.websocket.event",
                        turn=self.current_turn_data.get("turn_number"),
                        session_id=session_id,
                        details={"type": evt_type, "turn_id": turn_id},
                    )
                    # Check correlation
                    cur_turn_id = self.current_turn_data["turn_id"]
                    if (
                        cur_turn_id != "NOT_AVAILABLE"
                        and turn_id != "NONE"
                        and turn_id != cur_turn_id
                    ):
                        self.correlation_mismatches_count += 1
                        self.log(
                            f"WARNING: Logcat event for turn {turn_id} does not match active turn {cur_turn_id}"
                        )
                        return

                    if (
                        evt_type == "transcript.final"
                        or evt_type == "server.turn.completed"
                    ):
                        self.current_turn_data["android_final_received_timestamp"] = (
                            wall_ms
                        )
                        if elapsed_ms:
                            self.current_turn_data[
                                "android_final_received_monotonic_ms"
                            ] = elapsed_ms
                        self.log(f"Android received {evt_type} (wallMs={wall_ms})")
                        self._check_turn_completed()

        elif "VoiceAI-Audio" in line:
            if "Microphone capture started" in line:
                self.log(
                    f"Microphone capture active: {line.strip()}", print_stdout=False
                )

    def parse_backend_line(self, line: str):
        redacted_line = self._redact_log_line(line)
        self.backend_log.write(redacted_line + "\n")
        self.backend_log.flush()
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        event = data.get("event")
        if not event:
            return

        with self.lock:
            if event == "STT_WORKER_DIAGNOSTIC":
                self.worker_log.write(redacted_line + "\n")
                self.worker_log.flush()
            elif event == "STT_ENGINE_STARTED":
                provider = data.get("endpoint_host") or data.get("recognizer_name")
                self.engine_info.update(
                    {
                        "engine": data.get("engine"),
                        "runtime": data.get("runtime"),
                        "stt_provider": provider,
                        "recognizer_id": data.get("recognizer_name") or "NOT_APPLICABLE",
                        "language": data.get("language") or data.get("recognizer_language"),
                    }
                )
                if data.get("engine") == "remote":
                    self.remote_stt_deployment = True
                self.record_event("stt.engine.started", details=self.engine_info.copy())
            elif event == "STT_WORKER_READY":
                self.windows_worker_deployment = True
                self.engine_info.update(
                    {
                        "engine": data.get("engine", self.engine_info.get("engine")),
                        "runtime": data.get("runtime", self.engine_info.get("runtime")),
                        "stt_provider": data.get(
                            "endpoint_host", self.engine_info.get("stt_provider")
                        ),
                        "recognizer_id": data.get(
                            "recognizer_name", self.engine_info.get("recognizer_id")
                        ),
                        "language": data.get("language", self.engine_info.get("language")),
                    }
                )
                self.record_event("stt.worker.ready", details=self.engine_info.copy())
            elif event == "STT_WORKER_PROCESS_STARTED":
                self.worker_pid = data.get("worker_pid")
                self.record_event(
                    "stt.worker.started",
                    details={"pid": self.worker_pid, "path": data.get("path")},
                )
            elif event == "STT_REMOTE_FINAL":
                sample = {
                    "turn_id": data.get("turn_id"),
                    "status_code": data.get("status_code"),
                    "request_id": data.get("request_id"),
                    "audio_bytes": data.get("audio_bytes"),
                    "request_duration_ms": data.get("request_duration_ms"),
                    "utc_timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                }
                self.remote_request_samples.append(sample)
                self.record_event("stt.remote.final", details=sample)
            elif event == "voice.connection.opened":
                self.backend_pid = data.get("backend_pid") or self.backend_pid
                self.record_event(
                    "websocket.connected",
                    session_id=data.get("session_id"),
                    details={"reconnect": data.get("reconnect", False)},
                )
                if self.reconnect_test_active:
                    self.reconnect_evidence["reconnect_observed"] = True
                    self.reconnect_evidence["reconnect_timestamp"] = data.get(
                        "monotonic_ms"
                    )
            elif event in {"voice.connection.closed", "voice.session.ended"}:
                self.record_event(
                    "websocket.disconnected",
                    session_id=data.get("session_id"),
                    details={
                        "reason": data.get("reason") or data.get("close_reason"),
                        "close_code": data.get("close_code"),
                    },
                )
                if self.reconnect_test_active:
                    self.reconnect_evidence["disconnect_observed"] = True
                    self.reconnect_evidence["disconnect_timestamp"] = data.get(
                        "monotonic_ms"
                    )
            elif event == "voice.session.started":
                self.websocket_physical_path = True
                self.record_event(
                    "websocket.session.started",
                    session_id=data.get("session_id"),
                    details={"reconnect": data.get("reconnect", False)},
                    monotonic_ms=data.get("monotonic_ms"),
                )
                if self.reconnect_test_active:
                    self.reconnect_evidence["reconnect_observed"] = True
                    self.reconnect_evidence["reconnect_session_id"] = data.get("session_id")
            elif event == "voice.turn.started":
                turn_id = data.get("turn_id")
                response_id = data.get("response_id")
                session_id = data.get("session_id")
                ts = data.get("timestamp_ms")
                mono = data.get("monotonic_ms")

                if turn_id in self.seen_turn_ids:
                    self.stale_responses_count += 1
                    self.log(f"WARNING: Stale turn_id {turn_id} detected!")

                self.seen_turn_ids.add(turn_id)
                self.seen_response_ids.add(response_id)

                self.current_turn_data["session_id"] = session_id
                self.current_turn_data["websocket_session_id"] = session_id
                self.current_turn_data["turn_id"] = turn_id
                self.current_turn_data["response_id"] = response_id
                if data.get("turn_number") is not None:
                    self.current_turn_data["turn_number"] = data["turn_number"]
                if self.current_turn_data["turn_start_timestamp"] == "NOT_AVAILABLE":
                    if ts is not None:
                        self.current_turn_data["turn_start_timestamp"] = ts
                    if mono is not None:
                        self.current_turn_data["turn_start_monotonic_ms"] = mono
                self.current_turn_data["stt_engine"] = self.engine_info.get(
                    "engine", "NOT_AVAILABLE"
                )
                self.current_turn_data["stt_provider"] = self.engine_info.get(
                    "stt_provider", "NOT_AVAILABLE"
                )
                self.current_turn_data["recognizer_id"] = self.engine_info.get(
                    "recognizer_id", "NOT_AVAILABLE"
                )
                self.current_turn_data["runtime"] = self.engine_info.get(
                    "runtime", "NOT_AVAILABLE"
                )
                self.current_turn_data["language"] = self.engine_info.get(
                    "language", self.current_turn_data["language"]
                )
                self.record_event(
                    "turn.started",
                    turn=self.current_turn_data["turn_number"],
                    session_id=session_id,
                    details={"turn_id": turn_id, "response_id": response_id},
                    monotonic_ms=mono,
                )
                self.log(
                    f"Backend started turn: turn_id={turn_id}, response_id={response_id}"
                )

            elif event == "voice.pcm.accepted":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    frames = data.get("frames_accepted") or 0
                    bytes_cnt = data.get("bytes_received") or 0
                    if frames > self.current_turn_data["pcm_frames"]:
                        self.current_turn_data["pcm_frames"] = frames
                        self.current_turn_data["pcm_bytes"] = bytes_cnt
                    self.current_turn_data["audio_bytes"] = max(
                        self.current_turn_data["audio_bytes"], bytes_cnt
                    )
                    if self.reconnect_test_active:
                        self.reconnect_evidence["audio_accepted_after_reconnect"] = True
                        self.reconnect_evidence["audio_bytes_after_reconnect"] = bytes_cnt

            elif event == "voice.audio.commit.received":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("backend_commit_received_timestamp_ms")
                    mono = data.get("backend_commit_received_monotonic_ms")
                    self.current_turn_data["backend_commit_received_timestamp"] = ts
                    self.current_turn_data["backend_commit_received_monotonic_ms"] = (
                        mono
                    )
                    frame_cnt = data.get("frame_count") or 0
                    byte_cnt = data.get("byte_count") or 0
                    if frame_cnt:
                        self.current_turn_data["pcm_frames"] = frame_cnt
                        self.current_turn_data["pcm_bytes"] = byte_cnt
                        self.current_turn_data["audio_bytes"] = byte_cnt
                    self.log(
                        f"Backend received audio commit: {frame_cnt} frames, {byte_cnt} bytes"
                    )

            elif event in {"stt.audio.started", "STT_AUDIO_RECEIVED"}:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("audio_start_timestamp_ms")
                    mono = data.get("audio_start_monotonic_ms")
                    if (
                        self.current_turn_data["speech_start_timestamp"]
                        == "NOT_AVAILABLE"
                    ):
                        self.current_turn_data["speech_start_timestamp"] = ts
                        self.current_turn_data["speech_start_monotonic_ms"] = mono
                    self.current_turn_data["server_audio_start_monotonic_ms"] = mono
                    self.current_turn_data["first_pcm_timestamp"] = ts
                    self.current_turn_data["first_pcm_monotonic_ms"] = mono

            elif event == "stt.inference.submitted":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    kind = data.get("inference_kind")
                    dur = data.get("audio_duration_ms") or 0
                    self.current_turn_data["audio_duration_ms"] = dur
                    self.log(
                        f"STT inference submitted: kind={kind}, audio_duration={dur}ms"
                    )

            elif event == "stt.inference.started":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    kind = data.get("inference_kind")
                    ts = data.get("inference_start_timestamp_ms") or int(
                        time.time() * 1000
                    )
                    if kind == "partial":
                        self.current_turn_data["partial_inference_started"] = ts
                    elif kind == "final":
                        self.current_turn_data["final_inference_started"] = ts
                    self.log(f"STT inference started: kind={kind} at {ts}")

            elif event in {"stt.partial.emitted", "STT_PARTIAL"}:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("timestamp_ms")
                    mono = data.get("monotonic_ms")
                    text = data.get("text") or ""
                    if (
                        self.current_turn_data["first_partial_timestamp"]
                        == "NOT_AVAILABLE"
                    ):
                        self.current_turn_data["first_partial_timestamp"] = ts
                        self.current_turn_data["first_partial_monotonic_ms"] = mono
                        self.log(f'Partial transcript: "{text}"')
                    self.current_turn_data["last_partial_timestamp"] = ts
                    partial = {
                        "turn": self.current_turn_data["turn_number"],
                        "partial_index": len(self.current_turn_data["partials"]) + 1,
                        "text": text,
                        "monotonic_timestamp": mono,
                        "utc_timestamp": (
                            datetime.datetime.fromtimestamp(ts / 1000, datetime.UTC).isoformat()
                            if ts is not None
                            else None
                        ),
                        "elapsed_from_first_audio_ms": (
                            round(mono - self.current_turn_data["first_pcm_monotonic_ms"], 1)
                            if is_number(mono)
                            and is_number(self.current_turn_data["first_pcm_monotonic_ms"])
                            else None
                        ),
                    }
                    self.current_turn_data["partials"].append(partial)
                    self.current_turn_data["partial_transcripts"].append(partial)
                    self.current_turn_data["partial_count"] = len(
                        self.current_turn_data["partial_transcripts"]
                    )
                    self.current_turn_data["no_partial_reason"] = None

            elif event == "stt.finalize.requested":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    partial_running = data.get("partial_task_running", False)
                    ts = data.get("finalize_requested_timestamp_ms") or int(
                        time.time() * 1000
                    )
                    self.current_turn_data["cancellation_requested"] = ts
                    self.log(
                        f"STT finalization requested (partial_task_running={partial_running})"
                    )

            elif event in {"stt.speech_end.marked", "STT_COMMIT_RECEIVED"}:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("speech_end_timestamp_ms")
                    mono = data.get("speech_end_monotonic_ms")
                    self.current_turn_data["speech_end_timestamp"] = ts
                    self.current_turn_data["speech_end_monotonic_ms"] = mono
                    self.current_turn_data["vad_end_timestamp"] = ts
                    self.current_turn_data["vad_end_monotonic_ms"] = mono
                    commit_ts = data.get("commit_received_timestamp_ms")
                    commit_mono = data.get("commit_received_monotonic_ms")
                    if commit_ts is not None:
                        self.current_turn_data["backend_commit_received_timestamp"] = (
                            commit_ts
                        )
                    if commit_mono is not None:
                        self.current_turn_data[
                            "backend_commit_received_monotonic_ms"
                        ] = commit_mono

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
                    self.log(
                        f"STT inference completed: kind={kind}, dur={dur}ms, text='{text}'"
                    )

            elif event in {"stt.final.completed", "STT_FINAL"}:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = data.get("final_transcript_timestamp_ms")
                    mono = data.get("final_transcript_monotonic_ms")
                    text = data.get("text") or ""
                    lang = data.get("language") or "en"
                    metrics = data.get("metrics") or {}

                    self.current_turn_data["final_count"] += 1
                    if self.current_turn_data["final_count"] > 1:
                        self.duplicate_finals_count += 1
                        self.log(
                            f"WARNING: Duplicate final transcript received! count={self.current_turn_data['final_count']}"
                        )

                    self.current_turn_data["final_transcript_timestamp"] = ts
                    self.current_turn_data["final_transcript_monotonic_ms"] = mono
                    self.current_turn_data["final_transcript"] = text
                    self.current_turn_data["hypothesis_raw"] = text
                    self.current_turn_data["hypothesis_normalized"] = normalize_transcript(text)
                    self.current_turn_data["language"] = lang
                    self.current_turn_data["stt_engine"] = self.engine_info.get(
                        "engine", self.current_turn_data["stt_engine"]
                    )
                    self.current_turn_data["stt_provider"] = self.engine_info.get(
                        "stt_provider", self.current_turn_data["stt_provider"]
                    )
                    self.current_turn_data["recognizer_id"] = self.engine_info.get(
                        "recognizer_id", self.current_turn_data["recognizer_id"]
                    )
                    self.current_turn_data["runtime"] = self.engine_info.get(
                        "runtime", self.current_turn_data["runtime"]
                    )
                    if self.reconnect_test_active:
                        self.reconnect_evidence["stt_continued_after_reconnect"] = True
                        self.reconnect_evidence["probe_final_transcript"] = text
                    self.current_turn_data["recognition_confidence"] = data.get(
                        "confidence", metrics.get("confidence")
                    )
                    if metrics.get("audio_duration_ms"):
                        self.current_turn_data["audio_duration_ms"] = metrics[
                            "audio_duration_ms"
                        ]
                    self.log(f'Final transcript: "{text}"')
                    self._check_turn_completed()

            elif event == "voice.response.cancel.received":
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    ts = int(time.time() * 1000)
                    self.current_turn_data["cancellation_completed"] = ts
                    self.current_turn_data["status"] = "FAIL"
                    self.current_turn_data["failure_reason"] = "cancelled_by_client"
                    self.current_turn_data["error"] = "cancelled_by_client"
                    self.log(f"Voice turn cancelled: {data}")
                    self._check_turn_completed()

            elif event in {
                "stt.inference.failed",
                "voice.error",
                "STT_ERROR",
                "STT_CANCELLED",
            }:
                turn_id = data.get("turn_id")
                if self._matches_current_turn(turn_id):
                    self.current_turn_data["status"] = "FAIL"
                    self.current_turn_data["failure_reason"] = str(
                        data.get("message") or event
                    )
                    self.current_turn_data["error"] = self.current_turn_data[
                        "failure_reason"
                    ]
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
            observed_utc_ms = int(time.time() * 1000)
            observed_monotonic_ms = round(time.monotonic() * 1000, 1)
            d["turn_completion_timestamp"] = observed_utc_ms
            d["turn_completion_monotonic_ms"] = observed_monotonic_ms
            d["turn_end_timestamp"] = observed_utc_ms
            d["turn_end_monotonic_ms"] = observed_monotonic_ms
            self.record_event(
                "turn.completed",
                turn=d.get("turn_number"),
                session_id=d.get("websocket_session_id"),
                details={"status": d.get("status")},
                monotonic_ms=observed_monotonic_ms,
            )
            self.turn_completed_event.set()

    def _logcat_loop(self):
        cmd = [
            *self._adb_command(),
            "logcat",
            "-v",
            "time",
            "VoiceAI-Bridge:I",
            "VoiceAI-VoiceGateway:I",
            "VoiceAI-Audio:I",
            "*:S",
        ]
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
        backend_log_file = self.source_backend_log_path or get_latest_backend_log_file()
        self.log(f"Tailing backend logs from: {backend_log_file}")
        if not backend_log_file or not backend_log_file.exists():
            self.log("Backend log source is missing; acceptance cannot pass")
            return

        with open(backend_log_file, encoding="utf-8", errors="replace") as f:
            # Read startup metadata too.  The worker/engine may have started
            # before the physical turn loop, and that metadata is required.
            f.seek(0)
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
                subprocess.run(
                    self._adb_command("reverse", "tcp:8000", "tcp:8000"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    self._adb_command("reverse", "tcp:8081", "tcp:8081"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            time.sleep(2)

    def start_listeners(self):
        try:
            subprocess.run(
                self._adb_command("reverse", "tcp:8000", "tcp:8000"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                self._adb_command("reverse", "tcp:8081", "tcp:8081"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        self.logcat_thread = threading.Thread(target=self._logcat_loop, daemon=True)
        self.backend_thread = threading.Thread(
            target=self._backend_log_loop, daemon=True
        )
        self.keepalive_thread = threading.Thread(
            target=self._adb_reverse_keepalive, daemon=True
        )

        self.logcat_thread.start()
        self.backend_thread.start()
        self.keepalive_thread.start()
        self.resource_thread = threading.Thread(
            target=self._resource_sampling_loop,
            name="phase4-resource-sampler",
            daemon=True,
        )
        self.resource_thread.start()

    def _discover_backend_pid(self) -> int | None:
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object {$_.CommandLine -match 'uvicorn|app.main:app'} | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            value = result.stdout.strip()
            return int(value) if value else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def _sample_process(self, pid: int, process_name: str) -> dict[str, Any] | None:
        command = (
            f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; "
            "[pscustomobject]@{name=$p.ProcessName;cpu=$p.TotalProcessorTime.TotalSeconds;"
            "rss=$p.WorkingSet64} | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            data = json.loads(result.stdout)
            now = time.monotonic()
            previous = self._resource_previous.get(pid)
            cpu_percent = 0.0
            if previous is not None:
                cpu_delta = max(0.0, float(data["cpu"]) - previous[0])
                wall_delta = max(0.001, now - previous[1])
                cpu_percent = round(cpu_delta / wall_delta / max(os.cpu_count() or 1, 1) * 100, 2)
            self._resource_previous[pid] = (float(data["cpu"]), now)
            return {
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "monotonic_ns": round(now * 1_000_000_000),
                "pid": pid,
                "cpu_percent": cpu_percent,
                "rss_mb": round(float(data["rss"]) / 1024 / 1024, 2),
                "process_name": str(data.get("name") or process_name),
            }
        except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError):
            return None

    def _resource_sampling_loop(self):
        while self.running and not self.resource_stop_event.is_set():
            if self.backend_pid is None:
                self.backend_pid = self._discover_backend_pid()
            for label, pid in (("backend", self.backend_pid), ("worker", self.worker_pid)):
                if pid is None:
                    continue
                sample = self._sample_process(pid, label)
                if sample is None:
                    continue
                sample["role"] = label
                if label == "backend":
                    self.backend_resource_samples.append(sample)
                else:
                    self.worker_resource_samples.append(sample)
                self.resources_log.write(json.dumps(sample, separators=(",", ":")) + "\n")
                self.resources_log.flush()
            self.resource_stop_event.wait(1.0)

    @staticmethod
    def _resource_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
        if not samples:
            return {"sample_count": 0, "peak_rss_mb": None, "average_cpu_percent": None}
        return {
            "sample_count": len(samples),
            "peak_rss_mb": max(sample["rss_mb"] for sample in samples),
            "average_cpu_percent": round(
                sum(sample["cpu_percent"] for sample in samples) / len(samples), 2
            ),
            "pids": sorted({sample["pid"] for sample in samples}),
        }

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
        if (
            not turn_id
            or turn_id == "NOT_AVAILABLE"
            or not response_id
            or response_id == "NOT_AVAILABLE"
        ):
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
                    return (
                        False,
                        f"FAIL_TIMESTAMP_INTEGRITY: negative duration in {field} ({val} ms)",
                    )

        first_part_lat = turn_dict.get("first_partial_latency_ms")
        if isinstance(first_part_lat, (int, float)) and first_part_lat < 0:
            return (
                False,
                f"FAIL_TIMESTAMP_INTEGRITY: negative first_partial_latency_ms ({first_part_lat} ms)",
            )

        # 5. Latency timeout check
        c2f = turn_dict.get("commit_to_final_ms")
        if isinstance(c2f, (int, float)) and c2f > 180000:
            return (
                False,
                f"FAIL_LATENCY: commit_to_final_ms={c2f} exceeds 180000ms timeout",
            )

        evidence_errors = validate_turn_evidence(turn_dict)
        if evidence_errors:
            return False, "FAIL_MANDATORY_EVIDENCE: " + "; ".join(evidence_errors)
        return True, "PASS"

    def finalize_turn_metrics(self, turn_dict: dict[str, Any]):
        # Calculate latencies only within the server monotonic clock domain.
        # Wall-clock values are retained for correlation and are never used here.
        s_audio_start_mono = turn_dict.get("server_audio_start_monotonic_ms")
        s_end_mono = turn_dict.get("speech_end_monotonic_ms")
        commit_mono = turn_dict.get("backend_commit_received_monotonic_ms")
        final_mono = turn_dict.get("final_transcript_monotonic_ms")
        t_start_mono = turn_dict.get("turn_start_monotonic_ms")
        t_comp_mono = turn_dict.get("turn_completion_monotonic_ms")

        # 1. Speech duration
        if s_end_mono not in [None, "NOT_AVAILABLE"] and s_audio_start_mono not in [
            None,
            "NOT_AVAILABLE",
        ]:
            turn_dict["speech_duration_ms"] = round(s_end_mono - s_audio_start_mono, 1)
        else:
            turn_dict["speech_duration_ms"] = "N/A"

        # 2. Commit -> Final (server monotonic)
        if commit_mono not in [None, "NOT_AVAILABLE"] and final_mono not in [
            None,
            "NOT_AVAILABLE",
        ]:
            turn_dict["commit_to_final_ms"] = round(final_mono - commit_mono, 1)
        else:
            turn_dict["commit_to_final_ms"] = "N/A"

        # 3. Speech -> Final (server monotonic)
        if s_end_mono not in [None, "NOT_AVAILABLE"] and final_mono not in [
            None,
            "NOT_AVAILABLE",
        ]:
            turn_dict["speech_to_final_ms"] = round(final_mono - s_end_mono, 1)
        else:
            turn_dict["speech_to_final_ms"] = "N/A"
        turn_dict["speech_end_to_final_ms"] = turn_dict["speech_to_final_ms"]

        # 4. Partial latency (server monotonic)
        f_part_mono = turn_dict.get("first_partial_monotonic_ms")
        if f_part_mono not in [None, "NOT_AVAILABLE"] and s_audio_start_mono not in [
            None,
            "NOT_AVAILABLE",
        ]:
            turn_dict["first_partial_latency_ms"] = round(
                f_part_mono - s_audio_start_mono, 1
            )
        else:
            turn_dict["first_partial_latency_ms"] = None
        turn_dict["first_audio_to_first_partial_ms"] = turn_dict["first_partial_latency_ms"]

        # 5. Turn processing (server monotonic domain only!)
        if t_comp_mono not in [None, "NOT_AVAILABLE"] and t_start_mono not in [
            None,
            "NOT_AVAILABLE",
        ]:
            turn_dict["turn_processing_ms"] = round(t_comp_mono - t_start_mono, 1)
        elif not isinstance(turn_dict.get("turn_processing_ms"), (int, float)):
            turn_dict["turn_processing_ms"] = "N/A"

        if turn_dict["partial_count"] == 0 and not turn_dict.get("no_partial_reason"):
            turn_dict["no_partial_reason"] = "No partial event was observed before final"
        calculate_turn_wer(turn_dict)

        # Run strict invariant validation
        is_valid, validation_status = self.validate_turn_invariants(turn_dict)
        if not is_valid:
            turn_dict["status"] = validation_status.split(":")[0].strip()
            turn_dict["failure_reason"] = validation_status
        else:
            turn_dict["status"] = "PASS"
            turn_dict["failure_reason"] = None

    def print_turn_banner(self, turn_num: int):
        reference = (
            PHASE4_REFERENCE_SENTENCES[turn_num - 1]
            if 1 <= turn_num <= len(PHASE4_REFERENCE_SENTENCES)
            else "No documented reference sentence for this turn"
        )
        banner = f"""
************************************************************
*                                                          *
*                 TURN {turn_num} / {self.required_turns} — READY                    *
*                                                          *
*               SPEAK YOUR SENTENCE NOW                    *
*                                                          *
************************************************************

READY — Speak your English sentence now.

Reference sentence (speak exactly):
{reference}

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
TURN {turn_dict["turn"]} RESULTS
------------------------------------------------------------

Final transcript:
"{turn_dict["final_transcript"]}"

Reference sentence:
"{turn_dict["reference_sentence"]}"

Recognition confidence: {turn_dict.get("recognition_confidence")}
WER: {turn_dict.get("wer")} (S={turn_dict.get("wer_substitutions")}, D={turn_dict.get("wer_deletions")}, I={turn_dict.get("wer_insertions")})

Speech start:            {format_iso(turn_dict["speech_start_timestamp"])}
Speech end:              {format_iso(turn_dict["speech_end_timestamp"])}
First partial:           {first_partial_str}
Commit:                  {format_iso(turn_dict["client_commit_timestamp"])}
Final transcript:        {format_iso(turn_dict["final_transcript_timestamp"])}
Final received:          {format_iso(turn_dict["android_final_received_timestamp"])}

Speech duration:         {turn_dict["speech_duration_ms"]} ms
First partial latency:   {turn_dict["first_partial_latency_ms"] if turn_dict["first_partial_latency_ms"] is not None else "N/A"} ms
Speech -> final:          {turn_dict["speech_to_final_ms"]} ms
Commit -> final:          {turn_dict["commit_to_final_ms"]} ms
Turn processing:         {turn_dict["turn_processing_ms"]} ms

PCM frames:              {turn_dict["pcm_frames"]}
PCM bytes:               {turn_dict["pcm_bytes"]}
Response ID:             {turn_dict["response_id"]}
Final count:             {turn_dict["final_count"]}

Status: {turn_dict["status"]} {f"({turn_dict.get('failure_reason')})" if turn_dict.get("failure_reason") else ""}
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
            float(t["speech_to_final_ms"])
            for t in self.turns
            if isinstance(t["speech_to_final_ms"], (int, float))
        ]
        commit_to_finals = [
            float(t["commit_to_final_ms"])
            for t in self.turns
            if isinstance(t["commit_to_final_ms"], (int, float))
        ]
        partial_latencies = [
            float(t["first_partial_latency_ms"])
            for t in self.turns
            if t["first_partial_latency_ms"] is not None
            and isinstance(t["first_partial_latency_ms"], (int, float))
        ]
        speech_durations = [
            float(t["speech_duration_ms"])
            for t in self.turns
            if isinstance(t["speech_duration_ms"], (int, float))
        ]

        stf_stats = calculate_percentiles(speech_to_finals)
        ctf_stats = calculate_percentiles(commit_to_finals)
        part_stats = calculate_percentiles(partial_latencies)
        dur_stats = calculate_percentiles(speech_durations)
        wer_turns = [
            t
            for t in self.turns
            if isinstance(t.get("wer_reference_words"), int)
            and isinstance(t.get("wer_substitutions"), int)
            and isinstance(t.get("wer_deletions"), int)
            and isinstance(t.get("wer_insertions"), int)
        ]
        wer_reference_words = sum(t["wer_reference_words"] for t in wer_turns)
        wer_errors = sum(
            t["wer_substitutions"] + t["wer_deletions"] + t["wer_insertions"]
            for t in wer_turns
        )
        aggregate_wer = (
            round(wer_errors / wer_reference_words, 6) if wer_reference_words else None
        )

        # Write the authoritative machine-readable evidence bundle before the
        # human-readable report. Failed turns remain in all evidence files.
        with open(self.turns_path, "w", encoding="utf-8") as handle:
            for turn in self.turns:
                handle.write(json.dumps(turn, separators=(",", ":")) + "\n")
        with open(self.partials_path, "w", encoding="utf-8") as handle:
            for turn in self.turns:
                for partial in turn.get("partial_transcripts", []):
                    handle.write(json.dumps(partial, separators=(",", ":")) + "\n")
        strict_latencies = [
            float(turn["speech_end_to_final_ms"])
            for turn in self.turns
            if is_number(turn.get("speech_end_to_final_ms"))
        ]
        strict_partial_latencies = [
            float(turn["first_audio_to_first_partial_ms"])
            for turn in self.turns
            if is_number(turn.get("first_audio_to_first_partial_ms"))
        ]
        with open(self.latency_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "speech_end_to_final_ms": latency_statistics(strict_latencies),
                    "first_audio_to_first_partial_ms": latency_statistics(
                        strict_partial_latencies
                    ),
                    "calculation_clock": "monotonic",
                },
                handle,
                indent=2,
            )
        with open(self.wer_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "turns": [turn.get("per_turn_wer") for turn in self.turns],
                    "corpus": {
                        "substitutions": sum(
                            int(turn.get("wer_substitutions") or 0) for turn in self.turns
                        ),
                        "deletions": sum(
                            int(turn.get("wer_deletions") or 0) for turn in self.turns
                        ),
                        "insertions": sum(
                            int(turn.get("wer_insertions") or 0) for turn in self.turns
                        ),
                        "reference_word_count": sum(
                            int(turn.get("wer_reference_words") or 0) for turn in self.turns
                        ),
                        "wer": aggregate_wer,
                    },
                },
                handle,
                indent=2,
            )
        resource_summary = {
            "backend": self._resource_summary(self.backend_resource_samples),
            "worker": self._resource_summary(self.worker_resource_samples),
            "remote_requests": self.remote_request_samples,
        }
        with open(self.resource_summary_path, "w", encoding="utf-8") as handle:
            json.dump(resource_summary, handle, indent=2)
        with open(self.reconnect_path, "w", encoding="utf-8") as handle:
            json.dump(self.reconnect_evidence, handle, indent=2)
        self.acceptance_result = evaluate_acceptance_gates(
            self.turns,
            required_turns=self.required_turns,
            evidence={
                "references_stored_before_recognition": self.references_path.exists(),
                "backend_resource_samples": self.backend_resource_samples,
                "worker_resource_samples": self.worker_resource_samples,
                "remote_request_samples": self.remote_request_samples,
                "android_device": getattr(self, "device_verified", False),
                "apk_install_launch": self.apk_install_launch,
                "websocket_physical_path": self.websocket_physical_path,
                "windows_worker_deployment": self.windows_worker_deployment,
                "remote_stt_deployment": self.remote_stt_deployment,
                "automated_validation_passed": self.automated_validation_passed,
                "reconnect": self.reconnect_evidence,
            },
            expected_engine="remote",
        )
        with open(self.validation_summary_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "test_start_utc": self.test_start_utc,
                    "test_end_utc": self.test_end_utc,
                    "backend_pid": self.backend_pid,
                    "worker_pid": self.worker_pid,
                    "device": self.target_device,
                    "engine": self.engine_info,
                    "acceptance": self.acceptance_result,
                },
                handle,
                indent=2,
            )
        blocked = [
            name for name, passed in self.acceptance_result["gates"].items() if not passed
        ]
        with open(self.validation_status_path, "w", encoding="utf-8") as handle:
            handle.write(
                "PHASE 4: PASS\n"
                if self.acceptance_result["pass"]
                else "PHASE 4: IMPLEMENTED — ACCEPTANCE PENDING\n"
            )
            if blocked:
                handle.write("Blocked gates: " + ", ".join(blocked) + "\n")

        summary_md = f"""# Phase 4 — 10-Turn Physical STT Validation Summary

Validation timestamp: {self.timestamp}  
Device: `{self.target_device}`  
Required turns: {self.required_turns}  
Completed turns: {len(self.turns)} / {self.required_turns}  

## Aggregate Latency Table

| Metric | Average | P50 (Median) | P95 | Max | Min |
| :--- | ---: | ---: | ---: | ---: | ---: |
| **Speech Duration** | {dur_stats["avg"]} ms | {dur_stats["p50"]} ms | {dur_stats["p95"]} ms | {dur_stats["max"]} ms | {dur_stats["min"]} ms |
| **First Partial Latency** | {part_stats["avg"]} ms | {part_stats["p50"]} ms | {part_stats["p95"]} ms | {part_stats["max"]} ms | {part_stats["min"]} ms |
| **Speech → Final** | {stf_stats["avg"]} ms | {stf_stats["p50"]} ms | {stf_stats["p95"]} ms | {stf_stats["max"]} ms | {stf_stats["min"]} ms |
| **Commit → Final** | {ctf_stats["avg"]} ms | {ctf_stats["p50"]} ms | {ctf_stats["p95"]} ms | {ctf_stats["max"]} ms | {ctf_stats["min"]} ms |

## Per-Turn Verification

| Turn | Transcript | Speech Duration | First Partial | Speech→Final | Commit→Final | Final Count | Status |
| ---: | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
"""
        wer_rows: list[str] = []
        for t in self.turns:
            part_str = (
                f"{t['first_partial_latency_ms']} ms"
                if t["first_partial_latency_ms"] is not None
                else "N/A"
            )
            summary_md += f"| {t['turn']} | {t['final_transcript']} | {t['speech_duration_ms']} ms | {part_str} | {t['speech_to_final_ms']} ms | {t['commit_to_final_ms']} ms | {t['final_count']} | {t['status']} |\n"
            wer_rows.append(
                f"| {t['turn']} | {t['reference_sentence']} | {t['final_transcript']} | "
                f"{t.get('wer_substitutions', 'N/A')} | {t.get('wer_deletions', 'N/A')} | "
                f"{t.get('wer_insertions', 'N/A')} | {t.get('wer', 'N/A')} | "
                f"{t.get('recognition_confidence', 'N/A')} |"
            )

        wer_rows_text = "\n".join(wer_rows)
        summary_md += f"""
## WER Table

Normalization: `{self.turns[0]["wer_normalization"] if self.turns else "N/A"}`
Aggregate WER: **{aggregate_wer if aggregate_wer is not None else "NOT_AVAILABLE"}** ({len(wer_turns)} measured turns)

| Turn | Reference | Hypothesis | S | D | I | WER | Confidence |
| ---: | :--- | :--- | ---: | ---: | ---: | ---: | ---: |
{wer_rows_text}

## Event Statistics

- **Total completed turns**: {len(self.turns)} / {self.required_turns}
- **Turns with partials**: {len(partial_latencies)} / {len(self.turns)}
- **Duplicate finals**: {self.duplicate_finals_count}
- **Stale responses**: {self.stale_responses_count}
- **Correlation mismatches**: {self.correlation_mismatches_count}
- **WER measured turns**: {len(wer_turns)} / {len(self.turns)}
- **Aggregate WER**: {aggregate_wer if aggregate_wer is not None else "NOT_AVAILABLE"}
"""
        with open(self.summary_file_path, "w", encoding="utf-8") as f:
            f.write(summary_md)

        self._write_final_acceptance_report(
            summary_md, stf_stats, ctf_stats, part_stats
        )

    def _write_final_acceptance_report(
        self,
        summary_md: str,
        stf_stats: dict[str, float],
        ctf_stats: dict[str, float],
        part_stats: dict[str, float],
    ):
        acceptance_ready = bool(self.acceptance_result["pass"])
        report_status = "PASS" if acceptance_ready else "ACCEPTANCE PENDING"

        report = f"""# Phase 4 — Final Physical English Validation Report

Validation timestamp: {self.timestamp}  
Scope: English-only, CPU-only Phase 4 physical acceptance audit on physical `{self.target_device}`.

## Status

`PHASE 4 WINDOWS STT PHYSICAL TEST: {report_status}`

{summary_md}

## Final Gate

```text
PHASE 4: {"PASS" if report_status == "PASS" else "IMPLEMENTED — ACCEPTANCE PENDING"}
```
"""
        turn_rows = "\n".join(
            "| {turn} | {reference} | {final} | {partials} | {partial_latency} | "
            "{speech_latency} | {wer} | {status} |".format(
                turn=turn.get("turn_number"),
                reference=turn.get("reference_raw", "NOT_AVAILABLE"),
                final=turn.get("hypothesis_raw", "NOT_AVAILABLE"),
                partials=turn.get("partial_count", 0),
                partial_latency=turn.get("first_audio_to_first_partial_ms", "N/A"),
                speech_latency=turn.get("speech_end_to_final_ms", "N/A"),
                wer=turn.get("wer", "N/A"),
                status=turn.get("status", "FAIL"),
            )
            for turn in self.turns
        )
        gate_rows = "\n".join(
            f"| {name} | {'PASS' if passed else 'FAIL'} |"
            for name, passed in self.acceptance_result["gates"].items()
        )
        report += f"""
## 1. Executive result

The non-bypassable acceptance result is **{report_status}**.

## 2. Exact code/environment changes

The active STT path is the configured remote transcription API; Windows Speech
and Whisper runtimes are not accepted by this report.

## 3. Gradle blocker resolution

RN 0.87's included Gradle plugin requests the Foojay convention plugin at
version 1.0.0. The project settings already expose Maven Central, Google, and
the Gradle Plugin Portal; the failure was dependency availability to Gradle.

## 4. ADB blocker resolution

Device identity and ADB results are recorded in `android_or_adb.log` and
`events.jsonl`. A missing or unauthorized device keeps the result pending.

## 5. Remote STT API configuration

The remote endpoint is configured by `STT_API_URL` and the credential by
`STT_API_KEY`. Credentials are not included in this report or its logs.

## 6. Automated test results

Automated test command results are supporting evidence only; synthetic results
cannot satisfy the physical-turn gates.

## 7. Device information

Device: `{self.target_device}`; ADB serial: `{self.adb_serial}`; verified:
`{self.device_verified}`.

## 8. Remote provider/runtime information

```json
{json.dumps(self.engine_info, indent=2)}
```

## 9–15. Physical turn, WER, partial, and latency results

| Turn | Reference | Final | Partials | First partial ms | End→Final ms | WER | Result |
|---:|---|---|---:|---:|---:|---:|---|
{turn_rows or '| — | NOT_AVAILABLE | NOT_AVAILABLE | — | — | — | — | FAIL |'}

Corpus WER and authoritative latency statistics are in `wer.json` and
`latency.json`. All latency calculations use monotonic timestamps only.

## 16–17. Backend and worker CPU/memory

Backend resource summaries and remote request samples are in
`resource_summary.json`; raw process samples are in `resources.jsonl`.
Missing backend or remote request evidence fails the acceptance predicate.

## 18. Disconnect/reconnect evidence

```json
{json.dumps(self.reconnect_evidence, indent=2)}
```

## 19. Failures/anomalies

```json
{json.dumps(self.acceptance_result["turn_errors"], indent=2)}
```

## 20. Evidence file paths

Evidence directory: `{self.output_dir}`

Required files: `references.json`, `turns.jsonl`, `partials.jsonl`,
`events.jsonl`, `latency.json`, `wer.json`, `resources.jsonl`,
`resource_summary.json`, `reconnect.json`, `backend.log`, `stt_worker.log`,
`android_or_adb.log`, `validation_summary.json`, and `validation_status.txt`.

## 21. Acceptance predicate result

| Gate | Result |
|---|---|
{gate_rows}

## 22. Final Phase 4 status

```text
{"PHASE 4: PASS" if report_status == "PASS" else "PHASE 4: IMPLEMENTED — ACCEPTANCE PENDING"}
```
"""
        report += (
            "\nEvidence directory: `"
            + str(self.output_dir)
            + "`\n\nGate details:\n```json\n"
            + json.dumps(self.acceptance_result, indent=2)
            + "\n```\n"
        )
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

        if not self.verify_device():
            self.log("TURN LOOP NOT STARTED: physical device gate failed")
            self.test_end_utc = datetime.datetime.now(datetime.UTC).isoformat()
            self.test_end_monotonic_ms = round(time.monotonic() * 1000, 1)
            self.save_json_and_summary()
            self.stop()
            return

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

            if (
                not completed
                or self.current_turn_data["final_transcript"] == "NOT_AVAILABLE"
            ):
                self.log(f"""
 Turn failed - insufficient audio or timeout.
 Advancing to Turn {self.current_turn + 1} without fabricating evidence.""")
                self.current_turn_data["status"] = "FAIL"
                self.current_turn_data["error"] = "turn_timeout_or_no_final"
                self.current_turn_data["failure_reason"] = self.current_turn_data["error"]
                if self.current_turn_data["partial_count"] == 0:
                    self.current_turn_data["no_partial_reason"] = "No partial or final event before timeout"
                self._check_turn_completed()

            self.finalize_turn_metrics(self.current_turn_data)
            self.turns.append(self.current_turn_data)
            self.print_turn_results(self.current_turn_data)
            self.save_json_and_summary()

            self.current_turn += 1
            time.sleep(1)

        self.run_reconnect_validation()
        self.test_end_utc = datetime.datetime.now(datetime.UTC).isoformat()
        self.test_end_monotonic_ms = round(time.monotonic() * 1000, 1)
        self.save_json_and_summary()

        # Final 10-turn summary print
        speech_to_finals = [
            float(t["speech_to_final_ms"])
            for t in self.turns
            if isinstance(t["speech_to_final_ms"], (int, float))
        ]
        commit_to_finals = [
            float(t["commit_to_final_ms"])
            for t in self.turns
            if isinstance(t["commit_to_final_ms"], (int, float))
        ]
        stf_stats = calculate_percentiles(speech_to_finals)
        ctf_stats = calculate_percentiles(commit_to_finals)

        final_summary = f"""============================================================
PHASE 4 — 10 TURN TEST COMPLETE
============================================================

Valid turns: {len(self.turns)} / {self.required_turns}

Partial events: {sum(1 for t in self.turns if t["first_partial_latency_ms"] is not None)} / {len(self.turns)}
Final events: {len(self.turns)} / {self.required_turns}
Duplicate finals: {self.duplicate_finals_count}
Stale responses: {self.stale_responses_count}
Correlation mismatches: {self.correlation_mismatches_count}

Average speech → final: {stf_stats["avg"]} ms
P50 speech → final:     {stf_stats["p50"]} ms
P95 speech → final:     {stf_stats["p95"]} ms

Average commit → final: {ctf_stats["avg"]} ms
P50 commit → final:     {ctf_stats["p50"]} ms
P95 commit → final:     {ctf_stats["p95"]} ms

============================================================
10-turn physical evidence collection: COMPLETE
"""
        self.log(final_summary)
        self.stop()

    def run_reconnect_validation(self) -> None:
        """Use the existing Android controls for one real reconnect probe."""

        self.reconnect_test_active = True
        self.turn_completed_event.clear()
        self.current_turn_data = self._new_turn_dict(self.required_turns + 1)
        self.reconnect_evidence["started_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
        self.reconnect_evidence["started_monotonic_ms"] = round(time.monotonic() * 1000, 1)
        self.record_event("reconnect.test.started", details={"probe_turn": 11})
        self.log(
            """
==================================================
PHYSICAL WEBSOCKET DISCONNECT/RECONNECT CHECK

Use the existing app controls:
1. Tap Disconnect Voice Gateway.
2. Tap Connect Voice Gateway.
3. Tap Start Voice Session.
4. Start one fresh voice turn and speak exactly:
   \"The connection recovered and Windows speech is still active.\"
5. Commit that turn using the existing app control.

Waiting for real disconnect, reconnect, PCM, and Windows final events...
=================================================="""
        )
        completed = self.turn_completed_event.wait(timeout=180.0)
        if not completed or not self.reconnect_evidence["stt_continued_after_reconnect"]:
            self.reconnect_evidence["error"] = (
                "Reconnect probe did not produce a post-reconnect Windows final"
            )
            self.log("RECONNECT CHECK FAILED: no post-reconnect Windows final received")
        self.reconnect_evidence["ended_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
        self.reconnect_evidence["ended_monotonic_ms"] = round(time.monotonic() * 1000, 1)
        self.reconnect_test_active = False

    def stop(self):
        self.running = False
        self.resource_stop_event.set()
        if hasattr(self, "logcat_proc"):
            try:
                self.logcat_proc.terminate()
            except Exception:
                pass
        if self.resource_thread is not None:
            self.resource_thread.join(timeout=3)
        for handle in (
            self.log_file,
            self.backend_log,
            self.worker_log,
            self.android_log,
            self.resources_log,
        ):
            if not handle.closed:
                handle.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase 4 physical STT validation")
    parser.add_argument("--device", default="RMX5070", help="ADB target device")
    parser.add_argument("--turns", type=int, default=10, help="Required turns")
    parser.add_argument(
        "--output-dir", type=str, default=None, help="Directory to store outputs"
    )
    parser.add_argument(
        "--backend-log", type=str, default=None, help="Fresh backend JSON log to tail"
    )
    parser.add_argument(
        "--automated-validation-manifest",
        type=str,
        default=None,
        help="JSON manifest with pass=true from fresh automated checks",
    )
    args = parser.parse_args()

    out_path = Path(args.output_dir) if args.output_dir else None
    runner = InteractiveValidationRunner(
        target_device=args.device,
        required_turns=args.turns,
        output_dir=out_path,
        backend_log_path=Path(args.backend_log) if args.backend_log else None,
        automated_validation_manifest_path=(
            Path(args.automated_validation_manifest)
            if args.automated_validation_manifest
            else None
        ),
    )
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
