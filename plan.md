# LangGraph decision-router integration plan

**Status:** Phase 1 foundation implemented with gateway integration still off; no routing behavior has been cut over.
**Plan reconciliation:** 2026-09-24 against `docs/pre_langgraph_architecture_audit.md` and the review comments supplied with this plan. The recorded repository snapshot below is audit-time evidence; re-check HEAD, working tree, configuration, tests, and service health immediately before implementation.
**Scope:** route a **final STT transcript** to existing backend services. Do not move microphone capture, wake word, VAD, STT streaming, WebSocket ownership, TTS playback, or barge-in into LangGraph. This is an addendum to `implementation.md`, not a replacement for its unfinished acceptance gates.

## Verified starting point and proposal corrections

| Area | Current project status | Planning consequence |
| --- | --- | --- |
| Turn orchestration | `backend/app/websocket/gateway.py` sends final STT, resolves pending spoken confirmation, then normally calls `_stream_llm_response`. That method currently retrieves memory context before the main model when memory injection is enabled. | Insert the router **after final STT and after cancellation checks**, at the existing orchestration boundary. Preserve one gateway-owned response/TTS path. |
| Existing “graph” package | `backend/app/graph/` is the project's GraphRAG implementation, not LangGraph; `langgraph` is not in `backend/pyproject.toml` or the frozen `requirements.txt`. | Put the new workflow under a distinct `backend/app/routing/` package. Pin and test the new dependency; do not rename GraphRAG. |
| Active provider | This local `.env` selects `LLM_PROVIDER=openai`; `/ready` reports an enabled OpenAI Responses adapter, not NVIDIA. Readiness says `live_verified=false` and `structured_text_output=false`. The provider abstraction also supports NVIDIA and others. | Make routing provider-neutral. Validate router output with Pydantic even if a provider has no native structured-output capability. Do not hard-code the PDF's NVIDIA assumption. |
| Memory | Hybrid structured/FTS/dense retrieval, RRF, reranking, ownership checks, and `MemoryRetrievalResult` exist. Local configuration is `MEMORY_RETRIEVAL_MODE=inject`; `/ready` reports embedding and reranker healthy. GraphRAG is separately configured `off`. | Reuse retrieval, but call it only on an approved memory route. Expose bounded evidence/provenance for the second-stage decision; the current formatted context string is not sufficient for safe direct answers. Respect `off`/`shadow`/`inject`, user opt-out, and provider-degraded states. |
| Tools and confirmation | Time/date, tasks, reminders, and memory tools are registered through `ToolExecutor`, which enforces Pydantic arguments, scopes, confirmation, rate limits, audit, and idempotency. Spoken pending Yes/No is already resolved before ordinary routing with `RedisVoiceConfirmationStore`. The audit records the device-time and clock-answer changes as committed at `41a963be75bea5e0fe83ea106dfd4a6bdc184bac`; re-check the snapshot before implementation. | Reuse these boundaries; do not implement a second confirmation or write path. Time/date still need a **pre-main-LLM** direct route to realize the PDF's cost/latency claim. Explicit `memory_save`/`memory_forget` needs its own `MEMORY_ACTION` route or an explicitly preserved and tested existing bypass. |
| Structured reads | `list_tasks` filters status; `list_reminders` filters status/upcoming. Neither tool currently has a bounded device-local day window for “tomorrow” or “due next” semantics. | Add or reuse owner-scoped read services and precise date-range arguments before enabling those direct routes. |
| Voice acceptance | Backend gateway/cancellation and native barge-in paths exist, but the latest physical loudspeaker test did **not** interrupt audible TTS (`no_safe_acoustic_path`). Phase 8/9 physical acceptance in `implementation.md` remains open. | Treat this as a known baseline defect, not something LangGraph fixes. Router rollout must not worsen voice behavior, and cannot claim a clean physical barge-in pass until that separate issue is resolved. |

Audit-time verification: `/health` and `/ready` returned healthy and 85 targeted backend tests passed in the earlier plan review. The later architecture audit reran project gates and found **369 backend tests passed, 1 failed, and 56 skipped**, plus frontend typecheck/lint/format failures and backend Ruff failures (details in audit §23). The reproducible backend failure is `test_llm_context.py::test_informational_and_ordinary_voice_intent_remains_auto[How do I create a task?]`. The audit records HEAD `41a963be75bea5e0fe83ea106dfd4a6bdc184bac` and a clean worktree after its snapshot commit. Re-run relevant checks immediately before implementation; do not treat the earlier 85-test result as the current project gate.

## Target boundary

```text
final STT transcript + authenticated turn context
    -> existing pending-confirmation / cancellation checks
    -> LangGraph per-turn decision (rules -> optional semantic classifier)
    -> exactly one selected path:
       direct tool | structured read | memory retrieval/evaluation |
       memory action | task/reminder action | general LLM | mixed/ambiguous clarification
    -> gateway-owned text events, TTS queue, persistence, completion
```

The graph should return a validated outcome to the gateway. It must not own the WebSocket, audio buffers, live DB session across a pause, or independent TTS emission. Use minimal per-turn state (IDs, normalized transcript, decision, evidence references, outcome/error), with authenticated services and cancellation passed as runtime dependencies. Keep the existing `session_id`/`turn_id`/`response_id` on all events. A spoken “stop” **after** STT is a route; stopping speech **during** TTS remains the Android/client barge-in lifecycle. V1 does not decompose mixed requests into independently executable branches: mark them `MIXED_AMBIGUOUS` and clarify or use a single safe existing fallback. No mixed request may bypass per-action confirmation.

## Step-by-step TODOs and gates

### 0. Freeze contracts and acceptance baseline

- [ ] Record current working-tree revision/configuration without copying secrets; inventory gateway event order, tool schemas, confirmation TTL/scope, memory opt-out, cancellation, and TTS behavior.
- [ ] Build a versioned labeled corpus from PDF pages 8–9 and 11–13 plus real false positives: current time vs medicine time, today's date vs meeting date, Yes with/without pending action, missing memory, general knowledge, and ambiguous actions. Include multi-intent, multilingual, malformed STT, and cancel/retry cases.
- [ ] Measure baseline per-route router/main-model calls and rounds, main-model input/output tokens, embedding/reranker calls, retrieval calls, speech-end-to-first-text/audio, P50/P95 turn latency, and tool-write safety before cutover. Record monetary cost per 100 turns only if provider pricing is verified. Record the known physical barge-in failure separately.
- [ ] Decide and document the initial flag values, canary cohort, rollback owner, and provisional performance budgets before enabling a new route.
- [ ] Record current project test/build/lint failures and their disposition. The known backend routing failure must be triaged before foundation/shadow work; relevant project-level failures must be fixed or explicitly dispositioned before production canary.
- [ ] Include the known raw-transcript/time-resolution logging privacy issue in the pre-canary disposition; router telemetry itself must not add transcript or personal-memory content.

**Progress (2026-09-23 baseline capture; reconciled 2026-09-24):** The recorded revision/configuration and gateway/tool/confirmation/memory/cancellation/TTS inventory are in [`docs/20260923_1739_phase0_router_acceptance_baseline.md`](docs/20260923_1739_phase0_router_acceptance_baseline.md). A 24-case labeled draft corpus is in [`docs/phase0_router_acceptance_corpus_v1.json`](docs/phase0_router_acceptance_corpus_v1.json); add explicit `memory_save`/`memory_forget`, mixed-intent policy, and decided “remind me” semantics before freezing it. Proposal pages 8–9 and 11–13 remain to be reconciled. Current valid route-level call/token/retrieval/latency/write-safety measurements are unavailable; existing physical latency evidence has incompatible clock domains. The initial router mode is off; cohort and named rollback owner remain unassigned. The known backend routing false positive was reproduced and triaged during Phase 1 but remains unfixed; other backend/frontend lint/type/format gates and the transcript-logging privacy issue remain open for disposition.

**Gate: NOT PASSED.** Freeze the corpus only after source reconciliation and the added safety cases; capture current route-labeled call/token/retrieval/latency/write-safety baselines and physical barge-in status; triage the known backend route-test failure; record privacy/test-gate dispositions; assign the canary cohort and named rollback owner; then set absolute performance budgets before enabling a new route.

### 1. Add the router foundation with no behavior change

- [x] Add and pin `langgraph==1.2.12` in `backend/pyproject.toml` and the frozen `requirements.txt`; installed and verified on Python 3.12.10. No LangChain model wrappers were added.
- [x] Create `backend/app/routing/` with a compiled `StateGraph`, explicit conditional edges, per-turn state, ephemeral runtime context, and Pydantic `RouteDecision` contract. Routes: `CONTROL`, `DIRECT_TOOL`, `STRUCTURED_READ`, `MEMORY_QUERY`, `MEMORY_ACTION`, `TASK_ACTION`, `GENERAL_LLM`, and non-executable `MIXED_AMBIGUOUS`. Graph-owned `CONFIRMATION` remains reserved for a later explicit migration.
- [x] Add router service/settings (`off`, `shadow`, `canary`, `on`) with stable authenticated-user cohort selection and timeout. Default to `off`; gateway integration remains absent, so the current orchestrator is untouched.
- [x] Do not add a LangGraph checkpointer or graph-owned approval state in v1; the existing Redis confirmation store remains authoritative.
- [x] Unit-test graph compilation, allowed edges, invalid/unknown decisions, timeouts, cancellation, and feature-flag fallback before any live dispatch.

**Phase 1 implementation note (2026-09-23):** [`docs/20260923_1819_langgraph_router_foundation.md`](docs/20260923_1819_langgraph_router_foundation.md). Router remains off and is not called by the gateway. Full backend suite: 394 passed, 1 failed (the previously triaged `How do I create a task?` routing false positive), 56 skipped. The Phase 1 tests passed; see work note for exact checks.

**Gate: PASS for foundation-only `off` mode.** The gateway does not invoke the router, the service returns before compiling/invoking the graph, and the graph has no model/tool side effects. Shadow/canary/on behavior is not connected to voice turns.

### 2. Implement precedence and safe deterministic rules

- [ ] Check response/session cancellation and an authenticated pending confirmation **before** ordinary intent classification; reuse the existing Yes/No resolver and scope rules.
- [ ] Match only narrow, high-confidence direct time/date and control utterances. Explicitly exclude stored schedule questions (“What time do I take medicine?”, “What date is my meeting?”) and ambiguous “stop/cancel” referents.
- [ ] Validate route, target tool, confidence/source, and bounded read arguments with Pydantic; reject invented tools, invalid fields, excessive text, and unsafe mixed intents. For `TASK_ACTION` and `MEMORY_ACTION`, the router returns only the route/action domain and decision source; it never generates executable write arguments such as titles, dates, memory content, or IDs.
- [ ] For uncertain or multi-intent cases return explicit `MIXED_AMBIGUOUS`/needs-clarification rather than guessing or decomposing the utterance into multiple executable branches. Any existing fallback is permitted only before side effects and must retain confirmation for every write.
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
- [ ] Ensure `TASK_ACTION` skips RAG by default; initially reuse the existing action extraction/proposal path and `ToolExecutor` confirmation, authorization, audit, transaction, and idempotency boundaries. Add a separate validated extractor only if it improves measured latency without weakening safety.
- [ ] Route `MEMORY_ACTION` to the existing `memory_save`/`memory_forget` proposal and confirmation path; preserve scopes, user opt-out/exclusion, audit, and idempotency. Do not run RAG for the action itself, and do not create a second memory-write mechanism. Until this route is implemented, explicitly preserve and regression-test the existing memory-save bypass.
- [ ] Decide the meaning of “Remind me …” before action cutover: `create_reminder`, `create_task`, or another explicitly documented behavior. Add the decision and examples to the acceptance corpus. Do not claim notification delivery unless the configured push provider is available and that delivery path is verified.
- [ ] Before every routed write, re-check cancellation and authorization as close as possible to the mutation/transaction commit, then rely on idempotency for retries. Prove cancellation racing with execution and commit; cancellation after a committed transaction cannot undo that write. Preserve the existing confirmation as a prerequisite.
- [ ] Never treat route confidence as permission. Route fallback is forbidden after an action is claimed/executed; preserve exactly-once behavior on retry, disconnect, and resume.
- [ ] Test provider-specific behavior against the active OpenAI adapter and at least one other configured adapter before claiming provider-neutral release readiness.

**Gate:** general questions and task/reminder actions issue zero RAG calls by default; memory actions use the existing proposal/confirmation path without retrieval; no task/reminder/memory mutation occurs before approval or more than once; cancelled work cannot commit a not-yet-committed mutation.

### 8. Add a small semantic classifier only if needed

- [ ] First measure deterministic-rule coverage and disagreement. If needed, add a separately configured fast classifier for ambiguous turns with bounded input, tokens, deadline, and cost; do not call it for obvious direct/pending-confirmation requests.
- [ ] Parse and Pydantic-validate the classifier's proposed route independently of provider-native structured-output support; allow-list targets and fall back safely on malformed, timed-out, or low-confidence output.
- [ ] Benchmark classification accuracy and incremental latency against the frozen corpus and the current provider/model, including adversarial prompt-injection text from STT.

**Gate:** classifier improves ambiguous-route accuracy within an agreed latency/cost budget and cannot authorize tools.

### 9. Roll out, measure, and keep rollback simple

- [ ] Enable canary by stable authenticated cohort, then widen only after reviewing route disagreement, model/retrieval call counts, P50/P95 latency, STT-to-first-audio, response cancellation, stale TTS frames, and writes.
- [ ] Review per-route router/main-model calls and token totals, embedding/reranker calls, retrieval calls, latency, and cost where provider pricing is verified. Do not infer cost from incomplete historical token aggregates.
- [ ] Require 100% pass on critical intent distinctions, zero unconfirmed/duplicate writes, zero cross-user leakage, grounded memory no-result behavior, and no WebSocket/TTS protocol regression. Set numeric non-safety latency targets from Step 0 before canary; deterministic routing should not add a network round trip.
- [ ] Before production canary, fix or explicitly disposition the audit's known backend routing failure and relevant frontend/backend lint/type/format failures; close the transcript/time-resolution logging privacy issue or document an approved mitigation.
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
