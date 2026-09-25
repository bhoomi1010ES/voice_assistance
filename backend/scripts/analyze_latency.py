#!/usr/bin/env python3
"""Print per-turn and aggregate latency metrics from unified JSONL traces."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DIAGNOSTIC_DURATION_MAX_MS = 300_000.0

METRICS = (
    ("Speech duration", "speech_duration"),
    ("Speech-end → STT final", "speech_end_to_stt_final"),
    ("Device commit -> client STT final", "device_commit_to_stt_final"),
    ("STT request duration", "stt_request_duration"),
    ("Embedding latency", "embedding"),
    ("Vector retrieval", "vector_search"),
    ("FTS", "fts"),
    ("RRF", "rrf"),
    ("Reranking", "rerank"),
    ("Memory context pipeline", "memory_context_pipeline"),
    ("LLM TTFT", "llm_ttft"),
    ("LLM total", "llm_total"),
    ("Device speech-end -> first token received", "speech_end_to_first_token"),
    ("Device commit -> first text", "device_commit_to_first_text"),
    ("TTS TTFA", "tts_ttfa"),
    ("TTS generation", "tts_generation_total"),
    ("Server -> client first audio", "server_to_client_audio"),
    ("Playback buffering", "playback_buffer"),
    ("Playback duration", "playback_duration"),
    ("Speech-end -> first assistant audio", "speech_end_to_first_audio"),
    ("Device commit -> first audio", "device_commit_to_first_audio"),
    ("Device commit -> playback complete", "device_commit_to_playback_complete"),
    ("End-to-end", "end_to_end"),
    ("Backend speech-end -> first text", "backend_speech_end_to_first_text"),
    ("Backend speech-end -> first TTS audio", "backend_speech_end_to_first_audio"),
    ("Backend complete turn", "backend_complete_turn"),
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
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    segment = next(
        (
            metadata.get(name)
            for name in ("tts_segment_id", "segment_id", "segment_index", "chunk_id")
            if metadata.get(name) is not None
        ),
        None,
    )
    return (
        record.get("clock_domain"),
        record.get("event"),
        record.get("session_id"),
        record.get("turn_id"),
        record.get("response_id"),
        record.get("timestamp_ms"),
        record.get("monotonic_ms"),
        record.get("monotonic_ns"),
        segment,
        metadata.get("sequence"),
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
    commit = result.get("native_turn_commit_sent")
    if commit is not None and _time(commit) is not None:
        commit_time = _time(commit)
        candidates = [
            record
            for record in records
            if record.get("event") == "vad_end"
            and _same_clock(record, commit)
            and _time(record) is not None
            and _time(record) <= commit_time
            and record.get("session_id") == commit.get("session_id")
            and record.get("turn_id") == commit.get("turn_id")
            and record.get("response_id") == commit.get("response_id")
        ]
        if candidates:
            result["device_speech_end"] = max(candidates, key=lambda record: _time(record) or -1)
        else:
            result.pop("device_speech_end", None)
    return result


def _delta(events: dict[str, dict[str, Any]], start: str, end: str) -> float | None:
    if start not in events or end not in events:
        return None
    first = events[start]
    last = events[end]
    if not _same_clock(first, last):
        return None
    first_value = _time(first)
    last_value = _time(last)
    if not isinstance(first_value, (int, float)) or not isinstance(last_value, (int, float)):
        return None
    delta = float(last_value) - float(first_value)
    # A negative monotonic delta indicates that the records came from
    # incompatible clock origins (or an invalidly ordered trace). Never clamp
    # this to zero: doing so hides the measurement failure.
    if delta < 0:
        return None
    return round(delta, 3)


def _delta_is_incompatible(events: dict[str, dict[str, Any]], start: str, end: str) -> bool:
    """Return whether a present interval cannot be measured safely."""

    if start not in events or end not in events:
        return False
    first = events[start]
    last = events[end]
    if not _same_clock(first, last):
        return True
    first_value = _time(first)
    last_value = _time(last)
    if not isinstance(first_value, (int, float)) or not isinstance(last_value, (int, float)):
        return True
    return float(last_value) < float(first_value)


def _same_clock(first: dict[str, Any], last: dict[str, Any]) -> bool:
    """Return whether two timestamps can be subtracted safely."""

    first_domain = first.get("clock_domain")
    last_domain = last.get("clock_domain")
    if first_domain is not None and last_domain is not None and first_domain != last_domain:
        return False
    first_process = first.get("process")
    last_process = last.get("process")
    return not (
        first_process is not None and last_process is not None and first_process != last_process
    )


def _duration_value(record: dict[str, Any]) -> float | None:
    value = record.get("duration_ms")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    value = float(value)
    if value < 0 or value > DIAGNOSTIC_DURATION_MAX_MS:
        return None
    return round(value, 3)


def _local_duration(
    records: list[dict[str, Any]],
    event_names: tuple[str, ...],
    *,
    aggregate: bool = False,
) -> float | None:
    """Read a duration calculated by its source process.

    Source-local durations are authoritative. Endpoint subtraction is only a
    compatibility fallback for historical traces that predate this contract.
    """

    values: list[float] = []
    for event_name in event_names:
        values = [
            value
            for record in records
            if record.get("event") == event_name
            for value in [_duration_value(record)]
            if value is not None
        ]
        if values:
            break
    if not values:
        return None
    return round(sum(values), 3) if aggregate else values[0]


_RESPONSE_LIFECYCLE_EVENTS = {
    "response.cancelled",
    "response_cancelled_received",
    "response_cancel_queued",
    "response_cancel_sent",
    "barge_in_confirmed",
    "barge_in_playback_stop_requested",
    "barge_in_playback_stopped",
    "tts.cancelled",
    "tts_playback_stopped",
}


def _is_response_lifecycle_event(record: dict[str, Any]) -> bool:
    event = str(record.get("event", ""))
    return event in _RESPONSE_LIFECYCLE_EVENTS or event.startswith("barge_in_")


def _tts_segment_identity(record: dict[str, Any]) -> str | None:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key in ("tts_segment_id", "segment_id", "segment_index", "chunk_id", "sequence"):
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return None


def _first_tts_segment_audio_duration(records: list[dict[str, Any]]) -> float | None:
    """Use the provider-local TTFA belonging to the first audio-producing segment."""

    candidates = [
        record
        for record in records
        if record.get("event") == "tts_first_audio_received" and _duration_value(record) is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda record: (_time(record) is None, _time(record) or math.inf))
    return _duration_value(candidates[0])


def _tts_generation_duration(records: list[dict[str, Any]]) -> float | None:
    """Use a response aggregate, or sum verified non-overlapping serial segments."""

    aggregate = _local_duration(records, ("tts_response_metrics",))
    if aggregate is not None:
        return aggregate
    starts = [record for record in records if record.get("event") == "tts_request_started"]
    ends = [record for record in records if record.get("event") == "tts_generation_completed"]
    if not starts or len(starts) != len(ends):
        return None

    def key(record: dict[str, Any]) -> float:
        timestamp = _time(record)
        return timestamp if timestamp is not None else math.inf

    starts.sort(key=key)
    ends.sort(key=key)
    total = 0.0
    previous_end: float | None = None
    for start, end in zip(starts, ends, strict=True):
        if not _same_clock(start, end):
            return None
        start_time = _time(start)
        end_time = _time(end)
        duration = _duration_value(end)
        if (
            start_time is None
            or end_time is None
            or duration is None
            or end_time < start_time
            or (previous_end is not None and start_time < previous_end)
        ):
            return None
        start_segment = _tts_segment_identity(start)
        end_segment = _tts_segment_identity(end)
        if start_segment is not None and end_segment is not None and start_segment != end_segment:
            return None
        previous_end = end_time
        total += duration
    return round(total, 3)


def correlation_errors(records: list[dict[str, Any]]) -> list[str]:
    """Reject stale, duplicate, or cross-response records explicitly."""

    errors: list[str] = []
    turn_ids = {str(record.get("turn_id")) for record in records if record.get("turn_id")}
    if len(turn_ids) > 1:
        errors.append("wrong_turn_id")
    response_ids = {
        str(record.get("response_id"))
        for record in records
        if record.get("response_id") and not _is_response_lifecycle_event(record)
    }
    if len(response_ids) > 1:
        errors.append("wrong_response_id")
    segmented_tts_events = {
        "tts_request_started",
        "tts_first_audio_received",
        "tts_generation_completed",
    }
    counts: dict[tuple[str, str | None, str], int] = {}
    playback_counts: dict[tuple[str, str | None], int] = {}
    for record in records:
        event = str(record.get("event", ""))
        response = str(record.get("response_id")) if record.get("response_id") else None
        if event in segmented_tts_events:
            segment = _tts_segment_identity(record)
            if segment is None:
                continue
            key = (event, response, segment)
            counts[key] = counts.get(key, 0) + 1
        elif event in {"tts_playback_complete", "tts_playback_completed"}:
            key = (event, response)
            playback_counts[key] = playback_counts.get(key, 0) + 1
    errors.extend(
        f"duplicate_{event}" for (event, _response, _segment), count in counts.items() if count > 1
    )
    errors.extend(
        f"duplicate_{event}" for (event, _response), count in playback_counts.items() if count > 1
    )
    for record in records:
        if "duration_ms" not in record:
            continue
        raw = record.get("duration_ms")
        if isinstance(raw, (int, float)) and (
            not math.isfinite(float(raw))
            or float(raw) < 0
            or float(raw) > DIAGNOSTIC_DURATION_MAX_MS
        ):
            errors.append(f"invalid_duration:{record.get('event')}")
    return sorted(set(errors))


def accounting_for_turn(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize call counts and provider usage without reading prompt contents."""

    request_records = []
    request_keys: set[tuple[Any, ...]] = set()
    for record in records:
        if record.get("event") != "gateway_llm_request_observed":
            continue
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict) or metadata.get("attempt") is None:
            continue  # Exclude timing-point observations from older traces.
        key = (
            record.get("response_id"),
            metadata.get("tool_round", 1),
            metadata.get("attempt"),
            metadata.get("sequence"),
            record.get("process"),
        )
        if key not in request_keys:
            request_keys.add(key)
            request_records.append(record)

    calls_by_request: dict[tuple[str, str], dict[str, Any]] = {}
    tool_rounds: set[str] = set()
    for record in request_records:
        metadata = record.get("metadata") or {}
        attempt = str(metadata.get("attempt"))
        tool_round = str(metadata.get("tool_round", 1))
        tool_rounds.add(tool_round)
        calls_by_request.setdefault(
            (tool_round, attempt),
            {
                "provider": metadata.get("provider"),
                "model": metadata.get("configured_model"),
                "usage": None,
            },
        )
    usage_by_request: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("event") == "llm_provider_usage":
            metadata = record.get("metadata") or {}
            if isinstance(metadata, dict) and metadata.get("attempt") is not None:
                usage_by_request[
                    (str(metadata.get("tool_round", 1)), str(metadata["attempt"]))
                ].append(metadata)
    for request_key, usage_items in usage_by_request.items():
        if request_key in calls_by_request:
            usage_items.sort(key=lambda item: int(item.get("sequence", -1)))
            calls_by_request[request_key]["usage"] = usage_items[-1]

    usage_complete = bool(calls_by_request) and all(
        call["usage"] is not None
        and isinstance(call["usage"].get("input_tokens"), int)
        and isinstance(call["usage"].get("output_tokens"), int)
        for call in calls_by_request.values()
    )
    input_tokens = (
        sum(call["usage"]["input_tokens"] for call in calls_by_request.values())
        if usage_complete
        else None
    )
    output_tokens = (
        sum(call["usage"]["output_tokens"] for call in calls_by_request.values())
        if usage_complete
        else None
    )
    total_values = [
        call["usage"].get("total_tokens")
        for call in calls_by_request.values()
        if call["usage"] is not None
    ]
    total_tokens = (
        sum(total_values)
        if usage_complete and all(isinstance(value, int) for value in total_values)
        else None
    )
    event_counts = Counter(str(record.get("event", "")) for record in records)
    return {
        "main_model_calls": len(request_records),
        "model_rounds": len(tool_rounds),
        "providers": sorted(
            {call["provider"] for call in calls_by_request.values() if call["provider"]}
        ),
        "models": sorted({call["model"] for call in calls_by_request.values() if call["model"]}),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "token_usage_status": (
            "provider_usage_reported" if usage_complete else "n/a — provider usage unavailable"
        ),
        "embedding_calls": event_counts["embedding_started"],
        "vector_retrieval_calls": event_counts["vector_search_started"],
        "fts_calls": event_counts["fts_started"],
        "rrf_executions": event_counts["rrf_started"],
        "reranker_calls": event_counts["rerank_started"],
        "memory_context_pipeline_calls": event_counts["memory_context_pipeline_started"],
        "tool_invocations": event_counts["tool_start"],
        "write_attempts": event_counts["tool_write_attempt"],
        "confirmation_requests": event_counts["tool_confirmation_requested"],
        "confirmed_writes": event_counts["tool_write_committed"],
        "duplicate_write_prevented": event_counts["tool_duplicate_write_prevented"],
    }


def incompatible_metric_keys(records: list[dict[str, Any]]) -> set[str]:
    """Return metric keys whose available endpoints use incompatible clocks."""

    local_metrics = {
        "stt_request_duration": (
            "stt_request_completed",
            "stt_request_duration",
            "stt_response_received",
        ),
        "memory_context_pipeline": ("memory_context_pipeline_completed",),
        "llm_ttft": ("llm_first_token_received", "llm_ttft_local"),
        "llm_total": ("llm_request_completed", "llm_completed"),
        "tts_ttfa": ("tts_first_audio_received",),
        "tts_generation_total": ("tts_generation_completed", "tts_response_metrics"),
        "playback_duration": ("tts_playback_complete", "tts_playback_completed"),
    }
    incompatible = {
        key
        for key, event_names in local_metrics.items()
        if any(record.get("event") in event_names for record in records)
        and (
            _tts_generation_duration(records) is None
            if key == "tts_generation_total"
            else _first_tts_segment_audio_duration(records) is None
            if key == "tts_ttfa"
            else _local_duration(records, event_names) is None
        )
    }
    events = _events(records)
    pairs: dict[str, tuple[tuple[str, str], ...]] = {
        "speech_duration": (("speech_start", "speech_end"),),
        "speech_end_to_stt_final": (
            ("device_speech_end", "client_stt_final_received"),
            ("speech_end", "stt_final"),
        ),
        "embedding": (("embedding_start", "embedding_end"),),
        "vector_search": (("vector_search_start", "vector_search_end"),),
        "fts": (("fts_start", "fts_end"),),
        "rrf": (("rrf_start", "rrf_end"),),
        "rerank": (("rerank_start", "rerank_end"),),
        "rag": (("rag_start", "rag_end"),),
        "llm_ttft": (("llm_request_start", "llm_first_token"),),
        "llm_total": (("llm_request_start", "llm_complete"),),
        "speech_end_to_first_token": (("device_speech_end", "first_assistant_token_received"),),
        "device_commit_to_first_text": (
            ("native_turn_commit_sent", "first_assistant_token_received"),
        ),
        "device_commit_to_first_audio": (("native_turn_commit_sent", "tts_first_chunk_received"),),
        "device_commit_to_stt_final": (("turn_commit_sent", "client_stt_final_received"),),
        "device_commit_to_playback_complete": (
            ("turn_commit_sent", "tts_playback_complete"),
            ("turn_commit_sent", "tts_playback_completed"),
        ),
        "tts_ttfa": (("tts_request_start", "tts_first_audio_chunk"),),
        "tts_generation_total": (("tts_request_start", "tts_generation_complete"),),
        "server_to_client_audio": (("tts_first_audio_chunk", "tts_first_chunk_received"),),
        "playback_buffer": (
            ("tts_first_chunk_received", "tts_playback_start"),
            ("tts_first_chunk_received", "tts_playback_started"),
        ),
        "playback_duration": (
            ("tts_playback_start", "tts_playback_complete"),
            ("tts_playback_start", "tts_playback_completed"),
            ("tts_playback_started", "tts_playback_complete"),
            ("tts_playback_started", "tts_playback_completed"),
        ),
        "speech_end_to_first_audio": (("device_speech_end", "tts_first_chunk_received"),),
        "end_to_end": (
            ("device_speech_end", "tts_playback_complete"),
            ("device_speech_end", "tts_playback_completed"),
        ),
        "backend_speech_end_to_first_text": (("speech_end", "first_assistant_text_send_started"),),
        "backend_speech_end_to_first_audio": (("speech_end", "tts_first_audio_received"),),
        "backend_complete_turn": (("speech_end", "turn_complete"),),
        "stt_final_to_orchestration": (("stt_final_received", "orchestration_started"),),
        "orchestration_to_request": (("orchestration_started", "llm_request_started"),),
        "stt_final_to_request": (("stt_final_received", "llm_request_started"),),
        "stt_final_to_first_token": (("stt_final_received", "llm_first_token"),),
        "request_to_stream_opened": (("http_request_started", "llm_stream_opened"),),
        "connection_acquisition": (("http_request_started", "llm_connection_acquired"),),
        "stream_opened_to_first_token": (("llm_stream_opened", "llm_first_token"),),
        "provider_ttft": (("http_request_started", "llm_first_token"),),
        "speech_end_to_first_assistant_audio": (("device_speech_end", "tts_first_chunk_received"),),
        "backend_speech_end_to_provider_first_token": (("speech_end", "llm_first_token"),),
    }
    incompatible.update(
        {
            key
            for key, candidates in pairs.items()
            if any(_delta_is_incompatible(events, start, end) for start, end in candidates)
            and not any(_delta(events, start, end) is not None for start, end in candidates)
        }
    )
    return incompatible


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
    memory_context_pipeline = _first_measured(
        _local_duration(records, ("memory_context_pipeline_completed",)),
        _delta(events, "rag_start", "rag_end"),
    )
    values: dict[str, float | None] = {
        "speech_duration": _delta(events, "speech_start", "speech_end"),
        "speech_end_to_stt_final": _first_measured(
            _delta(events, "device_speech_end", "client_stt_final_received"),
            _delta(events, "speech_end", "stt_final"),
        ),
        "stt_request_duration": _local_duration(
            records, ("stt_request_completed", "stt_request_duration", "stt_response_received")
        ),
        "embedding": _delta(events, "embedding_start", "embedding_end"),
        "vector_search": _delta(events, "vector_search_start", "vector_search_end"),
        "fts": _delta(events, "fts_start", "fts_end"),
        "rrf": _delta(events, "rrf_start", "rrf_end"),
        "rerank": _delta(events, "rerank_start", "rerank_end"),
        "rag": memory_context_pipeline,
        "memory_context_pipeline": memory_context_pipeline,
        "llm_ttft": _first_measured(
            _local_duration(records, ("llm_first_token_received", "llm_ttft_local")),
            _delta(events, "llm_request_start", "llm_first_token"),
        ),
        "llm_total": _first_measured(
            _local_duration(records, ("llm_request_completed", "llm_completed")),
            _delta(events, "llm_request_start", "llm_complete"),
        ),
        "speech_end_to_first_token": _delta(
            events, "device_speech_end", "first_assistant_token_received"
        ),
        "device_commit_to_first_text": _delta(
            events, "native_turn_commit_sent", "first_assistant_token_received"
        ),
        "device_commit_to_first_audio": _delta(
            events, "native_turn_commit_sent", "tts_first_chunk_received"
        ),
        "device_commit_to_stt_final": _delta(
            events, "turn_commit_sent", "client_stt_final_received"
        ),
        "device_commit_to_playback_complete": _delta_aliases(
            events,
            ("turn_commit_sent",),
            ("tts_playback_complete", "tts_playback_completed"),
        ),
        "tts_ttfa": _first_measured(
            _first_tts_segment_audio_duration(records),
            _delta(events, "tts_request_start", "tts_first_audio_chunk"),
        ),
        "tts_generation_total": _first_measured(
            _tts_generation_duration(records),
            _delta(events, "tts_request_start", "tts_generation_complete"),
        ),
        "server_to_client_audio": _delta(
            events, "tts_first_audio_chunk", "tts_first_chunk_received"
        ),
        "playback_buffer": _delta_aliases(
            events,
            ("tts_first_chunk_received",),
            ("tts_playback_start", "tts_playback_started"),
        ),
        "playback_duration": _first_measured(
            _local_duration(records, ("tts_playback_complete", "tts_playback_completed")),
            _delta_aliases(
                events,
                ("tts_playback_start", "tts_playback_started"),
                ("tts_playback_complete", "tts_playback_completed"),
            ),
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
        "backend_speech_end_to_first_text": _delta(
            events, "speech_end", "first_assistant_text_send_started"
        ),
        "backend_speech_end_to_first_audio": _delta(
            events, "speech_end", "tts_first_audio_received"
        ),
        "backend_complete_turn": _delta(events, "speech_end", "turn_complete"),
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
    speech_end_to_stt_request = _delta(events, "speech_end", "stt_request_started")
    stt_request_duration = _local_duration(
        records, ("stt_request_completed", "stt_request_duration", "stt_response_received")
    )
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
    memory_context_pipeline = _local_duration(records, ("memory_context_pipeline_completed",))
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
    provider_ttft = _first_measured(
        _local_duration(records, ("llm_first_token_received", "llm_ttft_local")),
        _delta(events, "http_request_started", "llm_first_token"),
    )
    request_to_first_token = provider_ttft
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
        "speech_end_to_stt_request": speech_end_to_stt_request,
        "stt_request_duration": stt_request_duration,
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
        "memory_context_pipeline": memory_context_pipeline,
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
        "request_to_first_token": request_to_first_token,
        "provider_to_gateway_delay": provider_to_gateway_delay,
        "first_assistant_text_send": _stage_duration(records, "first_assistant_text_send"),
        "legacy_llm_ttft": _delta(events, "llm_request_start", "llm_first_token"),
        "stt_final_to_request": stt_final_to_request,
        "llm_request_to_first_token": request_to_first_token,
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
        incompatible = incompatible_metric_keys(turn_records)
        print(f"TURN {turn_id}")
        for label, key in METRICS:
            value = values[key]
            rendered = (
                "n/a — incompatible clock domains"
                if value is None and key in incompatible
                else f"{value} ms"
                if value is not None
                else "n/a"
            )
            print(f"{label + ':':34} {rendered}")
            if value is not None:
                aggregate[key].append(value)
        ranked = sorted(
            ((label, values[key]) for label, key in METRICS if values[key] is not None),
            key=lambda item: item[1] or 0,
            reverse=True,
        )[:3]
        contributors = ", ".join(f"{label} ({value:.1f} ms)" for label, value in ranked)
        print("Largest contributors: " + contributors)
        accounting = accounting_for_turn(turn_records)
        print(
            "Call accounting: "
            f"main_model_calls={accounting['main_model_calls']} "
            f"model_rounds={accounting['model_rounds']} "
            f"providers={accounting['providers']} models={accounting['models']} "
            f"input_tokens={accounting['input_tokens']} "
            f"output_tokens={accounting['output_tokens']} "
            f"usage={accounting['token_usage_status']}"
        )
        print(
            "Retrieval/tool accounting: "
            + ", ".join(
                f"{key}={accounting[key]}"
                for key in (
                    "embedding_calls",
                    "vector_retrieval_calls",
                    "fts_calls",
                    "rrf_executions",
                    "reranker_calls",
                    "memory_context_pipeline_calls",
                    "tool_invocations",
                    "write_attempts",
                    "confirmation_requests",
                    "confirmed_writes",
                    "duplicate_write_prevented",
                )
            )
        )
        print()
        print("STT → LLM PIPELINE BREAKDOWN")
        pipeline_rows = (
            ("Speech-end → STT final", "speech_end_to_stt_final"),
            ("Speech-end → STT request", "speech_end_to_stt_request"),
            ("STT request duration", "stt_request_duration"),
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
            ("Memory context pipeline", "memory_context_pipeline"),
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
            ("LLM request → first token (provider-local)", "llm_request_to_first_token"),
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
            rendered = (
                "n/a — incompatible clock domains"
                if value is None and key in incompatible
                else f"{value:.3f} ms"
                if value is not None
                else "n/a"
            )
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
        ("Speech-end → STT request", "speech_end_to_stt_request"),
        ("STT request duration", "stt_request_duration"),
        ("Persistence", "turn_persistence"),
        ("Gateway audio-commit queue wait", "gateway_commit_queue_wait"),
        ("Context loading", "context_loading"),
        ("History loading", "history_loading"),
        ("Confirmation routing", "confirmation_routing"),
        ("Memory/RAG decision", "memory_rag_decision"),
        ("Memory context pipeline", "memory_context_pipeline"),
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
        ("LLM request → first token (provider-local)", "llm_request_to_first_token"),
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
