# Latency instrumentation fix — 2026-09-16

Status: **LATENCY INSTRUMENTATION FIX: PASS**

This change fixes measurement and event-correlation defects exposed by the failed physical five-turn smoke test. It does not change STT, retrieval, prompts, provider behavior, TTS behavior, GraphRAG flags, or latency policy. A new physical smoke test was not started.

## Root causes found

The six cross-clock violations in the historical report came from the same defect in three rejected turns: gateway lifecycle records used Python `time.monotonic()` while provider/STT/TTS trace records used `time.perf_counter_ns()`, but all records were labeled `clock_domain: backend`. The gateway also converted the provider event's legacy `LLMEvent.monotonic_seconds` into trace timestamps and subtracted it from a perf-counter timestamp. The Android records were on a separate `SystemClock.elapsedRealtimeNanos()` origin and were also not valid endpoints for server calculations. Each of the three rejected turns therefore contained two invalid accepted cross-clock calculations; the analyzer correctly rejected them rather than clamping them.

The approximately 40.6-second STT values were caused by `STTTurn.finalize` comparing a remote result timestamp produced with `perf_counter()` against a `time.monotonic()` value and selecting the larger value. On this Windows host those clocks have different origins separated by roughly 39 seconds. STT request timing is now measured only between local perf-counter timestamps in the STT HTTP process. Speech duration, speech-end to STT request, request duration, and STT-final to orchestration remain separate metrics.

The 43–54.6-second TTS totals were caused by pairing the gateway's old `tts_request_start`/`tts_generation_complete` lifecycle points with provider and Android records from different clock domains, including multiple sentence requests. Server-side TTS now emits local request, first-audio, and generation durations. Android emits local stream and playback durations. These are separate metrics and are never subtracted across processes.

The historical “Complete RAG” value was the memory policy/retrieval/no-result wrapper. It was renamed to `memory_context_pipeline`; actual embedding, vector, FTS, RRF, and reranking stages remain `n/a` unless they execute and emit their own local stage records.

The analyzer keeps the historical LLM TTFT field for old traces, but the acceptance metric is now the provider-local HTTP TTFT. The gateway's old monotonic lifecycle points are correlation observations only and are never used as provider duration endpoints.

## Timing contract

| Metric | Source process | Clock domain | Calculation |
| --- | --- | --- | --- |
| Speech duration | Android | `android_elapsed_realtime` | Android-local speech-start/end endpoints |
| STT request duration | Backend STT adapter | `backend_python_perf_counter` | Source-emitted `stt_request_completed.duration_ms` |
| STT-final → orchestration | Backend gateway | `backend_python_perf_counter` | Backend-local endpoint delta |
| Persistence | Backend gateway/database | `backend_python_perf_counter` | `latency_span` source-local duration |
| Memory context pipeline | Backend gateway/memory service | `backend_python_perf_counter` | Source-local wrapper duration; retrieval callbacks expose executed stages |
| Embedding/vector/FTS/RRF/reranking | Backend retrieval/provider callbacks | `backend_python_perf_counter` | Source-local stage duration when the stage runs; otherwise `n/a` |
| LLM TTFT | Backend provider adapter | `backend_python_perf_counter` | `llm_first_token_received.duration_ms` from HTTP request start |
| LLM total | Backend provider adapter | `backend_python_perf_counter` | `llm_request_completed.duration_ms` from HTTP request start |
| Provider preparation | Backend provider adapter | `backend_python_perf_counter` | `provider_prepare_started/completed` span |
| TTS TTFA | Backend TTS adapter | `backend_python_perf_counter` | `tts_first_audio_received.duration_ms` |
| TTS generation | Backend TTS adapter | `backend_python_perf_counter` | `tts_generation_completed.duration_ms` |
| Android TTS stream/playback | Android | `android_elapsed_realtime` | Android-local durations |
| Speech-end → first token | Cross-process | incompatible | `n/a — incompatible clock domains` |
| Speech-end → playable audio | Cross-process | incompatible | `n/a — incompatible clock domains` |

Every emitted record now carries `session_id`, `turn_id`, `response_id` where applicable, `process`, `clock_domain`, `monotonic_ns`, `timestamp`, `wall_time_utc`, and a source-local `duration_ms` when the source can calculate one safely. Negative or non-finite durations are preserved for rejection; they are never converted to zero.

## Correlation and analyzer changes

- Gateway observations are explicitly named `gateway_*_observed` and are correlation-only. Provider-owned local LLM/TTS events are the duration endpoints.
- The provider emits both the repository's `*_started`/`*_completed` canonical names and the timing-contract aliases `llm_request_start`/`llm_request_complete`; all four carry the same provider-local perf-counter origin.
- The collector rejects stale response IDs and reports duplicate/stale TTS events. The analyzer validates turn/response consistency and local duration bounds.
- Optional cross-process metrics are allowed to be `n/a`; a turn is not rejected solely because an end-to-end metric cannot be measured without clock synchronization.
- `analyze_latency.py` reports STT request duration, memory context pipeline, provider-local TTFT/request durations, and an explicit unaccounted residual.

## Files changed

- Backend source timing: `backend/app/services/latency_trace.py`, `backend/app/websocket/gateway.py`, `backend/app/stt/remote_engine.py`, `backend/app/stt/service.py`, `backend/app/stt/whisper_engine.py`, `backend/app/stt/windows_engine.py`, `backend/app/llm/providers/openai_chat.py`, `backend/app/tts/remote.py`, `backend/app/tts/service.py`.
- Collector/analyzer: `backend/scripts/live_latency.py`, `backend/scripts/analyze_latency.py`.
- Android/React Native trace contract: `frontend/android/app/src/main/java/com/voiceaipoc/voice/VoiceWebSocketTransport.kt`, `frontend/src/voice/VoiceSocket.ts`, `frontend/src/voice/latencyTrace.ts`.
- Timing tests: `backend/tests/test_latency_trace.py`, `backend/tests/test_latency_analyzer.py`, `backend/tests/test_remote_stt_engine.py`, `backend/tests/test_llm_nvidia.py`, `backend/tests/test_tts.py`, `backend/tests/test_tts_pacing.py`.

## Validation

- Latency/provider/gateway/STT/TTS tests: **49 passed, 10 skipped, 1 warning**.
- Collector/analyzer correlation tests: **17 passed**.
- Phase 6 focused tests: **28 passed, 16 skipped, 1 warning**.
- PostgreSQL-backed Phase 6 integration run with `RUN_INTEGRATION_TESTS=1`: **15 passed, 1 skipped, 1 warning**. The one skip is the disposable migration test, gated separately by `RUN_GRAPH_MIGRATION_TESTS=1`.
- Full backend suite: **305 passed, 53 skipped, 1 warning**.
- Frontend Jest: **70 passed, 15 suites**.
- Frontend TypeScript typecheck: **passed**.
- Android `TtsAudioPlayerTest`: **Gradle build successful**.
- Ruff check on backend source/scripts/tests: **passed**.
- Ruff format check on all modified backend files: **18 files already formatted**.
- Prettier check on modified frontend trace files: **passed**. The repository-wide frontend formatter still reports three pre-existing unrelated files; they were not changed for this instrumentation task. The repository-wide backend formatter reports one pre-existing unrelated `backend/app/api/tasks.py` layout issue; it was not changed.
- Python compileall: **passed**.

The historical trace remains evidence of the old defect. It must not be used as a new baseline. The next physical run may proceed only with the corrected source-local events and the collector started from a clean logcat boundary.
