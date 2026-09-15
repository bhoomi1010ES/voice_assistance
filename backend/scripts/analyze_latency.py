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
    ("Device speech-end -> first token received", "speech_end_to_first_token"),
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

PIPELINE_STAGE_EVENTS = {
    "gateway_commit_queue_wait": ("gateway_commit_queue_wait_completed",),
    "transcript_final_send_lock_wait": ("transcript_send_lock_acquired",),
    "stt_finalize": ("stt_finalize_completed",),
    "turn_persistence": ("turn_persistence_completed",),
    "context_policy_lookup": ("memory_policy_lookup_completed",),
    "confirmation_routing": ("confirmation_routing_completed",),
    "transcript_final_send": ("transcript_final_send_completed",),
    "stt_turn_close": ("stt_turn_close_completed",),
    "transcript_delivery_log_write": ("transcript_delivery_log_write_completed",),
    "first_assistant_text_send": ("first_assistant_text_send_completed",),
    "memory_decision": ("memory_decision_completed",),
    "explicit_memory_routing": ("explicit_memory_routing_completed",),
    "tool_routing": ("tool_routing_completed",),
    "prompt_build": ("prompt_build_completed",),
    "token_budget": ("token_budget_completed",),
    "llm_prepare": ("llm_prepare_completed",),
    "provider_prepare": ("provider_prepare_completed",),
    "llm_semaphore_wait": ("llm_semaphore_acquired",),
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
        record.get("monotonic_ns"),
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


def filter_turn_records(
    records: list[dict[str, Any]], turn_ids: list[str] | None
) -> list[dict[str, Any]]:
    if not turn_ids:
        return records
    selected = set(turn_ids)
    return [
        record
        for record in records
        if record.get("turn_id") is not None and str(record["turn_id"]) in selected
    ]


def _time(record: dict[str, Any]) -> float | None:
    value_ns = record.get("monotonic_ns")
    if isinstance(value_ns, (int, float)):
        return float(value_ns) / 1_000_000
    value = record.get("monotonic_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _events(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    priorities: dict[str, int] = {}
    for record in sorted(records, key=lambda item: item.get("timestamp_ms", math.inf)):
        raw_event = str(record.get("event", ""))
        event = EVENT_ALIASES.get(raw_event, raw_event)
        value = _time(record)
        # Keep wall-only records available for display/debugging, but _delta
        # will never use wall timestamps to calculate a latency.
        if value is None and not isinstance(record.get("timestamp_ms"), (int, float)):
            continue
        normalized = dict(record)
        if raw_event == "vad_end":
            result["device_speech_end"] = normalized
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
    if first.get("clock_domain") != last.get("clock_domain"):
        return None
    first_value = _time(first)
    last_value = _time(last)
    if not isinstance(first_value, (int, float)) or not isinstance(last_value, (int, float)):
        return None
    delta = float(last_value) - float(first_value)
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


def _first_measured(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def metrics_for_turn(records: list[dict[str, Any]]) -> dict[str, float | None]:
    events = _events(records)
    values: dict[str, float | None] = {
        "speech_duration": _delta(events, "speech_start", "speech_end"),
        "speech_end_to_stt_final": _first_measured(
            _delta(events, "device_speech_end", "client_stt_final_received"),
            _delta(events, "speech_end", "stt_final"),
        ),
        "embedding": _delta(events, "embedding_start", "embedding_end"),
        "vector_search": _delta(events, "vector_search_start", "vector_search_end"),
        "fts": _delta(events, "fts_start", "fts_end"),
        "rrf": _delta(events, "rrf_start", "rrf_end"),
        "rerank": _delta(events, "rerank_start", "rerank_end"),
        "rag": _delta(events, "rag_start", "rag_end"),
        "llm_ttft": _delta(events, "llm_request_start", "llm_first_token"),
        "llm_total": _delta(events, "llm_request_start", "llm_complete"),
        "speech_end_to_first_token": _delta(
            events, "device_speech_end", "first_assistant_token_received"
        ),
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
            ("device_speech_end",),
            ("tts_first_chunk_received",),
        ),
        "end_to_end": _delta_aliases(
            events,
            ("device_speech_end",),
            ("tts_playback_complete", "tts_playback_completed"),
        ),
    }
    return values


def _stage_duration(records: list[dict[str, Any]], stage: str) -> float | None:
    """Sum completed execution spans; queue waits remain separate stages."""

    completed_events = PIPELINE_STAGE_EVENTS.get(stage, ())
    values = [
        float(record["duration_ms"])
        for record in records
        if record.get("event") in completed_events
        and isinstance(record.get("duration_ms"), (int, float))
    ]
    if values:
        return round(sum(values), 3)
    events = _events(records)
    starts = {
        "gateway_commit_queue_wait": "gateway_commit_queue_wait_started",
        "transcript_final_send_lock_wait": "transcript_send_lock_wait_started",
        "stt_finalize": "stt_finalize_started",
        "turn_persistence": "turn_persistence_started",
        "context_policy_lookup": "memory_policy_lookup_started",
        "confirmation_routing": "confirmation_routing_started",
        "transcript_final_send": "transcript_final_send_started",
        "stt_turn_close": "stt_turn_close_started",
        "transcript_delivery_log_write": "transcript_delivery_log_write_started",
        "first_assistant_text_send": "first_assistant_text_send_started",
        "memory_decision": "memory_decision_started",
        "explicit_memory_routing": "explicit_memory_routing_started",
        "tool_routing": "tool_routing_started",
        "prompt_build": "prompt_build_started",
        "token_budget": "token_budget_started",
        "llm_prepare": "llm_prepare_started",
        "provider_prepare": "provider_prepare_started",
        "llm_semaphore_wait": "llm_semaphore_wait_started",
    }
    ends = {
        "gateway_commit_queue_wait": "gateway_commit_queue_wait_completed",
        "transcript_final_send_lock_wait": "transcript_send_lock_acquired",
        "stt_finalize": "stt_finalize_completed",
        "turn_persistence": "turn_persistence_completed",
        "context_policy_lookup": "memory_policy_lookup_completed",
        "confirmation_routing": "confirmation_routing_completed",
        "transcript_final_send": "transcript_final_send_completed",
        "stt_turn_close": "stt_turn_close_completed",
        "transcript_delivery_log_write": "transcript_delivery_log_write_completed",
        "first_assistant_text_send": "first_assistant_text_send_completed",
        "memory_decision": "memory_decision_completed",
        "explicit_memory_routing": "explicit_memory_routing_completed",
        "tool_routing": "tool_routing_completed",
        "prompt_build": "prompt_build_completed",
        "token_budget": "token_budget_completed",
        "llm_prepare": "llm_prepare_completed",
        "provider_prepare": "provider_prepare_completed",
        "llm_semaphore_wait": "llm_semaphore_acquired",
    }
    if stage not in starts:
        return None
    return _delta(events, starts[stage], ends[stage])


def _trace_write_overhead_ms(records: list[dict[str, Any]], turn_id: str) -> float:
    total_ns = 0
    for record in records:
        previous = record.get("trace_writer_previous")
        if not isinstance(previous, dict) or str(previous.get("turn_id")) != turn_id:
            continue
        for key in ("lock_wait_ns", "execution_ns"):
            value = previous.get(key)
            if isinstance(value, (int, float)):
                total_ns += max(0, int(value))
    return round(total_ns / 1_000_000, 3)


def pipeline_metrics_for_turn(records: list[dict[str, Any]]) -> dict[str, float | None]:
    """Compute non-overlapping stage totals and expose uncovered time as residual."""

    events = _events(records)
    turn_id = str(records[0].get("turn_id", "")) if records else ""
    stt_final_to_orchestration = _delta(events, "stt_final_received", "orchestration_started")
    stt_finalize = _stage_duration(records, "stt_finalize")
    turn_persistence = _stage_duration(records, "turn_persistence")
    gateway_commit_queue_wait = _stage_duration(records, "gateway_commit_queue_wait")
    context_lookup = _stage_duration(records, "context_policy_lookup")
    confirmation = _stage_duration(records, "confirmation_routing")
    transcript_send_inclusive = _stage_duration(records, "transcript_final_send")
    transcript_send_lock_wait = _stage_duration(records, "transcript_final_send_lock_wait")
    transcript_send = (
        round(max(0.0, transcript_send_inclusive - transcript_send_lock_wait), 3)
        if transcript_send_inclusive is not None and transcript_send_lock_wait is not None
        else transcript_send_inclusive
    )
    stt_turn_close = _stage_duration(records, "stt_turn_close")
    transcript_log_write = _stage_duration(records, "transcript_delivery_log_write")
    memory = _stage_duration(records, "memory_decision")
    explicit_memory_routing = _stage_duration(records, "explicit_memory_routing")
    tool_routing = _stage_duration(records, "tool_routing")
    prompt_inclusive = _stage_duration(records, "prompt_build")
    token_budget = _stage_duration(records, "token_budget")
    provider_prepare = _stage_duration(records, "provider_prepare")
    llm_prepare = _stage_duration(records, "llm_prepare")
    semaphore_wait = _stage_duration(records, "llm_semaphore_wait")
    finalize_queue_wait = next(
        (
            float(record["duration_ms"])
            for record in records
            if record.get("event") == "finalize_task_started"
            and isinstance(record.get("duration_ms"), (int, float))
        ),
        None,
    )
    if explicit_memory_routing is not None and tool_routing is not None:
        tool_routing = round(explicit_memory_routing + tool_routing, 3)
    elif explicit_memory_routing is not None:
        tool_routing = explicit_memory_routing
    tool_classification = _stage_duration(records, "tool_routing") or 0.0
    prompt_build = (
        round(
            max(
                0.0,
                prompt_inclusive - (token_budget or 0.0) - tool_classification,
            ),
            3,
        )
        if prompt_inclusive is not None
        else None
    )
    orchestration_to_request = _delta(events, "orchestration_started", "llm_request_started")
    stt_final_to_request = _delta(events, "stt_final_received", "llm_request_started")
    llm_request_to_stream = _delta(events, "http_request_started", "llm_stream_opened")
    connection_acquisition = _delta(events, "http_request_started", "llm_connection_acquired")
    stream_to_first_token = _delta(events, "llm_stream_opened", "llm_first_token")
    provider_ttft = _delta(events, "http_request_started", "llm_first_token")
    provider_to_gateway_delay = next(
        (
            float(record["duration_ms"])
            for record in records
            if record.get("event") == "llm_first_token_observed_by_gateway"
            and isinstance(record.get("duration_ms"), (int, float))
        ),
        None,
    )
    stt_final_to_first_token = _delta(events, "stt_final_received", "llm_first_token")
    speech_end_to_first_token = _delta(
        events, "device_speech_end", "first_assistant_token_received"
    )

    # prompt_build and token_budget/tool classification are nested. Convert the
    # outer prompt span to its exclusive portion before summing stage totals.
    known_stages = (
        stt_final_to_orchestration,
        turn_persistence,
        context_lookup,
        confirmation,
        transcript_send,
        transcript_send_lock_wait,
        stt_turn_close,
        transcript_log_write,
        memory,
        tool_routing,
        prompt_build,
        token_budget,
        llm_prepare,
        semaphore_wait,
    )
    known_total = round(sum(value for value in known_stages if value is not None), 3)
    unaccounted = (
        round(stt_final_to_request - known_total, 3) if stt_final_to_request is not None else None
    )
    return {
        "speech_end_to_stt_final": _first_measured(
            _delta(events, "device_speech_end", "client_stt_final_received"),
            _delta(events, "speech_end", "stt_final"),
        ),
        "stt_final_to_orchestration": stt_final_to_orchestration,
        "stt_processing": stt_finalize,
        "server_commit_to_stt_final": _delta(events, "speech_end", "stt_final"),
        "turn_persistence": turn_persistence,
        "gateway_commit_queue_wait": gateway_commit_queue_wait,
        "context_loading": context_lookup,
        "history_loading": None,
        "confirmation_routing": confirmation,
        "transcript_final_send": transcript_send,
        "transcript_final_send_inclusive": transcript_send_inclusive,
        "transcript_send_lock_wait": transcript_send_lock_wait,
        "stt_turn_close": stt_turn_close,
        "transcript_log_write": transcript_log_write,
        "memory_rag_decision": memory,
        "tool_routing": tool_routing,
        "prompt_build": prompt_build,
        "token_budget": token_budget,
        "safety_preprocessing": None,
        "llm_request_preparation": llm_prepare,
        "provider_preparation": provider_prepare if provider_prepare is not None else llm_prepare,
        "semaphore_wait": semaphore_wait,
        "finalize_task_queue_wait": finalize_queue_wait,
        "trace_write_overhead": _trace_write_overhead_ms(records, turn_id),
        "orchestration_to_request": orchestration_to_request,
        "request_to_stream_opened": llm_request_to_stream,
        "connection_acquisition": connection_acquisition,
        "stream_opened_to_first_token": stream_to_first_token,
        "provider_ttft": provider_ttft,
        "provider_to_gateway_delay": provider_to_gateway_delay,
        "first_assistant_text_send": _stage_duration(records, "first_assistant_text_send"),
        "legacy_llm_ttft": _delta(events, "llm_request_start", "llm_first_token"),
        "stt_final_to_request": stt_final_to_request,
        "stt_final_to_first_token": stt_final_to_first_token,
        "speech_end_to_first_token": speech_end_to_first_token,
        "speech_end_to_first_assistant_audio": _delta(
            events, "device_speech_end", "tts_first_chunk_received"
        ),
        "backend_speech_end_to_provider_first_token": _delta(
            events, "speech_end", "llm_first_token"
        ),
        "connection_establishment": round(
            sum(
                float(record["duration_ms"])
                for record in records
                if record.get("event") in {"llm_tcp_connect_completed", "llm_tls_completed"}
                and isinstance(record.get("duration_ms"), (int, float))
            ),
            3,
        ),
        "known_measured_stages_total": known_total,
        "unaccounted_latency": unaccounted,
    }


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
    pipeline_aggregate: dict[str, list[float]] = defaultdict(list)
    for turn_id, turn_records in sorted(grouped.items()):
        values = metrics_for_turn(turn_records)
        pipeline = pipeline_metrics_for_turn(turn_records)
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
        print("STT → LLM PIPELINE BREAKDOWN")
        pipeline_rows = (
            ("Speech-end → STT final", "speech_end_to_stt_final"),
            ("STT final → orchestration start", "stt_final_to_orchestration"),
            ("Turn persistence", "turn_persistence"),
            ("Gateway audio-commit queue wait", "gateway_commit_queue_wait"),
            ("STT finalize processing", "stt_processing"),
            ("Server commit -> STT final (backend clock)", "server_commit_to_stt_final"),
            ("Session/context loading", "context_loading"),
            ("Conversation history", "history_loading"),
            ("Confirmation routing", "confirmation_routing"),
            ("Transcript WebSocket send (exclusive)", "transcript_final_send"),
            ("Transcript send-lock wait", "transcript_send_lock_wait"),
            ("STT turn cleanup", "stt_turn_close"),
            ("Synchronous transcript log", "transcript_log_write"),
            ("Provider first token → gateway", "provider_to_gateway_delay"),
            ("First assistant text WebSocket send", "first_assistant_text_send"),
            ("Device speech-end -> first token received", "speech_end_to_first_token"),
            ("Device speech-end -> first assistant audio", "speech_end_to_first_assistant_audio"),
            ("Memory/RAG decision", "memory_rag_decision"),
            ("Tool routing", "tool_routing"),
            ("Prompt construction (exclusive)", "prompt_build"),
            ("Token budgeting", "token_budget"),
            ("Safety preprocessing", "safety_preprocessing"),
            ("LLM request preparation", "llm_request_preparation"),
            ("NVIDIA provider preparation", "provider_preparation"),
            ("LLM semaphore wait", "semaphore_wait"),
            ("STT finalize task queue wait", "finalize_task_queue_wait"),
            ("Trace writer overhead", "trace_write_overhead"),
            ("Orchestration start → LLM request", "orchestration_to_request"),
            ("LLM request → stream opened", "request_to_stream_opened"),
            ("Stream opened → first token", "stream_opened_to_first_token"),
            ("Provider TTFT (HTTP start → token)", "provider_ttft"),
            ("Legacy LLM TTFT", "legacy_llm_ttft"),
            ("STT final → LLM request", "stt_final_to_request"),
            ("STT final → first token", "stt_final_to_first_token"),
            ("LLM TCP/TLS establishment", "connection_establishment"),
            (
                "Request dispatch -> connection assignment (pool wait included)",
                "connection_acquisition",
            ),
            ("Known measured stages total", "known_measured_stages_total"),
            ("UNACCOUNTED LATENCY", "unaccounted_latency"),
        )
        for label, key in pipeline_rows:
            value = pipeline[key]
            rendered = f"{value:.3f} ms" if value is not None else "n/a"
            if key in {"history_loading", "safety_preprocessing"}:
                rendered += " (no standalone stage in current path)"
            elif key == "context_loading" and value is not None:
                rendered += " (per-turn memory policy DB lookup; session already loaded)"
            elif key == "provider_preparation" and value is not None:
                rendered += " (overlaps LLM request preparation; not additive)"
            print(f"{label + ':':43} {rendered}")
            if value is not None:
                pipeline_aggregate[key].append(value)
        print(
            "TTFT semantics: legacy LLM TTFT starts when the provider stream iterator emits "
            "request_started, after the concurrency semaphore and before adapter payload "
            "preparation; it excludes local gateway orchestration. Provider TTFT starts at "
            "the HTTP transport call."
        )
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

    print()
    print("STT→LLM LATENCY INVESTIGATION")
    summary_rows = (
        ("STT final → orchestration", "stt_final_to_orchestration"),
        ("Persistence", "turn_persistence"),
        ("Gateway audio-commit queue wait", "gateway_commit_queue_wait"),
        ("Context loading", "context_loading"),
        ("History loading", "history_loading"),
        ("Confirmation routing", "confirmation_routing"),
        ("Memory/RAG decision", "memory_rag_decision"),
        ("Tool routing", "tool_routing"),
        ("Prompt build", "prompt_build"),
        ("Token budget", "token_budget"),
        ("Safety preprocessing", "safety_preprocessing"),
        ("Provider preparation", "provider_preparation"),
        ("Queue/semaphore wait", "semaphore_wait"),
        ("LLM connection establishment", "connection_establishment"),
        (
            "Request dispatch -> connection assignment (pool wait included)",
            "connection_acquisition",
        ),
        ("Provider TTFT", "provider_ttft"),
        ("Provider first token → gateway", "provider_to_gateway_delay"),
        ("First assistant text send", "first_assistant_text_send"),
        ("Device speech-end -> first token received", "speech_end_to_first_token"),
        ("Device speech-end -> first assistant audio", "speech_end_to_first_assistant_audio"),
        ("STT final → LLM request", "stt_final_to_request"),
        ("STT final → first token", "stt_final_to_first_token"),
        ("Unaccounted latency", "unaccounted_latency"),
    )
    print("Metric | count | min | p50 | p90 | p95 | max | mean")
    print("--- | ---: | ---: | ---: | ---: | ---: | ---: | ---:")
    for label, key in summary_rows:
        values = sorted(pipeline_aggregate[key])
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
    parser.add_argument(
        "--turn-id",
        action="append",
        metavar="TURN_ID",
        help="report selected correlated turn(s); repeat to aggregate a defined set",
    )
    parser.add_argument("--backend-trace", type=Path, help="explicit backend trace to join")
    args = parser.parse_args()
    records = load_analysis_records(args.path, args.backend_trace)
    records = filter_turn_records(records, args.turn_id)
    print_report(records)


if __name__ == "__main__":
    main()
