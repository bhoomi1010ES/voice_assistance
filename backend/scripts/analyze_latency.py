#!/usr/bin/env python3
"""Print per-turn and aggregate latency metrics from unified JSONL traces."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

METRICS = (
    ("Speech duration", "speech_duration"),
    ("STT latency", "speech_end_to_stt_final"),
    ("Embedding latency", "embedding"),
    ("Vector retrieval", "vector_search"),
    ("FTS", "fts"),
    ("RRF", "rrf"),
    ("Reranking", "rerank"),
    ("Complete RAG", "rag"),
    ("LLM TTFT", "llm_ttft"),
    ("LLM total", "llm_total"),
    ("Speech-end -> first token", "speech_end_to_first_token"),
    ("TTS TTFA", "tts_ttfa"),
    ("TTS generation total", "tts_generation_total"),
    ("Server -> client first audio", "server_to_client_audio"),
    ("Playback buffering", "playback_buffer"),
    ("Playback duration", "playback_duration"),
    ("Speech-end -> first assistant audio", "speech_end_to_first_audio"),
    ("End-to-end", "end_to_end"),
)

EVENT_ALIASES = {
    "microphone_speech_start": "speech_start",
    "vad_end": "speech_end",
    "client_stt_final": "stt_final",
}


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("clock_domain"),
        record.get("event"),
        record.get("session_id"),
        record.get("turn_id"),
        record.get("response_id"),
        record.get("timestamp_ms"),
        record.get("monotonic_ms"),
    )


def load_analysis_records(path: Path, backend_trace: Path | None = None) -> list[dict[str, Any]]:
    """Load a trace and enrich live client captures with backend events."""

    records = load_records(path)
    candidates = [backend_trace] if backend_trace else []
    if backend_trace is None and path.name == "live_latency_trace.jsonl":
        candidates.extend(
            (Path("logs/latency_trace.jsonl"), Path("backend/logs/latency_trace.jsonl"))
        )
    seen = {_record_key(record) for record in records}
    for candidate in candidates:
        if candidate is None or candidate.resolve() == path.resolve():
            continue
        for record in load_records(candidate):
            key = _record_key(record)
            if key not in seen:
                records.append(record)
                seen.add(key)
    return records


def _time(record: dict[str, Any]) -> float | None:
    value = record.get("monotonic_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _clock_calibration_samples(
    records: list[dict[str, Any]],
) -> dict[str, list[tuple[float, float]]]:
    """Collect receive-time samples for client/native wall-clock alignment."""

    samples: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for record in records:
        if record.get("clock_domain") == "backend":
            continue
        received_at_ms = record.get("timestamp_ms")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        gateway_ms = metadata.get("gateway_timestamp_ms")
        if isinstance(gateway_ms, (int, float)) and isinstance(received_at_ms, (int, float)):
            samples[str(record.get("clock_domain", "client"))].append(
                (float(received_at_ms), float(gateway_ms) - float(received_at_ms))
            )
    return samples


def _aligned_timestamp_ms(
    record: dict[str, Any],
    samples: dict[str, list[tuple[float, float]]],
) -> float | None:
    timestamp_ms = record.get("timestamp_ms")
    if not isinstance(timestamp_ms, (int, float)):
        return None
    if record.get("clock_domain") == "backend":
        return float(timestamp_ms)
    domain = str(record.get("clock_domain", "client"))
    candidates = samples.get(domain) or samples.get("client")
    if not candidates:
        return float(timestamp_ms)
    _, offset = min(candidates, key=lambda item: abs(item[0] - float(timestamp_ms)))
    return float(timestamp_ms) + offset


def _events(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    priorities: dict[str, int] = {}
    samples = _clock_calibration_samples(records)
    for record in sorted(records, key=lambda item: item.get("timestamp_ms", math.inf)):
        raw_event = str(record.get("event", ""))
        event = EVENT_ALIASES.get(raw_event, raw_event)
        value = _time(record)
        if value is None:
            continue
        normalized = dict(record)
        aligned_timestamp = _aligned_timestamp_ms(record, samples)
        if aligned_timestamp is not None:
            normalized["_aligned_timestamp_ms"] = aligned_timestamp
        priority = 0 if raw_event == event else 1
        if event not in result or priority < priorities[event]:
            result[event] = normalized
            priorities[event] = priority
    return result


def _delta(events: dict[str, dict[str, Any]], start: str, end: str) -> float | None:
    if start not in events or end not in events:
        return None
    first = events[start]
    last = events[end]
    if first.get("clock_domain") == last.get("clock_domain"):
        first_value = _time(first)
        last_value = _time(last)
    else:
        first_value = first.get("_aligned_timestamp_ms", first.get("timestamp_ms"))
        last_value = last.get("_aligned_timestamp_ms", last.get("timestamp_ms"))
    if not isinstance(first_value, (int, float)) or not isinstance(last_value, (int, float)):
        return None
    delta = float(last_value) - float(first_value)
    # Wall clocks are the only common clock across backend/client/native
    # processes. Clock skew can invert two events; do not turn that into a
    # misleading zero-duration measurement.
    if first.get("clock_domain") != last.get("clock_domain") and delta < 0:
        return None
    return round(max(0.0, delta), 3)


def _delta_aliases(
    events: dict[str, dict[str, Any]],
    starts: tuple[str, ...],
    ends: tuple[str, ...],
) -> float | None:
    """Measure a stage while accepting equivalent client/native event names."""

    for start in starts:
        for end in ends:
            value = _delta(events, start, end)
            if value is not None:
                return value
    return None


def metrics_for_turn(records: list[dict[str, Any]]) -> dict[str, float | None]:
    events = _events(records)
    values: dict[str, float | None] = {
        "speech_duration": _delta(events, "speech_start", "speech_end"),
        "speech_end_to_stt_final": _delta(events, "speech_end", "stt_final"),
        "embedding": _delta(events, "embedding_start", "embedding_end"),
        "vector_search": _delta(events, "vector_search_start", "vector_search_end"),
        "fts": _delta(events, "fts_start", "fts_end"),
        "rrf": _delta(events, "rrf_start", "rrf_end"),
        "rerank": _delta(events, "rerank_start", "rerank_end"),
        "rag": _delta(events, "rag_start", "rag_end"),
        "llm_ttft": _delta(events, "llm_request_start", "llm_first_token"),
        "llm_total": _delta(events, "llm_request_start", "llm_complete"),
        "speech_end_to_first_token": _delta(events, "speech_end", "llm_first_token"),
        "tts_ttfa": _delta(events, "tts_request_start", "tts_first_audio_chunk"),
        "tts_generation_total": _delta(events, "tts_request_start", "tts_generation_complete"),
        "server_to_client_audio": _delta(
            events, "tts_first_audio_chunk", "tts_first_chunk_received"
        ),
        "playback_buffer": _delta_aliases(
            events,
            ("tts_first_chunk_received",),
            ("tts_playback_start", "tts_playback_started"),
        ),
        "playback_duration": _delta_aliases(
            events,
            ("tts_playback_start", "tts_playback_started"),
            ("tts_playback_complete", "tts_playback_completed"),
        ),
        "speech_end_to_first_audio": _delta_aliases(
            events,
            ("speech_end",),
            ("tts_first_chunk_received", "tts_playback_start", "tts_playback_started"),
        ),
        "end_to_end": _delta_aliases(
            events,
            ("speech_end",),
            ("tts_playback_complete", "tts_playback_completed"),
        ),
    }
    return values


def percentile(values: list[float], percent: float) -> float:
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percent
    lower = math.floor(rank)
    upper = math.ceil(rank)
    return values[lower] + (values[upper] - values[lower]) * (rank - lower)


def print_report(records: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        turn_id = record.get("turn_id")
        if turn_id:
            grouped[str(turn_id)].append(record)

    aggregate: dict[str, list[float]] = defaultdict(list)
    for turn_id, turn_records in sorted(grouped.items()):
        values = metrics_for_turn(turn_records)
        print(f"TURN {turn_id}")
        for label, key in METRICS:
            value = values[key]
            print(f"{label + ':':34} {value if value is not None else 'n/a'} ms")
            if value is not None:
                aggregate[key].append(value)
        ranked = sorted(
            ((label, values[key]) for label, key in METRICS if values[key] is not None),
            key=lambda item: item[1] or 0,
            reverse=True,
        )[:3]
        contributors = ", ".join(f"{label} ({value:.1f} ms)" for label, value in ranked)
        print("Largest contributors: " + contributors)
        print()

    print("Metric | count | min | p50 | p90 | p95 | max | mean")
    print("--- | ---: | ---: | ---: | ---: | ---: | ---: | ---:")
    for label, key in METRICS:
        values = sorted(aggregate[key])
        if not values:
            print(f"{label} | 0 | n/a | n/a | n/a | n/a | n/a | n/a")
            continue
        print(
            f"{label} | {len(values)} | {min(values):.1f} | {percentile(values, 0.50):.1f} | "
            f"{percentile(values, 0.90):.1f} | {percentile(values, 0.95):.1f} | "
            f"{max(values):.1f} | {statistics.mean(values):.1f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path("logs/latency_trace.jsonl"))
    parser.add_argument("--turn-id", help="report one correlated turn only")
    parser.add_argument("--backend-trace", type=Path, help="explicit backend trace to join")
    args = parser.parse_args()
    records = load_analysis_records(args.path, args.backend_trace)
    if args.turn_id:
        records = [record for record in records if str(record.get("turn_id")) == args.turn_id]
    print_report(records)


if __name__ == "__main__":
    main()
