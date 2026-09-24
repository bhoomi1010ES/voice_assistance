# Phase 0 — Router acceptance baseline freeze

**Status:** PARTIAL; the acceptance gate remains open.  
**Recorded:** 2026-09-24 00:39 UTC (2026-09-23 17:39 America/Los_Angeles).  
**Repository:** `main`, `41a963be75bea5e0fe83ea106dfd4a6bdc184bac`; working tree was clean at capture.  
**Scope:** documentation and a labeled draft corpus only. No application code or runtime flags were changed.

## Todo and gate status

- [x] Record revision, safe configuration facts, and current gateway/tool/confirmation/memory/cancellation/TTS contracts.
- [ ] Reconcile and label the corpus against proposal PDF pages 8–9 and 11–13. A reproducible draft exists at [`phase0_router_acceptance_corpus_v1.json`](phase0_router_acceptance_corpus_v1.json), but those pages have not been extracted in this environment.
- [ ] Measure route-level model/retrieval calls, speech-end-to-first-text/audio, P50/P95 turn latency, and write safety on a current labeled run. Existing evidence does not support these aggregates.
- [ ] Finalize canary cohort and named rollback owner. Initial disabled flags and provisional relative budgets are recorded below; owner/cohort and absolute latency budgets need the missing baseline.

**Gate decision: NOT PASSED.** Do not use this draft as the canary acceptance corpus or enable a new route until source reconciliation, current measurements, and rollout ownership are recorded.

## Revision and configuration snapshot

The working tree was clean on `main` at commit `41a963be75bea5e0fe83ea106dfd4a6bdc184bac`. No secrets or secret values were copied. The PDF referenced by `plan.md` is present at `C:\Users\lenovo\Downloads\LangGraph_Router_Integration_Proposal_Voice_Assistant.pdf`, size 279,969 bytes, SHA-256 `B1E7DF57231233F7884918A0C05E241DB662BA3808179186C98100A390BA0825`. No `pdftotext`, `mutool`, or Python PDF package was available, so page-level source traceability is still open.

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
| New router mode | `off`; no router flag is currently implemented. Any future `ROUTER_MODE` setting must default to `off`. |
| Existing memory flags | Preserve deployment-specific values; safe repository defaults remain retrieval `off`, writes `false`. Current local values are `inject`/`true` and are not a rollout recommendation. |
| Graph flags | `GRAPH_RAG_MODE=off`, `GRAPH_WRITE_ENABLED=false`. |
| Canary cohort | None until corpus and baseline gates pass and a cohort can be selected deterministically by stable authenticated user identity. |
| Rollback owner | Project maintainer/on-call role is required; named person is TBD and must be assigned before canary. |
| Safety budget | 100% on critical corpus distinctions; zero unconfirmed/duplicate writes; zero cross-user or excluded-memory leakage; cancellation before mutation. |
| Provisional performance budget | Deterministic routes add 0 model calls and 0 retrieval calls when retrieval is not required. After a valid paired baseline exists, route P50 should not regress; route P95 may regress by at most 10%. Set absolute millisecond limits from the paired capture before canary. |
| Voice behavior | No increase in stale audio or cancellation failures; keep physical loudspeaker barge-in as a separate acceptance gate, not a router pass/fail attribution. |

## Files added or updated

- Added `docs/phase0_router_acceptance_corpus_v1.json`.
- Added this Phase 0 baseline report.
- Updated `plan.md` Step 0 to show completed inventory and remaining source, measurement, and ownership work.
- No code files changed and no application build was run.
