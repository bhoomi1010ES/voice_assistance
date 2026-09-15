"""Low-overhead, redacted JSONL latency tracing.

The backend owns the host-side trace file. Mobile clients emit the same record
shape through their native logcat sink because an Android process cannot write
to the repository filesystem. ``scripts/merge_latency_trace.py`` combines the
two streams after a physical run.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
_SENSITIVE_PARTS = ("token", "secret", "password", "authorization", "api_key", "credential")


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

    def emit(
        self,
        *,
        session_id: Any = None,
        turn_id: Any = None,
        response_id: Any = None,
        component: str,
        event: str,
        monotonic_ms: float | None = None,
        timestamp: str | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        wall = timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        wall_ms = (
            int(datetime.fromisoformat(wall.replace("Z", "+00:00")).timestamp() * 1000)
            if timestamp
            else int(time.time() * 1000)
        )
        record: dict[str, Any] = {
            "timestamp": wall,
            "timestamp_ms": wall_ms,
            "monotonic_ms": round(
                time.monotonic() * 1000 if monotonic_ms is None else monotonic_ms, 3
            ),
            "clock_domain": "backend",
            "session_id": str(session_id) if session_id is not None else None,
            "turn_id": str(turn_id) if turn_id is not None else None,
            "response_id": str(response_id) if response_id is not None else None,
            "component": component,
            "event": event,
        }
        if duration_ms is not None:
            record["duration_ms"] = round(max(0.0, float(duration_ms)), 3)
        if metadata:
            record["metadata"] = _safe_value(metadata)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            with _WRITE_LOCK:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as trace:
                    trace.write(line)
                    trace.write("\n")
        except OSError:
            # Tracing is strictly diagnostic and must never break a voice turn.
            return


def emit_latency(**kwargs: Any) -> None:
    """Convenience function for boundary code that does not retain a tracer."""

    LatencyTracer().emit(**kwargs)
