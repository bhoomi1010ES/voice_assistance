# Phase 3 — live shadow comparison

This is the execution plan for the next phase after the explicit Phase 0 owner
manual override. It is a fresh acceptance plan; it does not replace or alter
any earlier Phase 0 artifacts.

## Objective

Observe LangGraph decisions for completed STT turns while the legacy response
path remains authoritative. Shadow must never send a user-visible event, call
TTS, execute a tool, mutate a database, or repeat a full LLM or retrieval
pipeline solely for comparison.

## Preconditions

1. Confirm `phase_gate_overrides.json` evaluates
   `phase0_effective_pass = automated_phase0_pass || owner_manual_override`.
2. Keep the disposable test account and the existing authenticated gateway.
3. Verify `/health` and `/ready`, including PostgreSQL, Redis, LLM, embedding,
   reranker, and TTS readiness.
4. Verify the WebSocket heartbeat and normal legacy turn completion. A phone is
   needed for a physical run; a completed STT sample source may be used for a
   controlled non-acoustic shadow run.
5. Start with `ROUTER_MODE=off` and cohort `0`. Change to `shadow` only for the
   approved sample, with a stable authenticated cohort and bounded timeout.
6. Do not enable `canary` or `on` during this phase.

## Sample

Use a fresh labeled sample of completed turns covering:

- current time and current date, including schedule false positives;
- task and reminder structured reads;
- exact memory hit, missing memory, conflict, and memory-disabled cases;
- general knowledge and conversational questions;
- task/reminder and memory action proposals with confirmation and cancellation;
- multilingual, malformed STT, prompt-injection text, and mixed/ambiguous
  requests.

Record only case IDs and expected labels in the test manifest. Do not put raw
transcripts, memory content, credentials, or tool arguments in routine shadow
telemetry.

## Run procedure

1. Set the approved shadow mode, cohort, and timeout, then restart/reload only
   the backend process if settings are not hot-reloaded.
2. Complete one turn at a time and wait for the normal final response. The
   legacy path owns all text, TTS, persistence, confirmation, and completion
   events.
3. Verify each turn has exactly one legacy response and no shadow WebSocket or
   TTS events.
4. For action cases, verify pending confirmation remains in the existing Redis
   store and that shadow creates zero writes and zero tool calls.
5. Stop the sample if a heartbeat, response ordering, cancellation, or privacy
   violation occurs.

## Required telemetry

For each sampled turn emit a privacy-safe `router.shadow.observation` record
containing session/turn/response IDs, shadow route and target, decision source,
confidence, validation/fallback status, bounded latency, legacy route, and a
disagreement category. Include a reason for skips, timeout, cancellation, or
memory opt-out. Never include transcript text, personal memory content, secrets,
or executable write arguments.

## Acceptance gate

Phase 3 passes only when the fresh run provides:

- the planned sample and complete observation records;
- 100% agreement for critical time/date, confirmation, ownership, and write
  safety distinctions, or a reviewed disposition for every disagreement;
- zero shadow tool calls, writes, duplicate writes, user-visible events, TTS
  frames, cross-user results, and confirmation-state changes;
- bounded shadow latency and concurrency within the configured budget, with no
  measurable WebSocket, cancellation, or stale-TTS regression;
- category-level disagreement review and any corpus/rule updates recorded;
- a signed run report with the recommendation for the next phase.

Until these outputs exist, Phase 3 remains **NOT PASSED** even though its Phase
0 dependency is satisfied by owner override.

## Rollback

Set `ROUTER_MODE=off` and `ROUTER_COHORT_PERCENT=0`, reload the backend if
needed, verify `/health` and `/ready`, run gateway smoke tests, and verify that
Redis confirmation and task/reminder writes remain unchanged. Record the run and
keep shadow disabled until any failure is reviewed.
