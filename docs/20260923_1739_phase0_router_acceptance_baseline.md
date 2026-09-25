# Phase 0 — Router acceptance baseline freeze

**Status:** PARTIAL; the acceptance gate remains open.  
**Recorded:** 2026-09-24 00:39 UTC (2026-09-23 17:39 America/Los_Angeles).  
**Repository:** `main`, `41a963be75bea5e0fe83ea106dfd4a6bdc184bac`; working tree was clean at capture.  
**Scope:** documentation and a labeled draft corpus only. No application code or runtime flags were changed.

## Todo and gate status

- [x] Record revision, safe configuration facts, and current gateway/tool/confirmation/memory/cancellation/TTS contracts.
- [ ] Reconcile and label the corpus against proposal PDF pages 8–9 and 11–13. A reproducible draft exists at [`phase0_router_acceptance_corpus_v1.json`](phase0_router_acceptance_corpus_v1.json), but those pages have not been extracted in this environment.
- [ ] Measure route-level model/retrieval calls, speech-end-to-first-text/audio, P50/P95 turn latency, and write safety on a current labeled run. Existing evidence does not support these aggregates.
- [x] Record rollback owner, backup, triggers, and procedure. Canary cohort and latency guardrails are documented as provisional and inactive; validate them against a fresh baseline before rollout.

**Gate decision: NOT PASSED.** Do not use this draft as the canary acceptance corpus or enable a new route until source reconciliation, current measurements, and rollout ownership are recorded.

## Revision and configuration snapshot

The working tree was clean on `main` at commit `41a963be75bea5e0fe83ea106dfd4a6bdc184bac`. No secrets or secret values were copied. The PDF referenced by `plan.md` is present at `C:\Users\lenovo\Downloads\LangGraph_Router_Integration_Proposal_Voice_Assistant.pdf`, size 279,969 bytes, SHA-256 `B1E7DF57231233F7884918A0C05E241DB662BA3808179186C98100A390BA0825`. The initial capture did not extract proposal pages; the 2026-09-24 reconciliation below records the review of pages 8-9 and 11-13.

Safe values read from the local `.env` file (file configuration only; no claim about a running process):

| Setting | Local value |
|---|---|
| `STT_ENGINE` | `remote` |
| `LLM_PROVIDER` / `LLM_MODEL` | `openai` / `gpt-5.6-luna` |
| `MEMORY_RETRIEVAL_MODE` / `MEMORY_WRITE_ENABLED` | `inject` / `true` |
| `GRAPH_RAG_MODE` / `GRAPH_WRITE_ENABLED` | `off` / `false` |
| `CONVERSATION_LOGGING_ENABLED` | `true` |
| `VOICE_CONFIRMATION_TTL_SECONDS` | Not set locally; code default is 120 seconds |

Repository-safe defaults differ from this local profile: memory retrieval defaults to `off`, memory writes to `false`, graph reads/writes to `off`/`false`, and conversation logging to `false`. `/ready` or an isolated current process was not queried for this capture; local file values are not process-effective evidence.

## Existing contracts

### Gateway event and turn order

The relevant order is: authenticated `/v1/voice` WebSocket and session/turn ownership → audio frames and `client.audio.commit` (or client/VAD policy commit) → remote commit-time STT finalization → durable turn and user-message persistence → `transcript.final` delivery → pending Redis confirmation resolution → ordinary LLM orchestration when no confirmation consumed the turn → optional memory retrieval and the provider-neutral tool loop → streamed text and sentence-segmented remote TTS audio → gateway completion/cancellation event. The boundary for any later decision router is after final STT and existing confirmation resolution. `session_id`, `turn_id`, and `response_id` scope events and cancellation; the gateway remains the owner of output and completion.

Speech-end auto-commit in continuous flows waits a 900 ms grace period. The configured remote STT path buffers the committed audio and issues one final transcription request; it does not provide genuine partial transcripts. Android and backend monotonic clocks cannot be subtracted from each other without a verified clock mapping.

### Tool schemas and write boundary

Registered tool names are `get_current_time`, `get_current_date`, `list_tasks`, `list_reminders`, `memory_search`, `create_task`, `update_task`, `complete_task`, `create_reminder`, `update_reminder`, `delete_reminder`, `memory_save`, and `memory_forget`. Argument schemas are Pydantic models; authenticated owner identity is server supplied. Reads require the corresponding `tasks:read`, `reminders:read`, or `memory:read` scope (clock reads require none). Writes require `tasks:write`, `reminders:write`, or `memory:write`, explicit confirmation, rate limits, audit, and idempotency. Mutating tool calls cannot be registered without confirmation. The exact argument fields and current routing caveats are inventoried in [`pre_langgraph_architecture_audit.md`](pre_langgraph_architecture_audit.md), §11.

### Confirmation and cancellation

Spoken Yes/No/Cancel is resolved before ordinary model routing when a pending proposal exists. Pending confirmation is Redis backed, keyed by authenticated user/device, defaults to a 120-second TTL, is rebound to the authenticated session on reconnect, and is atomically claimed/revalidated before execution. Expired, stale, ambiguous, or out-of-scope approvals must not write. Server restart durability was not verified. Client response cancellation is response scoped and propagates cancellation through gateway work; it must be checked before side effects. Spoken “stop” after STT is a distinct route from the Android physical interruption of active playback.

### Memory opt-out and TTS

Memory retrieval respects global `off`/`shadow`/`inject`, user enablement, and session exclusion. `off` avoids retrieval; `shadow` does not inject returned memory; disabled/excluded users must not receive retrieval context. Memory write enablement and user/session exclusion separately gate automatic extraction. A memory question with no evidence must not be answered by guessing. Local `.env` currently opts into retrieval `inject` and writes, while checked-in code defaults are off.

Text is streamed by the LLM loop, segmented for remote Kokoro-compatible TTS, and returned as response-scoped binary audio for Android playback. Current timing hooks include gateway first text and first backend-observed TTS audio. A backend first-audio timestamp is not proof of speaker playback; physical playback telemetry is required for true user-perceived latency.

## Corpus artifact

`phase0_router_acceptance_corpus_v1.json` contains 24 deterministic cases spanning current time vs medicine time, today's date vs a meeting date, Yes with/without a pending action, missing memory, general knowledge, ambiguous actions, informational/action false positives, multi-intent, Spanish/Hindi utterances, malformed STT, cancel/retry, confirmation expiry, and memory opt-out. Critical safety labels cover no-write behavior, approval, idempotency, source-grounded memory, and clarification.

This is explicitly a draft: all proposal page links are marked pending because the PDF's target pages were not extracted. Case language and labels must be reviewed against those pages before version 1 is frozen. The corpus contains no personal identifiers or copied credentials.

## Baseline measurements and limits

| Required measure | Finding |
|---|---|
| Model calls by route | Not available from an accepted current labeled run. Existing audit reports most ordinary eligible turns reach the main LLM; confirmation and explicit memory-save paths can bypass it. Call/token counts by route are not present. |
| Retrieval calls by route | Not available as an accepted aggregate. Existing audit reports retrieval before the main LLM for ordinary eligible post-confirmation turns when memory is on; confirmation and explicit memory-save paths bypass it. |
| Speech-end to first text/audio | No valid current aggregate. First text/backend first audio hooks exist, but the retained physical trace mixed monotonic clock domains. |
| Turn latency P50/P95 | No valid current route-labeled aggregate. `docs/20260916_174500_live_turn_latency_capture.md` is a historical single turn (backend audio at 7,338 ms; backend completion at 15,912 ms after speech end), not a current or route-comparative baseline. |
| Tool-write safety | Contract is documented and existing tests cover portions, but no corpus-driven before-change safety run was captured here; this measure remains open. |
| Physical barge-in | Track separately as an existing acceptance defect. The 2026-09-23 fix note reports no attached device and no current physical trace; loudspeaker interruption and real-interruption trials remain pending. Earlier `no_safe_acoustic_path` observations do not establish cause. |

Do not compare Android and backend monotonic values directly. For the next capture, record same-device and same-provider route labels, model request/round/token counters, retrieval invocation/stage counters, gateway speech-end/final-STT/first-text/first-audio/completion markers, and tool proposal/approval/commit outcomes. Report p50/p95 only for complete, clock-compatible turns and retain excluded/error cases with reasons.

## Initial rollout decisions (provisional)

| Control | Initial value / decision |
|---|---|
| New router mode | `ROUTER_MODE=off` (settings field `router_mode`, default `off`); gateway dispatch remains disconnected. |
| Existing memory flags | Preserve deployment-specific values; safe repository defaults remain retrieval `off`, writes `false`. Current local values are `inject`/`true` and are not a rollout recommendation. |
| Graph flags | `GRAPH_RAG_MODE=off`, `GRAPH_WRITE_ENABLED=false`. |
| Canary cohort | Provisional 5% stable-authenticated-user hash cohort; inactive. Review deployment cohort before enabling. |
| Rollback owner | Bhoomi. Backup: Backend lead / designated developer. See rollback ownership procedure below. |
| Safety budget | 100% on critical corpus distinctions; zero unconfirmed/duplicate writes; zero cross-user or excluded-memory leakage; cancellation before mutation. |
| Provisional performance budget | Rule decision p95 <= 50 ms; added speech-end-to-first-text/audio p95 <= 100 ms; turn-latency regression p50 <= 2% and p95 <= 5%; no extra main-model or unnecessary retrieval/embedding calls; safety misroutes/unauthorized resolutions/duplicate writes = 0. Numeric latency values remain inactive until checked against a valid paired legacy baseline. |
| Voice behavior | No increase in stale audio or cancellation failures; keep physical loudspeaker barge-in as a separate acceptance gate, not a router pass/fail attribution. |

## Files added or updated

- Added `docs/phase0_router_acceptance_corpus_v1.json`.
- Added this Phase 0 baseline report.
- Updated `plan.md` Step 0 to show completed inventory and remaining source, measurement, and ownership work.
- No code files changed and no application build was run.


## 2026-09-24 source reconciliation and gate update

The supplied proposal PDF was present at the path and SHA-256 recorded above. Pages 8, 9, and 11-13 were extracted locally for this review. The acceptance corpus records page references per case and contains no credentials or user-specific transcript data.

| Proposal guidance | Corpus/implementation disposition |
|---|---|
| Pages 8-9: narrow current time/date can be direct; stored schedules use structured reads; memory/general routes are selective; pending Yes and writes retain confirmation | Added critical current clock/date versus medicine/meeting/task schedule distinctions; memory/general/confirmation cases; existing Redis confirmation remains authoritative. |
| Pages 9, 13: ?Remind me to ?? is an action; reminder/time meaning can be ambiguous | Existing assistant behavior creates a task for clear ?remind me to ??; route output is only `TASK_ACTION` domain, with no generated title/date/ID, and legacy tasks:write plus confirmation applies. Unclear reminder/time requests clarify. Management of an existing reminder uses the reminder action domain. |
| Pages 11-12: require false-positive, action safety, grounded memory, and voice checks | Frozen 75-case corpus includes multi-intent, multilingual, malformed STT, missing-memory, cancel/retry, yes/no/expiry/scope, idempotency and informational-action false positives. Voice protocol and physical playback remain separate gates. |
| Page 13: use deterministic obvious routing, validate decisions, defer semantic fallback to uncertain turns | Deterministic rules and Pydantic decision checks implemented. No semantic model fallback, gateway dispatch, shadow or canary is active in this phase. |

### Reproducible corpus and deterministic results

[`phase0_router_acceptance_corpus_v1.json`](phase0_router_acceptance_corpus_v1.json) is frozen, version 1.0.0, with 75 labeled cases (71 safety-critical). Run from repository root with `PYTHONPATH=backend .venv/Scripts/python.exe backend/scripts/router_acceptance.py` (PowerShell: set `$env:PYTHONPATH='backend'` first). The current result is in [`phase2_router_acceptance_v1.md`](phase2_router_acceptance_v1.md): 75/75 passed; 0 critical failures; the fixture attempted 0 writes and made 0 model/retrieval calls. This validates deterministic preflight/rules only. Confirmation resolution is represented with a non-writing fixture; real store behavior is covered by existing backend tests, not executed as a real Redis acceptance run here.

### Telemetry and performance disposition

`logs/latency_trace.jsonl` (61,409,192 bytes; last updated 2026-09-24 11:08 local) contains 79,274 records, including 65,389 tagged `backend_python_perf_counter` and 13,885 tagged `backend`. The trace has no process identifier or route labels; turn IDs can span multiple components and the file contains multiple process runs. It is not a valid labeled cohort. Speech-end/first-text/first-audio/turn-complete records cannot be safely paired for a current deployment, and no route-level model/tool/retrieval/token/write counters are available. Therefore route-level calls, retrieval, speech-end-to-first-text/audio and P50/P95 remain **N/A**, not zero. No cost estimate is inferred. A new isolated, route-labeled capture is still required before canary.

The known physical barge-in defect remains separately tracked in [`20260910_131500_phase7_physical_acceptance.md`](20260910_131500_phase7_physical_acceptance.md); that report says speech did not interrupt audible TTS and leaves a fresh loudspeaker trial pending. No new physical-device test was run for this router work, and software cancellation tests do not close that defect.

### Test, privacy, and rollout decisions

- Full backend suite: 456 passed, 56 skipped. Targeted Ruff and `git diff --check` pass. No frontend/native build was run because no client code changed.
- The known `How do I create a task?` false positive is fixed and regression-tested. Previously persisted raw log records were not rewritten; deployment retention/access review remains required before canary. Current date/time tool precedence is also tested and passes. Frontend Jest: 18 suites / 118 tests passed. Frontend ESLint passes. `npm run check` stops at the pre-existing TypeScript error `frontend/__tests__/phase3.test.ts:762` (expression inferred as `never`); it therefore does not run later steps. Independent Prettier check reports 30 pre-existing unformatted files. UI secret scan flags `TaskEditorModal.tsx` because the regex matches `testID="task-date-input"` (`sk-` substring), a false positive; no credential-like literal is present at the reported lines. Backend Ruff full check passes; changed backend files pass Ruff formatting check. Repository-wide Ruff format check reports 16 unformatted files, including untouched existing files; `mypy` is not installed/configured. No frontend changes were made.
- Date/time resolution logs now record safe status/basis and timezone metadata without the raw phrase or resolved local/UTC timestamp. Router telemetry is not enabled and the acceptance report contains only curated corpus utterances, not private user logs. Legacy STT/database retention is outside this patch and remains governed by existing settings/policy.
- Router mode: `off`; no gateway dispatch. Provisional canary cohort: 5% by stable authenticated user ID hash, inactive and not selected/enabled in any deployment. Rollout requires a deployment-specific cohort review before use.
- Provisional budgets (inactive pending valid paired baseline): deterministic rule decision p95 <= 50 ms; added speech-end-to-first-text and speech-end-to-first-audio p95 <= 100 ms; turn-latency regression p50 <= 2% and p95 <= 5%; extra main-model calls = 0 for deterministic routes; extra retrieval/embedding calls = 0 for direct clock/control/general routes; critical write misroutes = 0; unauthorized confirmation resolutions = 0; router-caused duplicate writes = 0. The absolute latency ceilings are engineering guardrails, not measured baseline-derived guarantees; verify against a fresh paired capture before any canary. The trace here cannot validate these limits.
- Rollback owner: **Bhoomi**. Backup: **Backend lead / designated developer**.

### Rollback ownership

**Rollback owner:** Bhoomi

**Backup owner:** Backend lead / designated developer

Trigger rollback for any critical routing misclassification, unconfirmed or duplicate write, cross-user data issue, significant latency regression, WebSocket/TTS regression, or router/provider dependency failure.

Procedure:

1. Set `ROUTER_MODE=off` (the repository setting; `LANGGRAPH_ROUTER_MODE` is not defined by this backend).
2. Restart or reload the backend if required for the configuration change to take effect.
3. Verify `/health` and `/ready`.
4. Run the gateway smoke tests.
5. Verify task/reminder confirmation still works through the legacy path.
6. Verify no duplicate or unexpected pending writes occurred.
7. Record the incident and keep the router off until the issue is resolved.

No custom rollback system is required for Phase 0; the owner performs the existing off-mode rollback and verifies the legacy path.

**Updated gate: NOT PASSED.** Corpus/source reconciliation and deterministic safety acceptance are complete, but valid before-change route metrics, fresh physical status, and validation of numeric latency limits against a paired baseline are still outstanding. Router remains off; no shadow or canary has started.

## 2026-09-24 physical baseline follow-up (partial; not gate evidence)

The user completed the capture and asked to stop and analyze. Collector output is retained at `logs/phase0_live_baseline_run2_20260924.jsonl`. The environment remained in legacy mode (`ROUTER_MODE=off`). This was a real-device sample, not a shadow or canary run.

| Observation | Captured result | Acceptance status |
|---|---:|---|
| Final STT turns | 5 | Partial route coverage |
| Completed turns | 4 | Fifth turn was still in progress when capture stopped |
| General questions | 2 | Route inferred from the prompted sequence; no route label emitted |
| Current time/date | 1 each; one legacy `get_current_time` and `get_current_date` tool invocation observed | Legacy behavior only; model rounds/calls are not countable from the current trace |
| Memory query | 1; one legacy `memory_search` invocation observed | No memory-content result is recorded here |
| Embedding/vector/FTS/rerank stages | One of each per captured turn | Indicates retrieval pipeline work even on non-memory prompts under current `inject` behavior; does not yield route-level call counts |
| Confirmation/write actions | 0 | Write-safety scenario not exercised |
| Router calls | 0 expected; router off and not connected to gateway dispatch | Confirms no router cutover |

The live collector/analyzer does not currently accept this as a latency sample. It counts legitimate segmented TTS events as duplicates and treats separate response IDs on barge-in lifecycle records as response mismatches. The capture included repeated `tts_first_audio_received` and `tts_generation_completed` events from chunked TTS; four turn groups also contained an additional response ID used only by `barge_in_confirmed` / `barge_in_playback_stop_requested` lifecycle events. These collector flags are instrumentation limitations, not evidence of a wrong assistant response. The backend monotonic clock, Android elapsed-realtime clock, and React Native performance clock are distinct; available speech-end-to-first-text/audio pairs are not valid cross-source durations. No P50/P95 or speech-to-first-text/audio latency is reported from this run.

The user's confirmation that the signed-in account is disposable test data was received, but no create/confirm/cleanup turn was spoken. Thus there is no live write-safety evidence. There was also no pending-confirmation Yes/No, schedule false-positive, multilingual, ambiguous, cancel, or retry turn in this capture. No raw transcript or personal memory content is reproduced in this report.

### Remaining work for the Phase 0 gate

- [ ] Repair/validate collector correlation for barge-in lifecycle response IDs and segmented TTS; add regression coverage so legitimate segments are not counted as duplicate playback completions.
- [ ] Capture the remaining labeled cases on device, including stored medicine/meeting schedule requests, structured reads, pending and no-pending Yes, multilingual and ambiguous/cancel cases, and the approved disposable-account confirmed write plus cleanup.
- [ ] Obtain valid paired same-clock speech-end-to-first-text/audio and complete-turn latency for enough turns to report P50/P95, and record model call/round/token and retrieval/embedding/reranker counts by route. Keep unmeasured fields explicitly N/A.
- [ ] Verify no duplicate/pending test write remains after the confirmed write and cleanup scenario.

**Gate remains NOT PASSED.** The five-turn sample is useful operational evidence but does not satisfy a reproducible route-labeled before-change baseline, the required category coverage, paired latency metrics, or write-safety coverage. Keep `ROUTER_MODE=off`; do not begin canary based on this capture.

## 2026-09-24 collector and baseline-harness follow-up

The prior segmentation/lifecycle collector defects have been corrected in the offline tooling. Live event identity now retains TTS segment/chunk identifiers and sequence values, response ownership and cancellation are session-scoped, and canceled response audio is excluded while valid later segments in a response remain accepted. Analyzer coverage now includes single, multi-segment, delayed/out-of-order, duplicate, canceled, and clock-domain cases. A same-backend-clock path records speech-end to first text-send, first provider TTS audio, and gateway turn-complete durations; mixed-device clock intervals remain unavailable rather than being synthesized.

The automated baseline manifest reflects the established legacy `Remind me to …` semantics: `create_task`, followed by `complete_task` to clean up that exact task. The driver checks pending confirmation scope, original turn, expected action/target, TTL, absence of pre-approval writes, one confirmed commit, and database cleanup before it can accept the action. The acceptance gate now requires at least 20 completed turns, paired backend metrics for all 20, required route categories, both approved action/cleanup confirmations, no excluded anomalies, and zero duplicate/unconfirmed writes. The gate cannot pass from offline tests or readiness checks alone.

The route inventory, frozen corpus, safe flag/cohort/rollback/budget documentation, and logging privacy disposition remain valid. Current settings resolve to `ROUTER_MODE=off`, cohort `0`; current local `/health` and `/ready` returned `ok` and `ready`. The fresh physical preflight did not start a capture: Windows `adb devices -l` failed with `Cannot mkdir '\.android': Permission denied`. As a result, the phone state, ADB reverse mappings, active voice WebSocket, heartbeat, and microphone PCM could not be reverified. The running backend's `/ready` response also does not currently expose the new optional TTS readiness detail, so TTS readiness is not accepted as a pre-run check. No prompts were spoken and no test task was created; there is no new live baseline or write-safety evidence.

Offline verification after these edits: focused Phase 0 collector/analyzer/harness tests passed (**45 passed**); full backend suite passed (**550 passed, 56 skipped**); full `ruff check backend` passed. `ruff format --check backend` still reports 13 files needing formatting; all files changed for this follow-up pass targeted formatting checks. No frontend or app build was run.

**Phase 0 remains NOT PASSED.** Outstanding gate work is a current TTS-ready backend process, repaired host ADB/device access, reverified phone/session/heartbeat/PCM, the automated 20+ turn acoustic sample, live confirmed task plus cleanup, and accepted route-level calls/tokens/retrieval and paired P50/P95 measurements. Phase 3/7/8 have not been activated by this follow-up; router remains off.


## 2026-09-24 fresh legacy-baseline capture attempt

**Outcome: no valid voice turns captured; Phase 0 remains NOT PASSED.** No latency or call-count sample is manufactured from health checks, unit tests, the deterministic Phase 2 corpus, or the mixed-clock retained trace.

### Capture environment and method

- Attempt time: 2026-09-24 18:29 UTC.
- Repository revision: `71a98077464d1e66ba13c1eb211f997f47905915`; the working tree contained the Phase 0/2 implementation changes recorded in the previous work note.
- Settings resolved in the capture shell (safe non-secret fields only): `ROUTER_MODE=off`; remote STT; OpenAI `gpt-5.6-luna`; memory retrieval `inject`; embedding model `BAAI/bge-m3`; reranker `BAAI/bge-reranker-v2-m3`; TTS model `kokoro`. The voice gateway source does not dispatch through `DecisionRouterService`.
- Method attempted: current local backend plus the existing live latency collector. `backend/scripts/live_latency.py` tails real backend/Android events; it is not a deterministic audio replay harness.
- `/health`: `{"status":"ok"}`. `/ready`: HTTP 503, because embedding and reranker readiness returned `memory_provider_network_error`; PostgreSQL, Redis, and LLM readiness reported OK/ready, with LLM live verification not established.
- Android capture: `adb devices -l` could not initialize the Android user directory (`Cannot mkdir '\.android': Permission denied`); no device capture was available.
- No authenticated safe test account/fixture was available for confirmed task/reminder or memory writes. No write was attempted. No representative turn was sent, so there are zero measured turns and zero route categories with samples.

### Route-level capture result

| Legacy route category | Turns measured | Model calls/rounds/tokens | Embedding/reranker/retrieval | Tool calls/writes |
|---|---:|---|---|---|
| General LLM | 0 | n/a | n/a | n/a |
| Direct date/time | 0 | n/a | n/a | n/a |
| Structured task/reminder read | 0 | n/a | n/a | n/a |
| Memory query hit/miss | 0 | n/a | n/a | n/a |
| Task/reminder action | 0 | n/a | n/a | n/a |
| Memory action | 0 | n/a | n/a | n/a |
| **Total** | **0** | **n/a** | **n/a** | **0 writes attempted by this capture** |

Per-100-turn metrics are n/a because the capture sample has zero turns. This capture made zero router calls; the application setting is off and the gateway has no router dispatch integration. Main-model calls, rounds, token usage, embedding, reranker, retrieval, and tool rates are not inferred from the prior invalid trace. Cost per 100 turns is not recorded ? provider pricing not verified.

### Valid latency summary

All values below are `n/a ? incompatible or unavailable clock domain` because no new turn emitted a correlated set of source-local events in this capture.

| Metric | Count | Min | Mean | P50 | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| Speech-end to STT final | 0 | n/a | n/a | n/a | n/a | n/a |
| Post-STT orchestration | 0 | n/a | n/a | n/a | n/a | n/a |
| LLM TTFT | 0 | n/a | n/a | n/a | n/a | n/a |
| Speech-end to first text | 0 | n/a | n/a | n/a | n/a | n/a |
| TTS TTFA | 0 | n/a | n/a | n/a | n/a | n/a |
| Speech-end to first audio | 0 | n/a | n/a | n/a | n/a | n/a |
| Complete turn | 0 | n/a | n/a | n/a | n/a | n/a |
| STT request, embedding, retrieval, reranking, TTS generation | 0 | n/a | n/a | n/a | n/a | n/a |

### Provisional budget validation

Existing provisional guardrails are preserved. No latency budget can be marked PASS from a zero-turn capture. Safety budgets remain absolute and the frozen deterministic corpus continues to cover them; they do not substitute for live baseline rates.

| Budget | Current legacy baseline | Allowed regression / guardrail | Absolute future limit | Status |
|---|---|---|---|---|
| Rule decision latency p95 | n/a; router intentionally not called | <= 50 ms | <= 50 ms when first introduced | REQUIRES LATER COMPARISON |
| Speech-end to first text p95 added latency | n/a | <= 100 ms | legacy p95 + 100 ms | REQUIRES LATER COMPARISON |
| Speech-end to first audio p95 added latency | n/a | <= 100 ms | legacy p95 + 100 ms | REQUIRES LATER COMPARISON |
| Complete-turn latency p50 | n/a | <= 2% regression | legacy p50 x 1.02 | INVALID ? no legacy p50 |
| Complete-turn latency p95 | n/a | <= 5% regression | legacy p95 x 1.05 | INVALID ? no legacy p95 |
| Extra main-model calls on deterministic routes | n/a per route; router path itself is absent | 0 | 0 | REQUIRES LATER COMPARISON |
| Extra retrieval/embedding calls for direct clock/control/general routes | n/a per route | 0 | 0 | REQUIRES LATER COMPARISON |
| Critical write misroutes | 0 in frozen safety corpus | 0 | 0 | PASS ? corpus only |
| Unauthorized confirmation resolutions | 0 in frozen safety corpus | 0 | 0 | PASS ? corpus only |
| Router-caused duplicate writes | 0 in non-writing acceptance harness | 0 | 0 | PASS ? harness only |
| Stored schedule routed to current date/time | 0 in frozen safety corpus | 0 | 0 | PASS ? corpus only |
| Informational task routed to task write | 0 in frozen safety corpus | 0 | 0 | PASS ? corpus only |
| Router-generated executable task/memory arguments | 0 by contract and corpus | 0 | 0 | PASS ? deterministic contract only |

To validate the latency budgets, a live/replay capture must first produce valid legacy P50/P95 values with route labels, same-source durations, representative route coverage, and safe confirmed-write fixture evidence. Then compute future limits as the formulas above and run a paired comparison when shadow/canary work is separately authorized.

**Physical barge-in status remains separately tracked and is not used to fabricate Phase 0 latency baseline evidence.** The last recorded loudspeaker result remains the September 10 report; no fresh physical run was done here.

**Updated gate: NOT PASSED.** The current environment cannot produce the requested representative live baseline or a real-path replay: readiness is degraded for memory providers, no device is available to the collector, and no deterministic end-to-end audio replay harness or safe write account is present. This leaves valid per-route call metrics and required P50/P95 latency values unavailable. Router remains off. Do not activate live shadow/canary/on based on this capture. Offline implementation work does not pass the Phase 0 gate.


## 2026-09-24 user-driven physical follow-up capture

**Outcome: capture executed; valid Phase 0 route baseline still NOT PASSED.** This follow-up supersedes the preceding ?zero turns? result as a record of whether a physical attempt occurred. It does not replace the gate: the collected trace cannot support complete, route-labeled latency/call/safety metrics.

### Environment

- The connected Android device was identified as model `CPH2527`, timezone `Asia/Kolkata`. The user launched the app and spoke the approved prompts. Router mode remained `off`; gateway dispatch still does not call the LangGraph router.
- Backend `/health` and `/ready` both returned HTTP 200 after restart; PostgreSQL, Redis, LLM, embedding, and reranker readiness reported OK. A Windows-host Redis RESP `PING` returned `+PONG`.
- Repository `HEAD` at capture was `71a98077464d1e66ba13c1eb211f997f47905915`. Safe config fields: remote STT, OpenAI `gpt-5.6-luna`, memory retrieval `inject`, embedding `BAAI/bge-m3`, reranker `BAAI/bge-reranker-v2-m3`, TTS `kokoro`. No secrets or spoken transcript were copied into this report.
- The event-only collector artifact is `logs/phase0_legacy_physical_capture_20260924.jsonl` (3,360 records). Its records contain event/timing/session/turn/response identifiers and aggregate metadata; do not publish raw identifiers beyond the local capture artifact.

### Capture counts and route-level usage

The artifact contains 22 completed-turn events and 20 turns with final STT. Eighteen turns entered the normal memory/context pipeline. Tool telemetry recorded 13 read operations: `list_tasks` 8, `list_reminders` 3, `memory_search` 1, and `get_current_date` 1. There were 18 embedding, FTS, vector-search, and rerank stage start/completions (18 each), matching the 18 observed memory/context pipelines. Two confirmation-required and two confirmation-resolved events were observed; the trace does not provide enough safe account/prompt context to treat them as tested action safety. No write tool invocation appears in the captured `tool_start` records. Therefore this run does not prove approved-write behavior or exactly-once write safety.

Model request observations are not a reliable call count: the gateway trace contains 50 request-observation, 34 first-token-observation, and 34 completion-observation events, while response correlation is inconsistent. No main-model token totals are available. Calls per 100 turns and route-level model/retrieval rates remain **n/a**; the spoken prompt sequence was not independently mapped to route labels. Router calls: 0 (mode off and not dispatched).

### Latency diagnostics and validity

The source-local STT request duration is measurable for 20 turns: min 942.66 ms, mean 1,344.52 ms, P50 1,264.10 ms, P95 1,827.36 ms, max 1,984.67 ms. The source-local memory-context pipeline is measurable for 18 turns: min 349.72 ms, mean 828.11 ms, P50 675.44 ms, P95 1,413.88 ms, max 1,436.29 ms. These are stage diagnostics only; they are not substitutes for route-labeled end-to-end percentiles.

The trace audit reports 18 turns with response-ID mismatch, repeated `tts_first_audio_received` events in 20 turns, repeated `tts_generation_completed` events in 21 turns, and one repeated TTS request-start event. Only one of the 22 turn groups passes the collector's full correlation checks. Repeated TTS events can represent multiple speech segments, but this capture did not identify the intended first segment consistently. Device and backend clock/process domains also prevent a valid complete paired set for speech-end-to-first-text/audio. Consequently **no P50/P95 is reported for post-STT orchestration, LLM TTFT, speech-end-to-first-text, TTS TTFA by turn, speech-end-to-first-audio, or complete-turn latency**. Do not use the collector's unfiltered per-turn summary for these measures.

| Required baseline measure | Valid sample | Status |
|---|---:|---|
| Speech-end to STT final | No consistent correlated pairs | Unavailable |
| Post-STT orchestration | No trustworthy route-labeled pairs | Unavailable |
| Main-model calls, rounds, tokens | Request observations do not reconcile; token counts absent | Unavailable |
| Speech-end to first text/audio and complete turn | Correlation/clock pairing invalid | Unavailable |
| Source-local STT request duration | 20 | Diagnostic only |
| Source-local memory-context pipeline | 18 | Diagnostic only |
| Per-route model/retrieval/tool/write metrics | No prompt-to-route mapping | Unavailable |

### Safety, TTS, and budgets

No write tool was recorded, so the run has no observed write to validate and cannot establish write-safety acceptance. The capture reported 37 `barge_in_degraded` and 22 `barge_in_confirmed` events; this event-level observation is recorded separately from a controlled physical loudspeaker barge-in test. It does not close the known physical barge-in defect.

Latency budgets cannot be checked without valid paired, route-labeled P50/P95 baselines. The numeric guardrails remain provisional, not measured acceptance limits. Keep `ROUTER_MODE=off`; do not activate/run shadow or enable canary/on based on this capture. Guarded offline implementation work does not pass the Phase 0 gate.

**Updated Phase 0 gate: NOT PASSED.** A device capture now exists, but it fails the gate's reproducibility/acceptance requirements because route labels and call counts are missing, TTS response correlation is unreliable, the required speech-end-to-first-text/audio and complete-turn percentiles are unavailable, and approved write safety was not exercised on a known test account.
