"""Low-overhead, redacted JSONL latency tracing.

The backend owns the host-side trace file. Mobile clients emit the same record
shape through their native logcat sink because an Android process cannot write
to the repository filesystem. ``scripts/merge_latency_trace.py`` combines the
two streams after a physical run.
"""

from __future__ import annotations

import atexit
import json
import math
import os
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

_WRITE_LOCK = threading.Lock()
_PREVIOUS_WRITE: dict[str, Any] | None = None
_TRACE_HANDLES: weakref.WeakSet[TextIO] = weakref.WeakSet()
_SENSITIVE_PARTS = ("token", "secret", "password", "authorization", "api_key", "credential")
BACKEND_CLOCK_DOMAIN = "backend_python_perf_counter"


def _close_trace_handles() -> None:
    with _WRITE_LOCK:
        for handle in list(_TRACE_HANDLES):
            try:
                handle.close()
            except OSError:
                pass
        _TRACE_HANDLES.clear()


atexit.register(_close_trace_handles)


def _safe_value(value: Any, *, key: str = "") -> Any:
    lowered = key.casefold()
    if any(part in lowered for part in _SENSITIVE_PARTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(name): _safe_value(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:32]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:512]


class LatencyTracer:
    """Append one safe, correlated event without affecting voice control flow."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("LATENCY_TRACE_PATH")
        if configured is None:
            configured = Path(__file__).resolve().parents[3] / "logs/latency_trace.jsonl"
        self.path = Path(configured)
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path
        self._handle: TextIO | None = None

    def close(self) -> None:
        with _WRITE_LOCK:
            handle = getattr(self, "_handle", None)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
                _TRACE_HANDLES.discard(handle)
                self._handle = None

    def __del__(self) -> None:
        self.close()

    def emit(
        self,
        *,
        session_id: Any = None,
        turn_id: Any = None,
        response_id: Any = None,
        component: str,
        event: str,
        process: str | None = None,
        clock_domain: str | None = None,
        monotonic_ms: float | None = None,
        monotonic_ns: int | None = None,
        timestamp: str | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_monotonic_ns = (
            int(monotonic_ns)
            if monotonic_ns is not None
            else int(float(monotonic_ms) * 1_000_000)
            if monotonic_ms is not None
            else time.perf_counter_ns()
        )
        wall = timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        wall_ms = (
            int(datetime.fromisoformat(wall.replace("Z", "+00:00")).timestamp() * 1000)
            if timestamp
            else int(time.time() * 1000)
        )
        record: dict[str, Any] = {
            "timestamp": wall,
            "wall_time_utc": wall,
            "timestamp_ms": wall_ms,
            "monotonic_ns": event_monotonic_ns,
            "monotonic_ms": round(event_monotonic_ns / 1_000_000, 3),
            "process": process or f"backend:{os.getpid()}",
            "clock_domain": clock_domain or BACKEND_CLOCK_DOMAIN,
            "session_id": str(session_id) if session_id is not None else None,
            "turn_id": str(turn_id) if turn_id is not None else None,
            "response_id": str(response_id) if response_id is not None else None,
            "component": component,
            "event": event,
            # Preserve invalid negative values so the analyzer can reject the
            # measurement explicitly. Never turn a clock error into zero.
            "duration_ms": (
                round(float(duration_ms), 3)
                if duration_ms is not None and math.isfinite(float(duration_ms))
                else None
            ),
            "metadata": _safe_value(metadata or {}),
        }
        try:
            lock_started_ns = time.perf_counter_ns()
            _WRITE_LOCK.acquire()
            lock_acquired_ns = time.perf_counter_ns()
            global _PREVIOUS_WRITE
            if _PREVIOUS_WRITE is not None:
                record["trace_writer_previous"] = _PREVIOUS_WRITE
            write_started_ns = lock_acquired_ns
            try:
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                self.path.parent.mkdir(parents=True, exist_ok=True)
                trace = self._handle
                if trace is None or trace.closed:
                    trace = self.path.open("a", encoding="utf-8")
                    self._handle = trace
                    _TRACE_HANDLES.add(trace)
                trace.write(line)
                trace.write("\n")
                # Keep the collector's tail current without paying the cost of
                # opening and closing the JSONL file for each trace record.
                trace.flush()
            finally:
                write_completed_ns = time.perf_counter_ns()
                _PREVIOUS_WRITE = {
                    "event": event,
                    "session_id": record["session_id"],
                    "turn_id": record["turn_id"],
                    "response_id": record["response_id"],
                    "lock_wait_ns": max(0, lock_acquired_ns - lock_started_ns),
                    "execution_ns": max(0, write_completed_ns - write_started_ns),
                }
                _WRITE_LOCK.release()
        except OSError:
            # Tracing is strictly diagnostic and must never break a voice turn.
            return


@contextmanager
def latency_span(
    emit: Callable[..., None],
    *,
    component: str,
    event: str,
    session_id: Any = None,
    turn_id: Any = None,
    response_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Emit a monotonic start/completion pair around real synchronous or awaited work."""

    started_ns = time.perf_counter_ns()
    emit(
        component=component,
        event=f"{event}_started",
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        monotonic_ns=started_ns,
        metadata=metadata,
    )
    status = "completed"
    try:
        yield
    except BaseException as error:
        status = "cancelled" if error.__class__.__name__ == "CancelledError" else "failed"
        raise
    finally:
        completed_ns = time.perf_counter_ns()
        completion_metadata = dict(metadata or {})
        completion_metadata["status"] = status
        duration_ms = (completed_ns - started_ns) / 1_000_000
        if component == "postgres":
            completion_metadata["execution_ms"] = duration_ms
            completion_metadata["db_pool_wait_included"] = True
            completion_metadata["db_pool_wait_separately_measured"] = False
        elif component == "voice_registry":
            registry_type = str(completion_metadata.get("registry_type", ""))
            if "redis" in registry_type.casefold():
                completion_metadata["execution_ms"] = duration_ms
                completion_metadata["redis_pool_wait_included"] = True
                completion_metadata["redis_pool_wait_separately_measured"] = False
        emit(
            component=component,
            event=f"{event}_completed",
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            monotonic_ns=completed_ns,
            duration_ms=duration_ms,
            metadata=completion_metadata,
        )


def emit_latency(**kwargs: Any) -> None:
    """Convenience function for boundary code that does not retain a tracer."""

    LatencyTracer().emit(**kwargs)
