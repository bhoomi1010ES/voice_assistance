#!/usr/bin/env python3
"""Collect and report latency traces continuously during a live voice session.

The collector is intentionally external to the application. It follows the
backend JSONL trace, streams Android logcat, optionally follows a Metro log,
deduplicates correlated records, and prints stage durations as their end event
arrives. Stop it with Ctrl+C; use ``--once`` to stop after the first backend
turn completion.

Example from the repository root::

    python backend/scripts/live_latency.py --clear-logcat

If Metro is redirected to a file, include it with ``--metro-log``. The live
output defaults to ``logs/live_latency_trace.jsonl`` so the backend's source
trace remains append-only and historical runs are not overwritten.
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from scripts.analyze_latency import METRICS, correlation_errors, metrics_for_turn
    from scripts.merge_latency_trace import TRACE_PATTERN
except ModuleNotFoundError:  # Direct ``python backend/scripts/live_latency.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.analyze_latency import METRICS, correlation_errors, metrics_for_turn
    from scripts.merge_latency_trace import TRACE_PATTERN


CompletionEvents = frozenset({"turn_complete", "server.turn.completed", "tts_playback_complete"})
_ANDROID_LOGCAT_TIME = re.compile(r"\d{2}-\d{2}_\d{2}:\d{2}:\d{2}\.\d{3}")

_STAGES: tuple[tuple[str, str, str], ...] = (
    ("speech", "speech_start", "speech_end"),
    ("STT", "stt_request_start", "stt_final"),
    ("embedding", "embedding_start", "embedding_end"),
    ("vector_search", "vector_search_start", "vector_search_end"),
    ("FTS", "fts_start", "fts_end"),
    ("RRF", "rrf_start", "rrf_end"),
    ("rerank", "rerank_start", "rerank_end"),
    (
        "Memory context pipeline",
        "memory_context_pipeline_started",
        "memory_context_pipeline_completed",
    ),
    ("LLM TTFT", "llm_request_started", "llm_first_token_received"),
    ("LLM total", "llm_request_started", "llm_request_completed"),
    ("TTS TTFA", "tts_request_started", "tts_first_audio_received"),
    ("playback", "tts_playback_started", "tts_playback_completed"),
)


def record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Return the same identity used by the offline merge utility."""

    return (
        record.get("clock_domain"),
        record.get("event"),
        record.get("session_id"),
        record.get("turn_id"),
        record.get("response_id"),
        record.get("timestamp_ms"),
        record.get("monotonic_ms"),
        record.get("monotonic_ns"),
    )


def parse_log_line(line: str) -> dict[str, Any] | None:
    """Extract one LATENCY_TRACE JSON object from a logcat/Metro line."""

    match = TRACE_PATTERN.search(line)
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and value.get("event") else None


def build_logcat_command(adb_command: str, *, start_at_now: bool) -> list[str]:
    """Build a logcat command that does not replay the ring buffer by default."""

    command = [adb_command, "logcat", "-b", "all"]
    if start_at_now:
        device_time = subprocess.run(
            [adb_command, "shell", "date", "+%m-%d_%H:%M:%S.%3N"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
        if not _ANDROID_LOGCAT_TIME.fullmatch(device_time):
            raise RuntimeError(f"Unexpected Android date for logcat start: {device_time!r}")
        command.extend(["-T", device_time.replace("_", " ")])
    command.extend(["-v", "threadtime"])
    return command


class FileTail:
    """Non-blocking tail of a growing UTF-8 file, including truncation recovery."""

    def __init__(self, path: Path, *, from_end: bool = True) -> None:
        self.path = path
        self.offset = path.stat().st_size if from_end and path.exists() else 0
        self.partial = ""

    def poll(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
            self.partial = ""
        with self.path.open("r", encoding="utf-8", errors="replace") as source:
            source.seek(self.offset)
            data = self.partial + source.read()
            self.offset = source.tell()
        lines = data.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.partial = lines.pop()
        else:
            self.partial = ""
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("event"):
                records.append(value)
        return records


class LiveCollector:
    def __init__(
        self,
        *,
        backend_trace: Path,
        output: Path,
        metro_log: Path | None,
        adb_command: str,
        clear_logcat: bool,
        from_start: bool,
        once: bool,
    ) -> None:
        self.backend_tail = FileTail(backend_trace, from_end=not from_start)
        self.metro_tail = FileTail(metro_log, from_end=not from_start) if metro_log else None
        self.output_path = output
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output = output.open("a", encoding="utf-8")
        self.adb_command = adb_command
        self.clear_logcat = clear_logcat
        self.once = once
        self.records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.seen: set[tuple[Any, ...]] = set()
        self.printed_durations: set[tuple[str, str]] = set()
        self.completed_turns: set[str] = set()
        self.turn_response_ids: dict[str, str] = {}
        self.response_turn_ids: dict[str, str] = {}
        self.rejected_records: list[dict[str, Any]] = []
        self.logcat_process: subprocess.Popen[str] | None = None
        self.logcat_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()

    def start(self) -> None:
        if shutil.which(self.adb_command) is None:
            raise RuntimeError(f"{self.adb_command!r} was not found on PATH")
        if self.clear_logcat:
            subprocess.run(
                [self.adb_command, "logcat", "-c"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        logcat_command = build_logcat_command(self.adb_command, start_at_now=not self.clear_logcat)
        if not self.clear_logcat:
            print(f"Android logcat capture starts at {logcat_command[5]}", flush=True)
        self.logcat_process = subprocess.Popen(
            logcat_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.logcat_process.stdout is not None
        threading.Thread(
            target=self._read_logcat,
            args=(self.logcat_process.stdout,),
            name="latency-logcat-reader",
            daemon=True,
        ).start()
        print(f"LIVE LATENCY CAPTURE ACTIVE -> {self.output_path}", flush=True)
        sources = "backend trace + Android all-buffer logcat"
        if self.metro_tail:
            sources += " + Metro"
        print(f"Sources: {sources}", flush=True)

    def _read_logcat(self, stream: Iterable[str]) -> None:
        for line in stream:
            if self.stop_event.is_set():
                return
            self.logcat_queue.put(line)

    def run(self, *, poll_seconds: float) -> None:
        try:
            while not self.stop_event.is_set():
                for record in self.backend_tail.poll():
                    self.accept(record, source="backend")
                if self.metro_tail:
                    for record in self.metro_tail.poll():
                        self.accept(record, source="metro")
                while True:
                    try:
                        line = self.logcat_queue.get_nowait()
                    except queue.Empty:
                        break
                    record = parse_log_line(line)
                    if record is not None:
                        self.accept(record, source="android")
                time.sleep(poll_seconds)
        finally:
            self.close()

    def accept(self, record: dict[str, Any], *, source: str) -> None:
        key = record_key(record)
        if key in self.seen:
            return
        self.seen.add(key)
        turn_id = record.get("turn_id")
        if not turn_id:
            self.output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()
            return
        turn_id = str(turn_id)
        event = str(record.get("event"))
        response_id = record.get("response_id")
        rejection: str | None = None
        if response_id is not None:
            response_id = str(response_id)
            expected_response_id = self.turn_response_ids.get(turn_id)
            if expected_response_id is None:
                self.turn_response_ids[turn_id] = response_id
            elif expected_response_id != response_id:
                rejection = "wrong_response_id"
            expected_turn_id = self.response_turn_ids.get(response_id)
            if expected_turn_id is None:
                self.response_turn_ids[response_id] = turn_id
            elif expected_turn_id != turn_id:
                rejection = "wrong_turn_id"
        if rejection is not None:
            record.setdefault("metadata", {})["collector_rejection"] = rejection
        self.output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.output.flush()
        if rejection is not None:
            self.rejected_records.append(record)
            print(
                f"REJECTED stale event={event} turn={turn_id} "
                f"response={response_id} reason={rejection}",
                flush=True,
            )
            return
        self.records[turn_id].append(record)
        print(
            f"[{record.get('timestamp', '?')}] {source} {event} "
            f"session={record.get('session_id')} turn={turn_id} "
            f"response={record.get('response_id')}",
            flush=True,
        )
        self.print_completed_stages(turn_id)
        if event in CompletionEvents:
            self.print_turn(turn_id, completion_event=event)
            if self.once and event in {"turn_complete", "server.turn.completed"}:
                self.stop_event.set()

    def print_completed_stages(self, turn_id: str) -> None:
        values = metrics_for_turn(self.records[turn_id])
        for label, start, end in _STAGES:
            key = next(
                (metric_key for metric_label, metric_key in METRICS if metric_label == label),
                None,
            )
            if key is None:
                key = {
                    "speech": "speech_duration",
                    "STT": "speech_end_to_stt_final",
                    "embedding": "embedding",
                    "vector_search": "vector_search",
                    "FTS": "fts",
                    "RRF": "rrf",
                    "rerank": "rerank",
                    "Memory context pipeline": "memory_context_pipeline",
                    "LLM TTFT": "llm_ttft",
                    "LLM total": "llm_total",
                    "TTS TTFA": "tts_ttfa",
                    "playback": "playback_duration",
                }[label]
            duration = values.get(key)
            marker = (turn_id, key)
            if duration is not None and marker not in self.printed_durations:
                self.printed_durations.add(marker)
                print(f"  COMPLETED {label}: {duration:.1f} ms ({start} -> {end})", flush=True)

    def print_turn(self, turn_id: str, *, completion_event: str) -> None:
        records = self.records[turn_id]
        values = metrics_for_turn(records)
        errors = correlation_errors(records)
        first = min(records, key=lambda item: item.get("timestamp_ms", 0))
        print(f"\nTURN {turn_id} COMPLETE ({completion_event})", flush=True)
        print(f"Session: {first.get('session_id')}", flush=True)
        for label, key in METRICS:
            value = values[key]
            print(f"{label + ':':34} {value if value is not None else 'n/a'} ms", flush=True)
        ranked = sorted(
            ((label, values[key]) for label, key in METRICS if values[key] is not None),
            key=lambda item: item[1] or 0,
            reverse=True,
        )[:3]
        if ranked:
            print(
                "Largest measured durations: "
                + ", ".join(f"{label} ({value:.1f} ms)" for label, value in ranked),
                flush=True,
            )
        print(f"Trace records: {len(records)}\n", flush=True)
        if errors:
            print(f"Correlation rejections: {', '.join(errors)}", flush=True)
        self.completed_turns.add(turn_id)

    def close(self) -> None:
        self.stop_event.set()
        if self.logcat_process is not None and self.logcat_process.poll() is None:
            self.logcat_process.terminate()
            try:
                self.logcat_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.logcat_process.kill()
        self.output.close()
        print("LIVE LATENCY CAPTURE STOPPED", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-trace",
        type=Path,
        help="backend JSONL source (auto-detected when omitted)",
    )
    parser.add_argument("--metro-log", type=Path)
    parser.add_argument("--output", type=Path, default=Path("logs/live_latency_trace.jsonl"))
    parser.add_argument("--adb-command", default="adb")
    parser.add_argument("--clear-logcat", action="store_true")
    parser.add_argument("--from-start", action="store_true", help="replay existing source files")
    parser.add_argument(
        "--once", action="store_true", help="stop after the first backend turn completion"
    )
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    backend_trace = args.backend_trace
    if backend_trace is None:
        candidates = (Path("logs/latency_trace.jsonl"), Path("backend/logs/latency_trace.jsonl"))
        backend_trace = next((path for path in candidates if path.exists()), candidates[0])
        print(f"Backend trace auto-detected: {backend_trace}", flush=True)
    collector = LiveCollector(
        backend_trace=backend_trace,
        output=args.output,
        metro_log=args.metro_log,
        adb_command=args.adb_command,
        clear_logcat=args.clear_logcat,
        from_start=args.from_start,
        once=args.once,
    )
    try:
        collector.start()
        collector.run(poll_seconds=args.poll_seconds)
    except KeyboardInterrupt:
        collector.close()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        collector.close()
        raise SystemExit(f"live latency capture failed: {error}") from error


if __name__ == "__main__":
    main()
