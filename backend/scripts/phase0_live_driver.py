#!/usr/bin/env python3
"""Drive the disposable-account Phase 0 acoustic baseline with local Windows TTS."""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

try:
    from scripts.analyze_latency import (
        METRICS,
        _events,
        _same_clock,
        accounting_for_turn,
        correlation_errors,
        metrics_for_turn,
        percentile,
        pipeline_metrics_for_turn,
    )
    from scripts.live_latency import FileTail, LiveCollector
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.analyze_latency import (
        METRICS,
        _events,
        _same_clock,
        accounting_for_turn,
        correlation_errors,
        metrics_for_turn,
        percentile,
        pipeline_metrics_for_turn,
    )
    from scripts.live_latency import FileTail, LiveCollector

from app.core.config import get_settings
from app.models import ConversationTurn, Message, Reminder, Task, VoiceSession
from app.services.voice_confirmation import RedisVoiceConfirmationStore

ROOT = Path(__file__).resolve().parents[2]
APP_PACKAGE = "com.voiceaipoc"
ANDROID_USER_HOME = ROOT / ".android-user"
TERMINAL_TTS_EVENTS = {"tts_playback_complete", "tts_playback_completed"}
TTS_START_EVENTS = {"tts_playback_start", "tts_playback_started", "tts.playback.started"}


class BaselineAbort(RuntimeError):
    pass


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def transcript_match(prompt: str, transcript: str, must_contain: list[str]) -> tuple[bool, float]:
    normalized_prompt = normalize_text(prompt)
    normalized_transcript = normalize_text(transcript)
    ratio = SequenceMatcher(None, normalized_prompt, normalized_transcript).ratio()
    words = set(normalized_transcript.split())
    required = all(normalize_text(term) in normalized_transcript for term in must_contain)
    if normalize_text(prompt) == "yes":
        required = bool(words & {"yes", "yeah", "yep", "confirm", "confirmed"})
    return ratio >= 0.58 and required, round(ratio, 3)


def voice_control_from_xml(xml_text: str) -> tuple[str, tuple[int, int]] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for node in root.iter("node"):
        if node.attrib.get("resource-id") != "voice-control":
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        label = node.attrib.get("content-desc", "")
        if match and label:
            left, top, right, bottom = map(int, match.groups())
            return label, ((left + right) // 2, (top + bottom) // 2)
    return None


def is_active_barge_event(record: dict[str, Any]) -> bool:
    event = str(record.get("event", ""))
    if not event.startswith("barge_in_"):
        return False
    metadata = event_metadata(record)
    if event in {"barge_in_playback_stop_requested", "barge_in_playback_stopped"} and (
        metadata.get("reason") == "response_not_active" or metadata.get("playback_active") is False
    ):
        return False
    if event == "barge_in_degraded" and metadata.get("playback_active") is False:
        return False
    return True


class EvidenceDatabase:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.database_url or not self.settings.redis_url:
            raise BaselineAbort("Database or Redis connection is not configured.")
        self.engine = create_async_engine(self.settings.database_dsn, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        from redis.asyncio import Redis

        self.redis = Redis.from_url(self.settings.redis_dsn, decode_responses=True)
        self.confirmations = RedisVoiceConfirmationStore(
            self.redis,
            ttl_seconds=self.settings.voice_confirmation_ttl_seconds,
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()

    async def ping(self) -> None:
        await self.redis.ping()
        async with self.engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")

    async def session_owner(self, session_id: str) -> tuple[uuid.UUID, uuid.UUID] | None:
        async with self.sessions() as db:
            row = await db.scalar(
                select(VoiceSession).where(VoiceSession.id == uuid.UUID(session_id))
            )
            if row is None:
                return None
            return row.user_id, row.device_id

    async def transcript_for_turn(self, turn_id: str, expected_session_id: str) -> str | None:
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(Message.content)
                    .join(
                        ConversationTurn,
                        (ConversationTurn.id == Message.turn_id)
                        & (ConversationTurn.user_id == Message.user_id),
                    )
                    .where(
                        Message.turn_id == uuid.UUID(turn_id),
                        Message.role == "user",
                        Message.is_final.is_(True),
                        ConversationTurn.session_id == uuid.UUID(expected_session_id),
                    )
                    .order_by(Message.sequence_no.desc())
                    .limit(1)
                )
            ).first()
            return rows[0] if rows else None

    async def pending_confirmation(self, session_id: str):
        owner = await self.session_owner(session_id)
        if owner is None:
            return None
        user_id, device_id = owner
        pending = await self.confirmations.get((user_id, device_id, uuid.UUID(session_id)))
        if pending is None:
            return None
        if (
            pending.authenticated_user_id != user_id
            or pending.device_id != device_id
            or pending.session_id != uuid.UUID(session_id)
        ):
            return None
        return pending

    async def reminders_for_marker(self, session_id: str, marker: str):
        owner = await self.session_owner(session_id)
        if owner is None:
            return []
        user_id, _device_id = owner
        async with self.sessions() as db:
            rows = await db.execute(
                select(Reminder.id, Reminder.title, Reminder.status)
                .where(Reminder.user_id == user_id, Reminder.title.ilike(f"%{marker}%"))
                .order_by(Reminder.created_at.asc())
            )
            return list(rows.all())

    async def tasks_for_marker(self, session_id: str, marker: str):
        owner = await self.session_owner(session_id)
        if owner is None:
            return []
        user_id, _device_id = owner
        async with self.sessions() as db:
            rows = await db.execute(
                select(Task.id, Task.title, Task.status)
                .where(Task.user_id == user_id, Task.title.ilike(f"%{marker}%"))
                .order_by(Task.created_at.asc())
            )
            return list(rows.all())


def process_env() -> dict[str, str]:
    ANDROID_USER_HOME.mkdir(parents=True, exist_ok=True)
    home = ANDROID_USER_HOME.resolve()
    return {
        **os.environ,
        "HOME": str(home),
        "ANDROID_SDK_HOME": str(home),
        "ANDROID_USER_HOME": str(home / ".android"),
    }


def run_command(args: list[str], *, timeout: float = 15.0) -> str:
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=process_env(),
    )
    return result.stdout.strip()


def check_speech_synthesizer() -> None:
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$n=$s.GetInstalledVoices().Count; $s.Dispose(); "
        "if ($n -lt 1) { exit 3 }; Write-Output $n"
    )
    try:
        output = run_command(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise BaselineAbort("Local Windows System.Speech is unavailable.") from error
    if not output.isdigit() or int(output) < 1:
        raise BaselineAbort("No installed local Windows speech voice is available.")


def speak(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$b=[Convert]::FromBase64String('" + encoded + "'); "
        "$t=[Text.Encoding]::UTF8.GetString($b); "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate=0; $s.Volume=85; $s.Speak($t); $s.Dispose()"
    )
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=process_env(),
        startupinfo=startup,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        _stdout, stderr = process.communicate(timeout=35)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.communicate()
        raise BaselineAbort("Local prompt playback exceeded its 35-second bound.") from error
    if process.returncode != 0:
        raise BaselineAbort("Local Windows prompt playback failed: " + (stderr or "unknown error"))


def check_local_health(url: str, expected_status: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if response.status != 200:
                raise BaselineAbort(f"{url.rsplit('/', 1)[-1]} returned HTTP {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BaselineAbort(f"Backend health check failed for {url}.") from error
    if payload.get("status") != expected_status:
        raise BaselineAbort(f"Backend endpoint returned status {payload.get('status')!r}.")
    return payload


def check_tcp(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(2.0)
        return connection.connect_ex((host, port)) == 0


def check_device(serial: str, model: str) -> dict[str, str]:
    if os.name == "nt":
        profile_path = ctypes.create_unicode_buffer(260)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 40, None, 0, profile_path)
        if result != 0 or not profile_path.value:
            raise BaselineAbort(
                "Windows cannot resolve the user profile with SHGetFolderPath; "
                "ADB cannot initialize its .android directory."
            )
    adb = shutil.which("adb")
    if adb is None:
        raise BaselineAbort("adb is not available on PATH.")
    lines = run_command([adb, "devices", "-l"]).splitlines()[1:]
    device = next(
        (
            line
            for line in lines
            if len(line.split()) >= 2 and line.split()[0] == serial and line.split()[1] == "device"
        ),
        None,
    )
    if device is None:
        raise BaselineAbort("Expected Android device is not authorized and online in ADB.")
    if f"model:{model}" not in device:
        raise BaselineAbort("The connected Android model does not match the expected test phone.")
    reverses = run_command([adb, "reverse", "--list"])
    for port in (8000, 8081):
        if f"tcp:{port} tcp:{port}" not in reverses:
            run_command([adb, "reverse", f"tcp:{port}", f"tcp:{port}"])
    reverses = run_command([adb, "reverse", "--list"])
    if any(f"tcp:{port} tcp:{port}" not in reverses for port in (8000, 8081)):
        raise BaselineAbort("ADB reverse verification failed for backend or Metro.")
    package_state = run_command([adb, "shell", "dumpsys", "package", APP_PACKAGE])
    permission = re.search(
        r"android\.permission\.RECORD_AUDIO:\s*granted=(true|false)",
        package_state,
        flags=re.IGNORECASE,
    )
    if permission is None or permission.group(1).casefold() != "true":
        raise BaselineAbort("Microphone permission is not granted to the voice app.")
    pid = run_command([adb, "shell", "pidof", APP_PACKAGE])
    if not pid:
        run_command([adb, "shell", "monkey", "-p", APP_PACKAGE, "1"])
    return {
        "serial": serial,
        "model": model,
        "device_line": device,
        "reverse": reverses,
        "app_pid": pid or "launched by monkey",
        "microphone_permission": "granted",
    }


def event_metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def accepted_turn_records(collector: LiveCollector, turn_id: str) -> list[dict[str, Any]]:
    return [
        record
        for record in collector.records.get(turn_id, [])
        if not event_metadata(record).get("collector_rejection")
    ]


def _find_metric_source(
    records: list[dict[str, Any]], key: str
) -> tuple[str, dict[str, Any] | None]:
    local_event = {
        "stt_request_duration": "stt_request_duration",
        "memory_context_pipeline": "memory_context_pipeline_completed",
        "llm_ttft": "llm_first_token_received",
        "llm_total": "llm_request_completed",
        "tts_ttfa": "tts_first_audio_received",
        "tts_generation_total": "tts_response_metrics",
        "playback_duration": "tts_playback_complete",
    }
    pairs = {
        "speech_duration": ("speech_start", "speech_end"),
        "speech_end_to_stt_final": ("speech_end", "stt_final"),
        "device_commit_to_stt_final": ("turn_commit_sent", "client_stt_final_received"),
        "speech_end_to_first_token": ("device_speech_end", "first_assistant_token_received"),
        "device_commit_to_first_text": (
            "native_turn_commit_sent",
            "first_assistant_token_received",
        ),
        "device_commit_to_first_audio": ("native_turn_commit_sent", "tts_first_chunk_received"),
        "device_commit_to_playback_complete": ("turn_commit_sent", "tts_playback_complete"),
        "speech_end_to_first_audio": ("device_speech_end", "tts_first_chunk_received"),
        "end_to_end": ("device_speech_end", "tts_playback_complete"),
        "backend_speech_end_to_first_text": (
            "speech_end",
            "first_assistant_text_send_started",
        ),
        "backend_speech_end_to_first_audio": ("speech_end", "tts_first_audio_received"),
        "backend_complete_turn": ("speech_end", "turn_complete"),
    }
    if key in local_event:
        candidates = [record for record in records if record.get("event") == local_event[key]]
        if key == "tts_generation_total":
            candidates = [
                record for record in records if record.get("event") == "tts_response_metrics"
            ]
        candidates = [
            record for record in candidates if isinstance(record.get("duration_ms"), (int, float))
        ]
        if candidates:
            source = candidates[0]
            return f"source-local:{source.get('clock_domain')}:{source.get('process')}", source
    if key in pairs:
        events = _events(records)
        start_name, end_name = pairs[key]
        start = events.get(start_name)
        end = events.get(end_name)
        if start is not None and end is not None and _same_clock(start, end):
            return f"paired:{start.get('clock_domain')}:{start.get('process')}", start
    if key in {"embedding", "vector_search", "fts", "rrf", "rerank"}:
        event = {
            "embedding": "embedding_completed",
            "vector_search": "vector_search_completed",
            "fts": "fts_completed",
            "rrf": "rrf_completed",
            "rerank": "rerank_completed",
        }[key]
        source = next((record for record in records if record.get("event") == event), None)
        if source is not None:
            return f"source-local:{source.get('clock_domain')}:{source.get('process')}", source
    return "n/a — no compatible measured endpoints", None


def metric_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values = metrics_for_turn(records)
    result: dict[str, dict[str, Any]] = {}
    for label, key in METRICS:
        source, anchor = _find_metric_source(records, key)
        result[key] = {
            "name": label,
            "value_ms": values.get(key),
            "source": source,
            "clock_domain": anchor.get("clock_domain") if anchor else None,
            "process": anchor.get("process") if anchor else None,
            "session_id": anchor.get("session_id") if anchor else None,
            "turn_id": anchor.get("turn_id") if anchor else None,
            "response_id": anchor.get("response_id") if anchor else None,
        }
    pipeline = pipeline_metrics_for_turn(records)
    for key in ("stt_final_to_orchestration", "server_commit_to_stt_final"):
        name = (
            "Post-STT orchestration"
            if key == "stt_final_to_orchestration"
            else "Backend commit -> STT final"
        )
        source_event = (
            "orchestration_started" if key == "stt_final_to_orchestration" else "stt_final"
        )
        anchor = next((r for r in records if r.get("event") == source_event), None)
        result[key] = {
            "name": name,
            "value_ms": pipeline.get(key),
            "source": f"source-local:{anchor.get('clock_domain')}:{anchor.get('process')}"
            if anchor
            else "n/a — no compatible measured endpoints",
            "clock_domain": anchor.get("clock_domain") if anchor else None,
            "process": anchor.get("process") if anchor else None,
            "session_id": anchor.get("session_id") if anchor else None,
            "turn_id": anchor.get("turn_id") if anchor else None,
            "response_id": anchor.get("response_id") if anchor else None,
        }
    return result


class PhysicalBaseline:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.settings = get_settings()
        if self.settings.router_mode != "off" or self.settings.router_cohort_percent != 0:
            raise BaselineAbort("Router flags are not off/zero; no capture was started.")
        if not args.disposable_account_confirmed:
            raise BaselineAbort(
                "Disposable-account confirmation is required before the write cases."
            )
        self.manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        self.run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.event_path = ROOT / "logs" / f"phase0_physical_baseline_{self.run_id}.jsonl"
        self.row_path = ROOT / "logs" / f"phase0_physical_baseline_rows_{self.run_id}.jsonl"
        self.summary_json = ROOT / "logs" / f"phase0_physical_baseline_summary_{self.run_id}.json"
        safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", self.run_id)
        self.summary_md = ROOT / "docs" / f"{safe_run_id}_phase0_automated_physical_baseline.md"
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        self.row_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_md.parent.mkdir(parents=True, exist_ok=True)
        self.backend_trace = ROOT / "logs" / "latency_trace.jsonl"
        self.collector = LiveCollector(
            backend_trace=self.backend_trace,
            output=self.event_path,
            metro_log=None,
            adb_command="adb",
            clear_logcat=False,
            from_start=False,
            once=False,
            verbose=False,
        )
        self.collector_thread: threading.Thread | None = None
        self.tail: FileTail | None = None
        self.records: list[dict[str, Any]] = []
        self.session_id: str | None = None
        self.last_pong_at: float | None = None
        self.health_checked_at = 0.0
        self.playback_active: set[str] = set()
        self.rows: list[dict[str, Any]] = []
        self.attempted_prompt_count = 0
        self.anomalous_turns: dict[str, dict[str, Any]] = {}
        self.lifecycle_diagnostics: list[dict[str, Any]] = []
        self.db = EvidenceDatabase()
        self.environment: dict[str, Any] = {}

    async def close(self) -> None:
        if self.collector_thread is not None:
            self.collector.stop_event.set()
            self.collector_thread.join(timeout=4)
        else:
            self.collector.close()
        await self.db.close()

    def preflight(self) -> None:
        self.environment["device"] = check_device(self.args.device_serial, self.args.device_model)
        if not check_tcp("127.0.0.1", 8081):
            raise BaselineAbort("Metro is not reachable on local port 8081.")
        health = check_local_health("http://127.0.0.1:8000/health", "ok")
        ready = check_local_health("http://127.0.0.1:8000/ready", "ready")
        self.environment["health"] = health
        self.environment["ready"] = ready
        if ready.get("dependencies", {}).get("postgres", {}).get("status") != "ok":
            raise BaselineAbort("PostgreSQL is not ready.")
        if ready.get("dependencies", {}).get("redis", {}).get("status") != "ok":
            raise BaselineAbort("Redis is not ready.")
        if ready.get("dependencies", {}).get("llm", {}).get("status") != "ready":
            raise BaselineAbort("LLM provider is not ready.")
        if ready.get("dependencies", {}).get("tts", {}).get("status") != "ready":
            raise BaselineAbort("TTS provider is not initialized and ready.")
        memory = ready.get("dependencies", {}).get("memory", {}).get("providers", {})
        if (
            memory.get("embedding", {}).get("status") != "ok"
            or memory.get("reranker", {}).get("status") != "ok"
        ):
            raise BaselineAbort("Embedding or reranker is not ready.")
        if not self.settings.tts_api_url:
            raise BaselineAbort("The configured TTS provider URL is missing.")
        check_speech_synthesizer()
        self.environment["router_mode"] = self.settings.router_mode
        self.environment["router_cohort_percent"] = self.settings.router_cohort_percent
        self.environment["memory_retrieval_mode"] = self.settings.memory_retrieval_mode
        self.environment["gateway_path"] = "legacy orchestration; router inactive"
        self.environment["metro"] = "127.0.0.1:8081 reachable"
        self.environment["speech"] = (
            "Windows System.Speech installed; prompts are not cloud-generated"
        )

    def start_capture(self) -> None:
        self.collector.start()
        self.tail = FileTail(self.event_path, from_end=False)
        self.collector_thread = threading.Thread(
            target=self.collector.run,
            kwargs={"poll_seconds": 0.05},
            name="phase0-live-collector",
            daemon=True,
        )
        self.collector_thread.start()

    def collect_new(self) -> list[dict[str, Any]]:
        assert self.tail is not None
        new_records = self.tail.poll()
        for record in new_records:
            self.records.append(record)
            if record.get("event") == "server.pong" and record.get("session_id"):
                self.last_pong_at = time.monotonic()
                if self.session_id is None:
                    self.session_id = str(record["session_id"])
            if (
                record.get("session_id")
                and self.session_id is None
                and record.get("event") == "server.session.ready"
            ):
                self.session_id = str(record["session_id"])
            if not self.session_id or str(record.get("session_id")) != self.session_id:
                continue
            event = str(record.get("event", ""))
            response = str(record.get("response_id") or "")
            if event in TTS_START_EVENTS and response:
                self.playback_active.add(response)
            elif event in TERMINAL_TTS_EVENTS and response:
                self.playback_active.discard(response)
        return new_records

    def _check_run_health(self) -> None:
        now = time.monotonic()
        if now - self.health_checked_at >= 8:
            check_local_health("http://127.0.0.1:8000/health", "ok")
            check_local_health("http://127.0.0.1:8000/ready", "ready")
            self.health_checked_at = now
        if self.session_id is None:
            return
        timeout = self.settings.voice_heartbeat_timeout_seconds
        if self.last_pong_at is None or now - self.last_pong_at > timeout:
            raise BaselineAbort("Voice-session heartbeat was lost; laptop speech stopped.")

    def wait_for_session(self) -> None:
        deadline = time.monotonic() + max(50, self.settings.voice_heartbeat_timeout_seconds)
        started_session = False
        while time.monotonic() < deadline:
            self.collect_new()
            if self.session_id is not None and self.last_pong_at is not None:
                self.environment["voice_websocket"] = "connected"
                self.environment["heartbeat"] = "healthy"
                return
            if not started_session:
                control = self.read_voice_control()
                if control and control[0] == "Start voice session":
                    self.tap_voice_control(control)
                    started_session = True
            time.sleep(0.1)
        raise BaselineAbort("No healthy WebSocket session/heartbeat appeared after capture start.")

    def read_voice_control(self) -> tuple[str, tuple[int, int]] | None:
        adb = shutil.which(self.collector.adb_command)
        if adb is None:
            raise BaselineAbort("adb is not available on PATH.")
        # The assistant screen auto-scrolls to diagnostics after a response.
        # Bring its primary voice button into view before reading its label.
        run_command([adb, "shell", "input", "swipe", "540", "500", "540", "1900", "250"])
        run_command([adb, "shell", "uiautomator", "dump", "/sdcard/phase0_window.xml"])
        xml_text = run_command([adb, "shell", "cat", "/sdcard/phase0_window.xml"])
        return voice_control_from_xml(xml_text)

    def tap_voice_control(self, control: tuple[str, tuple[int, int]]) -> None:
        label, (x, y) = control
        if label not in {"Start voice session", "Start turn"}:
            raise BaselineAbort(f"Voice control is not idle ({label!r}); no prompt was spoken.")
        adb = shutil.which(self.collector.adb_command)
        if adb is None:
            raise BaselineAbort("adb is not available on PATH.")
        run_command([adb, "shell", "input", "tap", str(x), str(y)])

    async def ensure_input_turn(self) -> None:
        await self.wait_for_playback_idle()
        ready_events = {"server_turn_ready", "turn_ready_received"}
        completed_events = {"server.turn.completed", "turn_complete"}
        latest_ready: str | None = None
        latest_completed: set[str] = set()
        for record in self.records:
            if str(record.get("session_id")) != self.session_id:
                continue
            event = record.get("event")
            turn_id = str(record.get("turn_id") or "")
            if turn_id and event in ready_events:
                latest_ready = turn_id
            elif turn_id and event in completed_events:
                latest_completed.add(turn_id)
        if latest_ready and latest_ready not in latest_completed:
            if self.playback_active:
                raise BaselineAbort("Phone playback is active; refusing to start laptop speech.")
            return

        control = self.read_voice_control()
        if control is None or control[0] != "Start turn":
            label = control[0] if control else "unavailable"
            raise BaselineAbort(
                f"Phone is not ready for a new turn ({label}); no prompt was spoken."
            )
        start = len(self.records)
        self.tap_voice_control(control)
        deadline = time.monotonic() + float(self.manifest.get("event_timeout_seconds", 75))
        while time.monotonic() < deadline:
            new_records = self._new_records(start)
            self._guard_events(new_records, None)
            ready = self._find_event(new_records, ready_events)
            if ready is not None:
                if self.playback_active:
                    raise BaselineAbort("Phone playback began before the laptop prompt.")
                return
            await asyncio.sleep(0.1)
        raise BaselineAbort("Phone did not enter a listening turn; no prompt was spoken.")

    async def wait_for_playback_idle(self) -> None:
        deadline = time.monotonic() + float(self.manifest.get("event_timeout_seconds", 75))
        while self.playback_active and time.monotonic() < deadline:
            new_records = self._new_records(len(self.records))
            self._guard_events(new_records, None)
            await asyncio.sleep(0.1)
        if self.playback_active:
            raise BaselineAbort("Phone playback did not complete; laptop speech remained stopped.")

    def _new_records(self, start: int) -> list[dict[str, Any]]:
        self.collect_new()
        return self.records[start:]

    def _find_event(
        self,
        records: list[dict[str, Any]],
        event_names: set[str],
        *,
        turn_id: str | None = None,
        response_id: str | None = None,
    ) -> dict[str, Any] | None:
        for record in records:
            if record.get("event") not in event_names:
                continue
            if self.session_id and str(record.get("session_id")) != self.session_id:
                continue
            if turn_id is not None and str(record.get("turn_id")) != turn_id:
                continue
            if response_id is not None and str(record.get("response_id")) != response_id:
                continue
            return record
        return None

    def _guard_events(self, new_records: list[dict[str, Any]], current_turn_id: str | None) -> None:
        self._check_run_health()
        for record in new_records:
            event = str(record.get("event", ""))
            if self.session_id and str(record.get("session_id")) != self.session_id:
                continue
            if event.startswith("barge_in_"):
                metadata = event_metadata(record)
                if not is_active_barge_event(record):
                    self.lifecycle_diagnostics.append(
                        {
                            "event": event,
                            "reason": metadata.get("reason"),
                            "turn_id": record.get("turn_id"),
                        }
                    )
                    continue
                turn_id = str(record.get("turn_id") or "")
                self.anomalous_turns.setdefault(
                    turn_id or f"unknown-{len(self.anomalous_turns) + 1}",
                    {
                        "turn_id": turn_id or None,
                        "response_id": record.get("response_id"),
                        "event": event,
                        "reason": metadata.get("reason"),
                        "timestamp": record.get("timestamp"),
                    },
                )
                if not current_turn_id or turn_id == current_turn_id:
                    raise BaselineAbort(
                        "Unexpected device barge-in; current turn excluded and speech stopped."
                    )
            if event in {"server.error", "server.session.ended", "voice.session.closed"}:
                raise BaselineAbort(
                    "WebSocket reported an error or disconnected; laptop speech stopped."
                )
            if (
                current_turn_id
                and record.get("turn_id")
                and str(record["turn_id"]) != current_turn_id
            ):
                if event in {
                    "turn_started",
                    "turn_ready_received",
                    "barge_in_replacement_turn_ready",
                }:
                    raise BaselineAbort(
                        "Unexpected replacement turn appeared; laptop speech stopped."
                    )

    async def await_turn(self, start: int, *, expected_prompt: dict[str, Any]) -> dict[str, Any]:
        # wait_for_turn uses the same async DB loop for transcript reads.
        deadline = time.monotonic() + float(self.manifest.get("event_timeout_seconds", 75))
        turn_id: str | None = None
        transcript: str | None = None
        similarity = 0.0
        pcm_observed = False
        while time.monotonic() < deadline:
            new_records = self._new_records(start)
            self._guard_events(new_records, turn_id)
            stt = self._find_event(new_records, {"stt_final"})
            if stt is not None and turn_id is None:
                turn_id = str(stt["turn_id"])
            if turn_id is not None:
                pcm_observed = (
                    pcm_observed
                    or self._find_event(
                        self.records[start:], {"first_pcm_received"}, turn_id=turn_id
                    )
                    is not None
                )
                if transcript is None:
                    transcript = await self.db.transcript_for_turn(turn_id, self.session_id or "")
                    if transcript is not None:
                        matched, similarity = transcript_match(
                            expected_prompt["text"],
                            transcript,
                            expected_prompt.get("must_contain", []),
                        )
                        if not matched:
                            raise BaselineAbort(
                                "STT transcript did not sufficiently match case "
                                f"{expected_prompt['case_id']} (similarity={similarity:.3f}); "
                                "no next prompt was spoken."
                            )
                terminal = self._find_event(
                    self.records[start:], {"server.turn.completed"}, turn_id=turn_id
                )
                if terminal is not None:
                    response_id = str(terminal.get("response_id") or "")
                    playback = self._find_event(
                        self.records[start:],
                        TERMINAL_TTS_EVENTS,
                        turn_id=turn_id,
                        response_id=response_id or None,
                    )
                    if playback is not None:
                        if not pcm_observed:
                            raise BaselineAbort(f"No microphone PCM was observed for {turn_id}.")
                        await asyncio.sleep(float(self.manifest.get("quiet_settle_seconds", 0.9)))
                        self.collect_new()
                        if self.playback_active:
                            raise BaselineAbort(
                                "Phone playback remained active after its terminal event."
                            )
                        return {
                            "turn_id": turn_id,
                            "response_id": response_id,
                            "transcript": transcript,
                            "transcript_similarity": similarity,
                            "transcript_matched": True,
                            "completion_status": "server_turn_and_phone_playback_complete",
                            "pcm_observed": pcm_observed,
                            "turn_event": terminal,
                        }
            await asyncio.sleep(0.1)
        raise BaselineAbort(
            f"No complete STT/assistant/playback lifecycle for {expected_prompt['case_id']}."
        )

    async def speak_case(self, case: dict[str, Any]) -> dict[str, Any]:
        if self.anomalous_turns:
            self.append_anomaly_rows()
            raise BaselineAbort("Unexpected barge-in was recorded; no next prompt was spoken.")
        self.collect_new()
        self._check_run_health()
        await self.wait_for_playback_idle()
        await self.ensure_input_turn()
        start = len(self.records)
        print(f"Speaking {case['case_id']} ({case['category']})", flush=True)
        self.attempted_prompt_count += 1
        try:
            speak(case["text"])
            return await self.await_turn(start, expected_prompt=case)
        except BaselineAbort as error:
            self.collect_new()
            self.append_anomaly_rows()
            self.append_incomplete_case(case, self.records[start:], str(error))
            raise

    def append_incomplete_case(
        self, case: dict[str, Any], records: list[dict[str, Any]], reason: str
    ) -> None:
        turn_ids = list(
            dict.fromkeys(str(record["turn_id"]) for record in records if record.get("turn_id"))
        )
        if not turn_ids:
            turn_ids = [""]
        for index, turn_id in enumerate(turn_ids):
            if turn_id and any(row.get("turn_id") == turn_id for row in self.rows):
                continue
            turn_records = [
                record for record in records if str(record.get("turn_id") or "") == turn_id
            ]
            accounting = accounting_for_turn(turn_records)
            self.append_row(
                {
                    "case_id": case["case_id"]
                    if index == 0
                    else f"{case['case_id']}-EXTRA-{index}",
                    "spoken_laptop_text": case["text"],
                    "session_id": self.session_id,
                    "turn_id": turn_id or None,
                    "response_id": next(
                        (
                            record.get("response_id")
                            for record in turn_records
                            if record.get("response_id")
                        ),
                        None,
                    ),
                    "legacy_route_category": case["category"],
                    "completion_status": "excluded_incomplete_turn",
                    "exclusion_reason": reason,
                    "main_model_calls": accounting["main_model_calls"],
                    "model_rounds": accounting["model_rounds"],
                    "providers": accounting["providers"],
                    "models": accounting["models"],
                    "input_tokens": accounting["input_tokens"],
                    "output_tokens": accounting["output_tokens"],
                    "token_usage_status": accounting["token_usage_status"],
                    "embedding_calls": accounting["embedding_calls"],
                    "vector_retrieval_calls": accounting["vector_retrieval_calls"],
                    "fts_calls": accounting["fts_calls"],
                    "rrf_executions": accounting["rrf_executions"],
                    "reranker_calls": accounting["reranker_calls"],
                    "memory_context_pipeline_calls": accounting["memory_context_pipeline_calls"],
                    "tool_calls": accounting["tool_invocations"],
                    "write_attempts": accounting["write_attempts"],
                    "confirmed_writes": accounting["confirmed_writes"],
                    "unconfirmed_writes": max(
                        0, accounting["write_attempts"] - accounting["confirmed_writes"]
                    ),
                    "duplicate_writes_prevented": accounting["duplicate_write_prevented"],
                    "metrics": metric_records(turn_records),
                    "correlation_errors": correlation_errors(turn_records),
                }
            )

    def append_anomaly_rows(self) -> None:
        for index, (turn_id, anomaly) in enumerate(self.anomalous_turns.items(), start=1):
            if anomaly.get("row_written"):
                continue
            records = (
                accepted_turn_records(self.collector, turn_id) if anomaly.get("turn_id") else []
            )
            row = self.row_for_turn(
                case_id=f"P0-ANOMALY-{index:03d}",
                category="unexpected_barge_in",
                text="",
                completed={
                    "turn_id": anomaly.get("turn_id"),
                    "response_id": anomaly.get("response_id"),
                    "transcript": "[omitted: anomalous audio may contain assistant playback]",
                    "transcript_matched": False,
                    "transcript_similarity": None,
                    "completion_status": "excluded_unexpected_barge_in",
                },
                turn_records=records,
            )
            row["exclusion_reason"] = (
                f"{anomaly['event']}: {anomaly.get('reason') or 'unexpected device audio event'}"
            )
            row["barge_in_event"] = anomaly["event"]
            self.append_row(row)
            anomaly["row_written"] = True

    def row_for_turn(
        self,
        *,
        case_id: str,
        category: str,
        text: str,
        completed: dict[str, Any],
        turn_records: list[dict[str, Any]],
        write_attempts_override: int | None = None,
        confirmed_writes_override: int | None = None,
    ) -> dict[str, Any]:
        accounting = accounting_for_turn(turn_records)
        if write_attempts_override is not None:
            accounting["write_attempts"] = write_attempts_override
        if confirmed_writes_override is not None:
            accounting["confirmed_writes"] = confirmed_writes_override
        return {
            "case_id": case_id,
            "spoken_laptop_text": text,
            "stt_final_transcript": completed["transcript"],
            "transcript_matched_intent": completed["transcript_matched"],
            "transcript_similarity": completed["transcript_similarity"],
            "session_id": self.session_id,
            "turn_id": completed["turn_id"],
            "response_id": completed["response_id"],
            "legacy_route_category": category,
            "tool_names": [
                event_metadata(record).get("tool_name")
                for record in turn_records
                if record.get("event") in {"tool_start", "tool_write_attempt"}
                and event_metadata(record).get("tool_name")
            ],
            "completion_status": completed["completion_status"],
            "microphone_pcm_observed": completed.get("pcm_observed", False),
            "exclusion_reason": None,
            "main_model_calls": accounting["main_model_calls"],
            "model_rounds": accounting["model_rounds"],
            "providers": accounting["providers"],
            "models": accounting["models"],
            "input_tokens": accounting["input_tokens"],
            "output_tokens": accounting["output_tokens"],
            "total_tokens": accounting["total_tokens"],
            "token_usage_status": accounting["token_usage_status"],
            "embedding_calls": accounting["embedding_calls"],
            "vector_retrieval_calls": accounting["vector_retrieval_calls"],
            "fts_calls": accounting["fts_calls"],
            "rrf_executions": accounting["rrf_executions"],
            "reranker_calls": accounting["reranker_calls"],
            "memory_context_pipeline_calls": accounting["memory_context_pipeline_calls"],
            "tool_calls": accounting["tool_invocations"],
            "write_attempts": accounting["write_attempts"],
            "unconfirmed_writes": max(
                0, accounting["write_attempts"] - accounting["confirmed_writes"]
            ),
            "confirmed_writes": accounting["confirmed_writes"],
            "duplicate_writes_prevented": accounting["duplicate_write_prevented"],
            "metrics": metric_records(turn_records),
            "correlation_errors": correlation_errors(turn_records),
        }

    def append_row(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        with self.row_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    async def wait_for_pending(
        self,
        *,
        request_turn_id: str,
        expected_tool: str,
        expected_title_marker: str | None,
        expected_reminder_id: str | None,
        expected_task_id: str | None = None,
    ):
        deadline = time.monotonic() + min(
            self.settings.voice_confirmation_ttl_seconds,
            float(self.manifest.get("event_timeout_seconds", 75)),
        )
        while time.monotonic() < deadline:
            self.collect_new()
            self._check_run_health()
            pending = await self.db.pending_confirmation(self.session_id or "")
            if pending is not None and pending.status == "PENDING":
                arguments = pending.validated_tool_arguments
                valid = (
                    pending.original_turn_id == uuid.UUID(request_turn_id)
                    and pending.tool_name == expected_tool
                    and pending.session_id == uuid.UUID(self.session_id or "")
                    and not pending.is_expired()
                )
                if expected_title_marker is not None:
                    valid = (
                        valid
                        and expected_title_marker.casefold()
                        in str(arguments.get("title", "")).casefold()
                    )
                if expected_reminder_id is not None:
                    valid = valid and str(arguments.get("reminder_id")) == expected_reminder_id
                if expected_task_id is not None:
                    valid = valid and str(arguments.get("task_id")) == expected_task_id
                return pending, bool(valid)
            await asyncio.sleep(0.25)
        return None, False

    async def reject_owned_pending(self, pending, start_records: int) -> None:
        """Decline a malformed proposal only when it belongs to this scripted request."""

        if (
            pending is None
            or pending.status != "PENDING"
            or pending.session_id != uuid.UUID(self.session_id or "")
            or pending.original_turn_id
            not in {
                uuid.UUID(str(record["turn_id"]))
                for record in self.records[start_records:]
                if record.get("turn_id")
            }
        ):
            return
        rejection = {
            "case_id": "P0-SAFETY-DECLINE",
            "category": "safe_decline",
            "text": "No",
            "must_contain": ["no"],
        }
        start = len(self.records)
        speak("No")
        await self.await_turn(start, expected_prompt=rejection)

    async def run_confirmation_case(self, case: dict[str, Any]) -> None:
        request_started_at = len(self.records)
        created = await self.speak_case(case)
        request_records = accepted_turn_records(self.collector, created["turn_id"])
        confirmation = case["confirmation"]
        expected_reminder_id: str | None = None
        expected_task_id: str | None = None
        if confirmation.get("target_from_case"):
            marker = "Phase Zero test cleanup"
            rows = await self.db.tasks_for_marker(self.session_id or "", marker)
            active = [row for row in rows if row.status in {"pending", "in_progress"}]
            if len(active) != 1:
                raise BaselineAbort("Expected exactly one active disposable task before cleanup.")
            expected_task_id = str(active[0].id)
        pending, valid = await self.wait_for_pending(
            request_turn_id=created["turn_id"],
            expected_tool=confirmation["tool_name"],
            expected_title_marker=confirmation.get("title_marker"),
            expected_reminder_id=expected_reminder_id,
            expected_task_id=expected_task_id,
        )
        if not valid:
            await self.reject_owned_pending(pending, request_started_at)
            raise BaselineAbort(
                "The authenticated pending action did not match this case. "
                "The driver did not speak Yes."
            )

        confirm_case = {
            "case_id": case["case_id"] + "-CONFIRM",
            "category": "confirmation_resolution",
            "text": confirmation["text"],
            "must_contain": ["yes"],
        }
        confirmed = await self.speak_case(confirm_case)
        owner = await self.db.session_owner(self.session_id or "")
        if owner is None:
            raise BaselineAbort(
                "The authenticated voice-session owner disappeared during confirmation."
            )
        if confirmation["tool_name"] == "create_task":
            marker = confirmation["title_marker"]
            rows = await self.db.tasks_for_marker(self.session_id or "", marker)
            active = [row for row in rows if row.status in {"pending", "in_progress"}]
            if len(active) != 1:
                raise BaselineAbort(
                    "Confirmed task write was not exactly one active matching test item."
                )
            created_id = str(active[0].id)
            self.environment["test_task_id"] = created_id
        else:
            marker = "Phase Zero test cleanup"
            rows = await self.db.tasks_for_marker(self.session_id or "", marker)
            if len(rows) != 1 or rows[0].status != "completed":
                raise BaselineAbort(
                    "Normal product cleanup did not complete exactly the disposable "
                    "Phase Zero task."
                )

        preapproval_write_events = [
            record
            for record in request_records
            if record.get("event") in {"tool_write_attempt", "tool_write_committed"}
            and event_metadata(record).get("tool_name") == confirmation["tool_name"]
        ]
        if preapproval_write_events:
            raise BaselineAbort("A write attempt was observed before the authenticated Yes turn.")
        confirm_records = accepted_turn_records(self.collector, confirmed["turn_id"])
        resolved_request_records = accepted_turn_records(self.collector, created["turn_id"])
        resolved_records = [*resolved_request_records, *confirm_records]
        write_events = [
            record
            for record in resolved_records
            if record.get("event") == "tool_write_committed"
            and event_metadata(record).get("tool_name") == confirmation["tool_name"]
        ]
        duplicate_events = [
            record
            for record in resolved_records
            if record.get("event") == "tool_duplicate_write_prevented"
            and event_metadata(record).get("tool_name") == confirmation["tool_name"]
        ]
        if len(write_events) != 1:
            raise BaselineAbort("Expected exactly one confirmed write commit in gateway telemetry.")

        request_row = self.row_for_turn(
            case_id=case["case_id"],
            category=case["category"],
            text=case["text"],
            completed=created,
            turn_records=request_records,
        )
        request_row["confirmation_status"] = "APPROVED"
        request_row["confirmation_id"] = str(pending.confirmation_id)
        request_row["idempotency_key_present"] = bool(pending.idempotency_key)
        request_row["duplicate_writes_prevented"] = len(duplicate_events)
        request_row["write_commits"] = len(write_events)
        request_row["pending_confirmation_verified_before_speech"] = True
        request_row["unconfirmed_writes"] = 0
        request_row["cleanup_target_id"] = expected_task_id
        request_row["cleanup_verified"] = confirmation["tool_name"] == "complete_task"
        # The pre-approval turn is the measured route; write-safety counts are
        # attributed to this action across its authenticated Yes turn.
        request_row["write_attempts"] = sum(
            1 for record in resolved_records if record.get("event") == "tool_write_attempt"
        )
        request_row["confirmed_writes"] = len(write_events)
        self.append_row(request_row)
        confirm_row = self.row_for_turn(
            case_id=confirm_case["case_id"],
            category=confirm_case["category"],
            text=confirm_case["text"],
            completed=confirmed,
            turn_records=confirm_records,
        )
        confirm_row["confirmed_action_case_id"] = case["case_id"]
        confirm_row["pending_confirmation_verified_before_speech"] = True
        self.append_row(confirm_row)

    async def run_cases(self) -> None:
        marker = "Phase Zero test cleanup"
        # Refuse to collide with an existing test item; never inspect or delete
        # unrelated reminders.
        self.collect_new()
        if self.session_id is None:
            raise BaselineAbort("No active session was selected before the acoustic run.")
        preexisting = await self.db.reminders_for_marker(self.session_id, marker)
        preexisting_tasks = await self.db.tasks_for_marker(self.session_id, marker)
        if preexisting or preexisting_tasks:
            raise BaselineAbort(
                "A Phase Zero marker reminder already exists; refusing to create another."
            )
        for case in self.manifest["cases"]:
            if case.get("confirmation"):
                await self.run_confirmation_case(case)
                continue
            completed = await self.speak_case(case)
            turn_records = accepted_turn_records(self.collector, completed["turn_id"])
            self.append_row(
                self.row_for_turn(
                    case_id=case["case_id"],
                    category=case["category"],
                    text=case["text"],
                    completed=completed,
                    turn_records=turn_records,
                )
            )

    def write_summary(self, *, outcome: str, failure: str | None) -> None:
        rows = self.rows
        valid = [
            row
            for row in rows
            if row.get("completion_status") == "server_turn_and_phone_playback_complete"
        ]
        turn_ids = {str(row.get("turn_id")) for row in valid}
        model_calls = sum(row.get("main_model_calls", 0) for row in valid)
        rounds = sum(row.get("model_rounds", 0) for row in valid)
        metric_keys = (
            "speech_end_to_stt_final",
            "stt_request_duration",
            "stt_final_to_orchestration",
            "memory_context_pipeline",
            "llm_ttft",
            "llm_total",
            "speech_end_to_first_token",
            "tts_ttfa",
            "tts_generation_total",
            "speech_end_to_first_audio",
            "end_to_end",
            "backend_speech_end_to_first_text",
            "backend_speech_end_to_first_audio",
            "backend_complete_turn",
        )
        stats: dict[str, dict[str, Any]] = {}
        for key in metric_keys:
            field_values = []
            for row in valid:
                item = row.get("metrics", {}).get(key)
                if item and isinstance(item.get("value_ms"), (int, float)):
                    field_values.append(float(item["value_ms"]))
                if key in {"stt_final_to_orchestration", "server_commit_to_stt_final"}:
                    extra = next(
                        (
                            metric
                            for metric in row.get("metrics", {}).values()
                            if metric.get("name")
                            in {"Post-STT orchestration", "Backend commit -> STT final"}
                            and metric.get("name")
                            == (
                                "Post-STT orchestration"
                                if key == "stt_final_to_orchestration"
                                else "Backend commit -> STT final"
                            )
                        ),
                        None,
                    )
                    if extra and isinstance(extra.get("value_ms"), (int, float)):
                        field_values.append(float(extra["value_ms"]))
            ordered = sorted(field_values)
            stats[key] = {
                "count": len(ordered),
                "min_ms": min(ordered) if ordered else None,
                "mean_ms": round(sum(ordered) / len(ordered), 3) if ordered else None,
                "p50_ms": round(percentile(ordered, 0.50), 3) if ordered else None,
                "p95_ms": round(percentile(ordered, 0.95), 3) if ordered else None,
                "max_ms": max(ordered) if ordered else None,
            }
        category_totals: dict[str, dict[str, Any]] = {}
        for row in valid:
            category = row["legacy_route_category"]
            total = category_totals.setdefault(
                category,
                {
                    "turns": 0,
                    "main_model_calls": 0,
                    "model_rounds": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "embedding_calls": 0,
                    "vector_retrieval_calls": 0,
                    "fts_calls": 0,
                    "rrf_executions": 0,
                    "reranker_calls": 0,
                    "retrieval_calls": 0,
                    "memory_context_pipeline_calls": 0,
                    "tool_calls": 0,
                    "confirmation_requests": 0,
                    "write_attempts": 0,
                    "confirmed_writes": 0,
                    "duplicate_writes_prevented": 0,
                    "token_usage_complete": True,
                },
            )
            total["turns"] += 1
            for field in (
                "main_model_calls",
                "model_rounds",
                "embedding_calls",
                "vector_retrieval_calls",
                "fts_calls",
                "rrf_executions",
                "reranker_calls",
                "memory_context_pipeline_calls",
                "tool_calls",
                "confirmation_requests",
                "write_attempts",
                "confirmed_writes",
                "duplicate_writes_prevented",
            ):
                total[field] += int(row.get(field) or 0)
            total["retrieval_calls"] += int(row.get("memory_context_pipeline_calls") or 0)
            if row.get("input_tokens") is None or row.get("output_tokens") is None:
                total["token_usage_complete"] = False
            else:
                total["input_tokens"] += int(row["input_tokens"])
                total["output_tokens"] += int(row["output_tokens"])
        for total in category_totals.values():
            turns = total["turns"]
            for field in (
                "main_model_calls",
                "model_rounds",
                "input_tokens",
                "output_tokens",
                "embedding_calls",
                "vector_retrieval_calls",
                "fts_calls",
                "rrf_executions",
                "reranker_calls",
                "retrieval_calls",
                "memory_context_pipeline_calls",
                "tool_calls",
                "confirmation_requests",
                "write_attempts",
                "confirmed_writes",
                "duplicate_writes_prevented",
            ):
                total[field + "_per_100"] = round(total[field] * 100 / turns, 2) if turns else None
            if not total["token_usage_complete"]:
                total["input_tokens"] = None
                total["output_tokens"] = None
                total["input_tokens_per_100"] = None
                total["output_tokens_per_100"] = None

        observed_router_calls = sum(
            1
            for record in self.records
            if str(record.get("event", "")).startswith("router.")
            or str(record.get("component", "")).casefold() == "router"
        )
        summary = {
            "version": "1.0.0",
            "run_id": self.run_id,
            "outcome": outcome,
            "failure": failure,
            "created_at": datetime.now(UTC).isoformat(),
            "environment": self.environment,
            "disposable_account_user_confirmed": self.args.disposable_account_confirmed,
            "router_mode": self.settings.router_mode,
            "router_cohort_percent": self.settings.router_cohort_percent,
            "gateway_path": "legacy orchestration",
            "langgraph_router_calls": observed_router_calls,
            "attempted_scripted_prompts": self.attempted_prompt_count,
            "valid_completed_physical_turns": len(valid),
            "excluded_turns": sum(
                str(row.get("completion_status", "")).startswith("excluded_") for row in rows
            ),
            "lifecycle_diagnostic_count": len(self.lifecycle_diagnostics),
            "lifecycle_anomaly_count": sum(
                1
                for row in self.rows
                if row.get("completion_status") == "excluded_unexpected_barge_in"
            ),
            "rows": self.rows,
            "turn_ids": sorted(turn_ids),
            "model_calls": model_calls,
            "model_rounds": rounds,
            "metric_statistics": stats,
            "route_totals": category_totals,
            "rows_path": str(self.row_path.relative_to(ROOT)),
            "event_artifact_path": str(self.event_path.relative_to(ROOT)),
        }
        self.summary_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.summary_md.write_text(self.summary_markdown(summary), encoding="utf-8")

    def summary_markdown(self, summary: dict[str, Any]) -> str:
        pcm_turns = sum(bool(row.get("microphone_pcm_observed")) for row in self.rows)
        lines = [
            f"# Automated physical Phase 0 baseline {self.run_id}",
            "",
            f"**Outcome:** {summary['outcome']}",
            f"**Phase 0 gate:** {'PASS' if self.phase0_gate(summary) else 'NOT PASSED'}",
            f"**Failure / stop reason:** {summary['failure'] or 'None'}",
            "",
            "## Physical setup",
            "",
            f"- Target device: {self.args.device_model} ({self.args.device_serial})",
            "- Backend `/health`: "
            f"{summary['environment'].get('health', {}).get('status', 'not verified')}; "
            f"`/ready`: {summary['environment'].get('ready', {}).get('status', 'not verified')}.",
            "- TTS readiness: "
            + summary["environment"]
            .get("ready", {})
            .get("dependencies", {})
            .get("tts", {})
            .get("status", "not verified"),
            "- ADB device/reverse: "
            + (
                "verified; backend 8000 and Metro 8081 mappings active."
                if summary["environment"].get("device")
                else "not verified."
            ),
            f"- Router: `{self.settings.router_mode}`; "
            f"cohort `{self.settings.router_cohort_percent}`.",
            "- Gateway: legacy orchestration; LangGraph router calls = 0 required.",
            "- Laptop speech: "
            + summary["environment"].get("speech", "not verified; no prompts were spoken."),
            "- Voice WebSocket: "
            + summary["environment"].get("voice_websocket", "not verified")
            + "; heartbeat: "
            + summary["environment"].get("heartbeat", "not verified")
            + f"; turns with observed PCM: {pcm_turns}.",
            "",
            "## Automated acoustic sample",
            "",
            f"- Scripted prompts attempted: {summary['attempted_scripted_prompts']}",
            f"- Valid completed turns: {summary['valid_completed_physical_turns']}",
            f"- Excluded anomalous turns: {summary['excluded_turns']}",
            f"- Non-active barge-in lifecycle diagnostics: {summary['lifecycle_diagnostic_count']}",
            "- Confirmed write commits: "
            f"{sum(row.get('confirmed_writes', 0) for row in self.rows)}",
            "- Duplicate writes prevented: "
            f"{sum(row.get('duplicate_writes_prevented', 0) for row in self.rows)}; "
            "row state is verified in run notes.",
            "",
            "## Valid-clock statistics",
            "",
            "| Metric | Count | Mean | P50 | P95 | Max |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        metric_names = {
            "speech_end_to_stt_final": "Speech-end → STT final",
            "stt_request_duration": "STT request",
            "stt_final_to_orchestration": "Post-STT orchestration",
            "memory_context_pipeline": "Memory-context pipeline",
            "llm_ttft": "LLM TTFT",
            "llm_total": "LLM total",
            "speech_end_to_first_token": "Speech-end → first text (device clock)",
            "tts_ttfa": "TTS provider TTFA",
            "tts_generation_total": "TTS generation total",
            "speech_end_to_first_audio": "Speech-end → first audio (device clock)",
            "end_to_end": "Speech-end → phone playback complete",
            "backend_speech_end_to_first_text": "Backend speech-end → first text",
            "backend_speech_end_to_first_audio": "Backend speech-end → first TTS audio",
            "backend_complete_turn": "Backend speech-end → turn complete",
        }
        for key, label in metric_names.items():
            value = summary["metric_statistics"][key]

            def fmt(name: str, metric: dict[str, Any] = value) -> str:
                item = metric.get(name)
                return "n/a" if item is None else f"{item:.1f} ms"

            lines.append(
                f"| {label} | {value['count']} | {fmt('mean_ms')} | {fmt('p50_ms')} | "
                f"{fmt('p95_ms')} | {fmt('max_ms')} |"
            )
        playback_stats = summary["metric_statistics"]["backend_complete_turn"]
        lines.extend(
            [
                "",
                "## Route/category calls per 100 valid turns",
                "",
                "| Category | Turns | Model calls | Rounds | Input tokens | Output tokens | "
                "Embeddings | Vector | FTS | RRF | Reranker | Retrieval pipeline | Tools | "
                "Confirmations | Write attempts | Confirmed | Duplicates |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for category, values in summary["route_totals"].items():

            def rate(key: str, totals: dict[str, Any] = values) -> str:
                item = totals.get(key + "_per_100")
                return "n/a" if item is None else f"{item:.1f}"

            lines.append(
                f"| {category} | {values['turns']} | {rate('main_model_calls')} | "
                f"{rate('model_rounds')} | {rate('input_tokens')} | {rate('output_tokens')} | "
                f"{rate('embedding_calls')} | {rate('vector_retrieval_calls')} | "
                f"{rate('fts_calls')} | {rate('rrf_executions')} | {rate('reranker_calls')} | "
                f"{rate('memory_context_pipeline_calls')} | {rate('tool_calls')} | "
                f"{rate('confirmation_requests')} | {rate('write_attempts')} | "
                f"{rate('confirmed_writes')} | {rate('duplicate_writes_prevented')} |"
            )
        lines.extend(
            [
                "",
                "## Rollout budgets",
                "",
                "- Deterministic route decision p95 <= 50 ms: router is off; "
                "deferred to Phase 3 shadow comparison.",
                "- Added speech-end to first-text/audio p95 <= 100 ms: baseline measured; "
                "router overhead comparison is deferred to Phase 3 shadow.",
                f"- Complete-turn P50 regression <= 2%: legacy P50 "
                f"{playback_stats['p50_ms']} ms; "
                "future ceiling is 1.02x this value.",
                f"- Complete-turn P95 regression <= 5%: legacy P95 "
                f"{playback_stats['p95_ms']} ms; "
                "future ceiling is 1.05x this value.",
                "- Extra model/retrieval calls: legacy values are recorded per category; "
                "comparison is deferred until shadow.",
                "- Safety misroutes, unauthorized confirmation, duplicate writes, and "
                "cross-user access: required observed count is zero.",
                "",
                "## Artifacts",
                "",
                f"- Per-turn report: `{summary['rows_path']}`",
                f"- Privacy-safe raw event capture: `{summary['event_artifact_path']}`",
                f"- Machine summary: `{self.summary_json.relative_to(ROOT)}`",
                "",
                "Transcript fields contain only harmless scripted prompts and corresponding "
                "final STT results. Assistant answers, credentials, and personal memory content "
                "are omitted.",
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def phase0_gate(summary: dict[str, Any]) -> bool:
        rows = summary.get("rows", [])
        categories = {row.get("legacy_route_category") for row in rows}
        metrics = summary.get("metric_statistics", {})
        request_rows = [row for row in rows if row.get("case_id") == "P0-LIVE-013"]
        cleanup_rows = [row for row in rows if row.get("case_id") == "P0-LIVE-016"]
        route_totals = summary.get("route_totals", {})
        required_metrics = (
            "backend_speech_end_to_first_text",
            "backend_speech_end_to_first_audio",
            "backend_complete_turn",
        )
        required_categories = {
            "general_llm",
            "current_time",
            "current_date",
            "structured_task_read",
            "structured_reminder_read",
            "memory_query",
            "memory_query_missing",
            "confirmed_task_write",
            "confirmed_task_cleanup",
        }
        return bool(
            summary.get("outcome") == "complete"
            and summary.get("valid_completed_physical_turns", 0) >= 20
            and summary.get("langgraph_router_calls") == 0
            and summary.get("router_mode") == "off"
            and summary.get("router_cohort_percent") == 0
            and summary.get("excluded_turns", 0) == 0
            and summary.get("lifecycle_anomaly_count", 0) == 0
            and all(metrics.get(key, {}).get("count", 0) >= 20 for key in required_metrics)
            and required_categories.issubset(categories)
            and len(request_rows) == 1
            and request_rows[0].get("pending_confirmation_verified_before_speech") is True
            and request_rows[0].get("write_attempts") == 1
            and request_rows[0].get("confirmed_writes") == 1
            and request_rows[0].get("unconfirmed_writes", 0) == 0
            and request_rows[0].get("duplicate_writes_prevented", 0) == 0
            and request_rows[0].get("write_commits") == 1
            and len(cleanup_rows) == 1
            and cleanup_rows[0].get("cleanup_verified") is True
            and cleanup_rows[0].get("pending_confirmation_verified_before_speech") is True
            and cleanup_rows[0].get("write_attempts") == 1
            and cleanup_rows[0].get("confirmed_writes") == 1
            and cleanup_rows[0].get("unconfirmed_writes", 0) == 0
            and cleanup_rows[0].get("duplicate_writes_prevented", 0) == 0
            and cleanup_rows[0].get("write_commits") == 1
            and sum(int(row.get("write_attempts", 0)) for row in rows) == 2
            and sum(int(row.get("confirmed_writes", 0)) for row in rows) == 2
            and sum(int(row.get("unconfirmed_writes", 0)) for row in rows) == 0
            and sum(int(row.get("duplicate_writes_prevented", 0)) for row in rows) == 0
            and all(
                total.get("duplicate_writes_prevented", 0) == 0 for total in route_totals.values()
            )
        )


async def async_main(args: argparse.Namespace) -> int:
    baseline = PhysicalBaseline(args)
    failure: str | None = None
    outcome = "aborted"
    try:
        baseline.preflight()
        await baseline.db.ping()
        baseline.start_capture()
        baseline.wait_for_session()
        await baseline.run_cases()
        outcome = "complete"
        return_code = 0
    except KeyboardInterrupt:
        failure = "Stopped by operator; partial capture retained."
        return_code = 130
    except BaselineAbort as error:
        failure = str(error)
        return_code = 1
    except Exception as error:  # noqa: BLE001 - preserve the partial capture on any harness fault
        failure = (
            f"Harness error ({type(error).__name__}); speech stopped and partial capture retained."
        )
        return_code = 1
    finally:
        if baseline.tail is not None:
            baseline.collect_new()
            baseline.append_anomaly_rows()
        await baseline.close()
        try:
            baseline.write_summary(outcome=outcome, failure=failure)
        except Exception as error:  # noqa: BLE001
            print(f"Summary generation failed ({type(error).__name__}).", flush=True)
        print(f"Physical event capture: {baseline.event_path}", flush=True)
        print(f"Per-turn rows: {baseline.row_path}", flush=True)
        print(f"Summary: {baseline.summary_md}", flush=True)
        if failure:
            print(f"Stopped: {failure}", flush=True)
    return return_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("phase0_live_manifest.json")
    )
    parser.add_argument("--device-serial", default="9b0ea196")
    parser.add_argument("--device-model", default="CPH2527")
    parser.add_argument("--run-id")
    parser.add_argument("--disposable-account-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.manifest.is_absolute():
        args.manifest = (ROOT / args.manifest).resolve()
    try:
        code = asyncio.run(async_main(args))
    except BaselineAbort as error:
        print(f"Preflight blocked: {error}", flush=True)
        raise SystemExit(1) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
