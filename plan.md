# LangGraph decision-router integration plan

**Status:** proposed; no router implementation has started.  
**Reviewed:** 2026-09-23 against the current working tree, `implementation.md`, and the 13-page `LangGraph_Router_Integration_Proposal_Voice_Assistant.pdf` in `C:\Users\lenovo\Downloads`.  
**Scope:** route a **final STT transcript** to existing backend services. Do not move microphone capture, wake word, VAD, STT streaming, WebSocket ownership, TTS playback, or barge-in into LangGraph. This is an addendum to `implementation.md`, not a replacement for its unfinished acceptance gates.

## Verified starting point and proposal corrections

| Area | Current project status | Planning consequence |
| --- | --- | --- |
| Turn orchestration | `backend/app/websocket/gateway.py` sends final STT, resolves pending spoken confirmation, then normally calls `_stream_llm_response`. That method currently retrieves memory context before the main model when memory injection is enabled. | Insert the router **after final STT and after cancellation checks**, at the existing orchestration boundary. Preserve one gateway-owned response/TTS path. |
| Existing “graph” package | `backend/app/graph/` is the project's GraphRAG implementation, not LangGraph; `langgraph` is not in `backend/pyproject.toml` or the frozen `requirements.txt`. | Put the new workflow under a distinct `backend/app/routing/` package. Pin and test the new dependency; do not rename GraphRAG. |
| Active provider | This local `.env` selects `LLM_PROVIDER=openai`; `/ready` reports an enabled OpenAI Responses adapter, not NVIDIA. Readiness says `live_verified=false` and `structured_text_output=false`. The provider abstraction also supports NVIDIA and others. | Make routing provider-neutral. Validate router output with Pydantic even if a provider has no native structured-output capability. Do not hard-code the PDF's NVIDIA assumption. |
| Memory | Hybrid structured/FTS/dense retrieval, RRF, reranking, ownership checks, and `MemoryRetrievalResult` exist. Local configuration is `MEMORY_RETRIEVAL_MODE=inject`; `/ready` reports embedding and reranker healthy. GraphRAG is separately configured `off`. | Reuse retrieval, but call it only on an approved memory route. Expose bounded evidence/provenance for the second-stage decision; the current formatted context string is not sufficient for safe direct answers. Respect `off`/`shadow`/`inject`, user opt-out, and provider-degraded states. |
| Tools and confirmation | Time/date, tasks, reminders, and memory tools are registered through `ToolExecutor`, which enforces Pydantic arguments, scopes, confirmation, rate limits, audit, and idempotency. Spoken pending Yes/No is already resolved before the LLM with `RedisVoiceConfirmationStore`. Recent uncommitted device-time and clock-answer work is present. | Reuse these boundaries; do not implement a second confirmation or write path. Time/date still need a **pre-main-LLM** direct route to realize the PDF's cost/latency claim. |
| Structured reads | `list_tasks` filters status; `list_reminders` filters status/upcoming. Neither tool currently has a bounded device-local day window for “tomorrow” or “due next” semantics. | Add or reuse owner-scoped read services and precise date-range arguments before enabling those direct routes. |
| Voice acceptance | Backend gateway/cancellation and native barge-in paths exist, but the latest physical loudspeaker test did **not** interrupt audible TTS (`no_safe_acoustic_path`). Phase 8/9 physical acceptance in `implementation.md` remains open. | Treat this as a known baseline defect, not something LangGraph fixes. Router rollout must not worsen voice behavior, and cannot claim a clean physical barge-in pass until that separate issue is resolved. |

Read-only verification for this review: `/health` and `/ready` returned healthy; **85 targeted backend tests passed** (`test_llm_tool_loop`, `test_voice_confirmation`, `test_phase6_memory`, `test_llm_voice_gateway`, `test_voice_barge_in_lifecycle`). The working tree already contains unrelated/uncommitted edits; preserve them during implementation. No full build or physical test was run for this planning task.

## Target boundary

```text
final STT transcript + authenticated turn context
    -> existing pending-confirmation / cancellation checks
    -> LangGraph per-turn decision (rules -> optional semantic classifier)
    -> exactly one selected path:
       direct tool | structured read | memory retrieval/evaluation |
       existing action proposal/confirmation path | general LLM
    -> gateway-owned text events, TTS queue, persistence, completion
```

The graph should return a validated outcome to the gateway. It must not own the WebSocket, audio buffers, live DB session across a pause, or independent TTS emission. Use minimal per-turn state (IDs, normalized transcript, decision, evidence references, outcome/error), with authenticated services and cancellation passed as runtime dependencies. Keep the existing `session_id`/`turn_id`/`response_id` on all events. A spoken “stop” **after** STT is a route; stopping speech **during** TTS remains the Android/client barge-in lifecycle.

## Step-by-step TODOs and gates

### 0. Freeze contracts and acceptance baseline

- [ ] Record current working-tree revision/configuration without copying secrets; inventory gateway event order, tool schemas, confirmation TTL/scope, memory opt-out, cancellation, and TTS behavior.
- [ ] Build a versioned labeled corpus from PDF pages 8–9 and 11–13 plus real false positives: current time vs medicine time, today's date vs meeting date, Yes with/without pending action, missing memory, general knowledge, and ambiguous actions. Include multi-intent, multilingual, malformed STT, and cancel/retry cases.
- [ ] Measure baseline per-route model calls, retrieval calls, speech-end-to-first-text/audio, P50/P95 turn latency, and tool-write safety before cutover. Record the known physical barge-in failure separately.
- [ ] Decide and document the initial flag values, canary cohort, rollback owner, and provisional performance budgets before enabling a new route.

**Gate:** a reproducible corpus and before-change metrics exist; critical safety cases are explicitly labeled.

### 1. Add the router foundation with no behavior change

- [ ] Add and pin a compatible `langgraph` version in `backend/pyproject.toml` and the frozen `requirements.txt`; verify Python 3.12 compatibility in this environment. Do not add LangChain model wrappers unless a tested adapter truly requires them.
- [ ] Create `backend/app/routing/` with a small compiled `StateGraph`, explicit conditional edges, per-turn state, runtime context, and a Pydantic `RouteDecision` contract. Suggested routes: `CONTROL`, `DIRECT_TOOL`, `STRUCTURED_READ`, `MEMORY_QUERY`, `TASK_ACTION`, `GENERAL_LLM`; reserve `CONFIRMATION` for a later explicit migration because pending Yes/No is already handled before graph dispatch.
- [ ] Add a router service interface and settings (`off`, `shadow`, `canary`, `on` plus deterministic cohort/timeout limits). Default to `off`. Keep the current orchestrator callable.
- [ ] Do not add a LangGraph checkpointer or graph-owned approval state in v1; the existing Redis confirmation store remains authoritative.
- [ ] Unit-test graph compilation, allowed edges, invalid/unknown decisions, timeouts, cancellation, and feature-flag fallback before any live dispatch.

**Gate:** `off` preserves existing route choices and user-visible protocol behavior; no new model or tool call occurs.

### 2. Implement precedence and safe deterministic rules

- [ ] Check response/session cancellation and an authenticated pending confirmation **before** ordinary intent classification; reuse the existing Yes/No resolver and scope rules.
- [ ] Match only narrow, high-confidence direct time/date and control utterances. Explicitly exclude stored schedule questions (“What time do I take medicine?”, “What date is my meeting?”) and ambiguous “stop/cancel” referents.
- [ ] Validate route, target tool, confidence/source, and bounded arguments with Pydantic; reject invented tools, invalid fields, excessive text, and unsafe mixed intents.
- [ ] For uncertain cases return an explicit fallback/needs-classification decision rather than guessing; preserve the current path only **before** side effects.
- [ ] Test all precedence and false-positive cases with a frozen clock and device timezone.

**Gate:** zero critical time/date, confirmation, or write-action misroutes in the acceptance corpus.

### 3. Run safe shadow mode and compare decisions

- [ ] Invoke the graph for sampled completed STT turns without changing user-visible responses. In shadow, never execute writes, send TTS, or duplicate full LLM/RAG calls just to obtain a comparison.
- [ ] Emit privacy-safe per-turn decision telemetry: route/target/source, validation/fallback reason, latency, legacy-vs-shadow disagreement, and the existing IDs. Avoid raw transcript, tool secrets, or personal memory content in routine metrics.
- [ ] Review disagreements by category and update rules/corpus before canary. Bound classifier latency and shadow load; honor memory opt-out even in shadow.

**Gate:** all critical corpus cases agree with the expected safe route, and shadow adds no user-visible events or mutations.

### 4. Cut over direct utilities and existing confirmation

- [ ] Call registered `get_current_time`/`get_current_date` through the existing `ToolExecutor` and authenticated `ToolExecutionContext`, not by bypassing scopes/audit. Extract the existing successful clock-result formatter for reuse instead of duplicating its logic.
- [ ] Keep pending Yes/No in the **existing pre-router** `RedisVoiceConfirmationStore` flow; Yes without a pending item must not execute a tool. Keep `client.confirmation.resolve` semantics unchanged.
- [ ] Return one bounded text outcome through the gateway's existing text/TTS/persistence/completion helpers, with monotonic sequence numbers and the same turn IDs. Avoid an unnecessary “thinking” phrase or first main-LLM request on direct routes.
- [ ] Preserve explicit-timezone and advancing device-clock behavior from the current in-flight clock fix.
- [ ] Add integration tests for direct reply, tool failure, expired confirmation, replay, cancellation, WebSocket event order, and exactly one final response.

**Gate:** time/date and pending approval paths use **zero RAG calls and zero main-LLM calls**; writes still require confirmation and remain idempotent.

### 5. Add authoritative structured reads

- [ ] Extend shared owner-scoped task/reminder read services or tool arguments with bounded due/trigger windows and “next” ordering. Convert “today/tomorrow” using the validated device IANA timezone and UTC bounds; cover DST transitions.
- [ ] Route schedule questions to DB reads, not current-time tools or semantic memory. Preserve explicit ownership, status filtering, pagination/limits, and permission checks.
- [ ] Format concise deterministic answers for empty, one, and multiple results; distinguish “nothing scheduled” from DB/tool unavailable.
- [ ] Test today/tomorrow/next queries, medicine-time false positives, cross-user isolation, and provider/DB failures.

**Gate:** structured-read examples answer from authoritative rows with no RAG/main LLM; no cross-user data is returned.

### 6. Route memory selectively, then evaluate evidence

- [ ] Invoke `MemoryRetrievalService.retrieve` only for `MEMORY_QUERY`, subject to current memory mode, per-user enablement, session exclusion, and cancellation. Leave GraphRAG `off` unless separately accepted.
- [ ] Expose a bounded internal evidence result (memory IDs, source, rank/score, type, recency, status, conflicts/provenance) before `assemble_context`; do not infer exactness from the formatted context string or treat an RRF/rerank score as a probability.
- [ ] Add a deterministic second-stage evaluator: `DIRECT_RAG` only for a single supported, non-conflicting exact fact; `RAG_PLUS_LLM` for synthesis/comparison; `NO_RESULT` when evidence is absent. Distinguish no result from disabled/degraded retrieval and abstain safely on outages.
- [ ] Produce direct memory answers with attribution to the retrieved record internally, bounded formatting, and no unsupported personal claim. For synthesis, pass only selected evidence to the existing provider-neutral main LLM.
- [ ] Test exact, conflicting, superseded, temporal, deleted, cross-user, no-result, low-score, reranker-outage, and memory-disabled cases; calibrate thresholds on a labeled corpus before enabling direct answers.

**Gate:** missing personal facts never fall through to a guessing general LLM; direct answers have verifiable evidence; disabled/failed retrieval is not misreported as “you have no memory.”

### 7. Make general LLM and action paths selective

- [ ] Ensure `GENERAL_LLM` skips memory embedding, FTS, vector search, RRF, reranker, and graph retrieval while retaining conversation history and the existing provider adapter. Personalized questions must not be mislabeled general.
- [ ] For `TASK_ACTION`, initially reuse the current tool-loop proposal/extraction path and `ToolExecutor` confirmation, authorization, audit, transaction, and idempotency boundaries. Add a separate validated extractor only if it improves measured latency without weakening safety.
- [ ] Never treat route confidence as permission. Route fallback is forbidden after an action is claimed/executed; preserve exactly-once behavior on retry, disconnect, and resume.
- [ ] Test provider-specific behavior against the active OpenAI adapter and at least one other configured adapter before claiming provider-neutral release readiness.

**Gate:** general questions issue zero RAG calls; no task/reminder mutation occurs before approval or more than once.

### 8. Add a small semantic classifier only if needed

- [ ] First measure deterministic-rule coverage and disagreement. If needed, add a separately configured fast classifier for ambiguous turns with bounded input, tokens, deadline, and cost; do not call it for obvious direct/pending-confirmation requests.
- [ ] Parse and Pydantic-validate the classifier's proposed route independently of provider-native structured-output support; allow-list targets and fall back safely on malformed, timed-out, or low-confidence output.
- [ ] Benchmark classification accuracy and incremental latency against the frozen corpus and the current provider/model, including adversarial prompt-injection text from STT.

**Gate:** classifier improves ambiguous-route accuracy within an agreed latency/cost budget and cannot authorize tools.

### 9. Roll out, measure, and keep rollback simple

- [ ] Enable canary by stable authenticated cohort, then widen only after reviewing route disagreement, model/retrieval call counts, P50/P95 latency, STT-to-first-audio, response cancellation, stale TTS frames, and writes.
- [ ] Require 100% pass on critical intent distinctions, zero unconfirmed/duplicate writes, zero cross-user leakage, grounded memory no-result behavior, and no WebSocket/TTS protocol regression. Set numeric non-safety latency targets from Step 0 before canary; deterministic routing should not add a network round trip.
- [ ] Verify both `off` rollback and an in-flight canary revert without changing persisted task/reminder/confirmation ownership. Document degraded dependency behavior and on-call diagnostics.
- [ ] Run a physical voice regression on the same device after the router cutover, but track the existing loudspeaker barge-in failure as a separate baseline defect; do not attribute or “pass” it based on graph-only tests.

**Gate:** canary acceptance report and rollback drill pass before `on` becomes the default.

### 10. Optional later phase: native LangGraph confirmation state

- [ ] Only after Steps 0–9 are stable, decide whether graph `interrupt()`/`Command(resume=...)` materially improves the existing Redis workflow. Do **not** maintain two authoritative pending approvals.
- [ ] If migrating, choose a durable checkpointer, scoped stable `thread_id`, expiry/ownership model, checkpoint retention, schema migration, and restart recovery; never serialize live DB sessions or secrets into graph state.
- [ ] Make every side effect idempotent on node replay, since interrupted/retried nodes can re-execute. Preserve existing tool idempotency keys and authorization at execution time.
- [ ] Prove Yes/No, duplicate resume, expiry, reconnect, crash between approval and commit, cross-user replay, and rollback against the old confirmation store.

**Gate:** an explicit migration design and separate acceptance report; this phase is **not required** for the initial router release.

## Proposed implementation touch points

- New: `backend/app/routing/{models,rules,graph,service,evaluator,telemetry}.py` (names may be refined after the contract test).
- Existing integration: `backend/app/websocket/gateway.py`, `backend/app/core/config.py`, `backend/app/llm/tool_loop.py`, `backend/app/llm/task_tools.py`, `backend/app/llm/reminder_tools.py`, `backend/app/memory/retrieval.py` and `backend/app/memory/types.py` only where the selected step requires it.
- Dependency lock: `backend/pyproject.toml` and root `requirements.txt` together.
- Tests: new router corpus/unit tests plus existing gateway, confirmation, tool, memory, clock, cancellation, and physical voice regressions. Frontend/Android changes are **not** part of the initial router integration.

## References

- Supplied proposal: `C:\Users\lenovo\Downloads\LangGraph_Router_Integration_Proposal_Voice_Assistant.pdf`, pages 2–13.
- Project baseline: [`implementation.md`](implementation.md), especially Phases 5–9; current source paths above and the 2026-09-23 work notes in `docs/`.
- Official LangGraph documentation: [Graph API and conditional edges](https://docs.langchain.com/oss/python/langgraph/graph-api), [interrupts and resume](https://docs.langchain.com/oss/python/langgraph/interrupts), [persistence and thread IDs](https://docs.langchain.com/oss/python/langgraph/persistence). The documentation notes that checkpointed nodes can re-execute after resume, so side effects must remain idempotent.
