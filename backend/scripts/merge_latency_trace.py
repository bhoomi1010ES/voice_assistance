#!/usr/bin/env python3
"""Merge backend JSONL and LATENCY_TRACE logcat/Metro captures."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

TRACE_PATTERN = re.compile(r"LATENCY_TRACE\s+(\{.*\})")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event"):
            records.append(value)
    return records


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TRACE_PATTERN.search(line)
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event"):
            records.append(value)
    return records


def read_source(path: Path) -> list[dict[str, Any]]:
    """Read either raw JSONL or a text log containing LATENCY_TRACE records."""

    return read_jsonl(path) if path.suffix.casefold() == ".jsonl" else read_log(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, default=Path("logs/latency_trace.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("logs/latency_trace.jsonl"))
    parser.add_argument("logs", nargs="*", type=Path)
    args = parser.parse_args()
    records = read_jsonl(args.backend)
    for path in args.logs:
        records.extend(read_source(path))
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record in records:
        key = (
            record.get("clock_domain"),
            record.get("event"),
            record.get("session_id"),
            record.get("turn_id"),
            record.get("response_id"),
            record.get("timestamp_ms"),
            record.get("monotonic_ms"),
        )
        unique[key] = record
    records = list(unique.values())
    records.sort(key=lambda item: (item.get("timestamp_ms", 0), item.get("monotonic_ms", 0)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")
    print(f"merged {len(records)} records into {args.output}")


if __name__ == "__main__":
    main()
