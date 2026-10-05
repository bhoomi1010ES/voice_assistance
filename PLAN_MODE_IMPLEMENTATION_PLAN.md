# Conversational Plan Mode implementation plan

**Verified:** 2026-10-05, America/Los_Angeles.  
**Status:** Planning only; no feature code implemented or changed during this review.  
**Scope:** The conversational Plan Mode described in the supplied `Pasted text.txt`.  
**Parent plan:** [implementation.md](implementation.md). Related boundaries: [router plan](plan.md) and [OKF plan](OKF_IMPLEMENTATION_PLAN.md).

## 1. Feature decision

Plan Mode gives the assistant permission to organize actionable commitments from natural conversation into owned plans, tasks, and push reminders. It must be explicitly enabled through the assistant UI or a direct voice request. Normal mode continues to use the existing behavior.

Plan Mode is session behavior layered around orchestration, not a replacement for `GENERAL_LLM`, `TASK_ACTION`, `MEMORY_QUERY`, Hybrid RAG, or OKF. The extraction model proposes actions; the backend validates evidence, resolves dates and owned targets, applies policy, and executes through the existing tool boundary.

Example while Plan Mode is enabled:

> We're starting XYZ. The backend will use FastAPI and PostgreSQL. I need the initial backend running tomorrow. The presentation and report must be ready Friday. We have a client meeting Friday at 4 PM.

Expected: one XYZ plan, technology context, three tasks, and one push reminder at the meeting time. Do not infer architecture/setup tasks merely from the technology list. The supplied description contains both a generated setup checklist and a later instruction to treat technologies as context; this plan follows the later, more precise actionability rule. Additional suggested steps can be displayed as drafts when requested, but must not silently become tasks.

## 2. Review todo and evidence limits

- [x] Read the supplied feature description and root `agents.md` instructions.
- [x] Inspect the current working tree, parent plans, router, voice orchestration, tools, models, APIs, memory, and mobile/native integration.
- [x] Identify existing capabilities and feature gaps from current source rather than historical completion claims.
- [x] Run focused existing backend tests without building the backend or mobile application.
- [x] Check multi-action time normalization with an in-memory diagnostic.
- [x] Write implementation phases, file mapping, acceptance criteria, and rollback behavior.
- [ ] Implement the feature in a future, explicitly requested code-change task.

The working tree already contained backend/frontend code edits, plan edits, and the untracked `0020_user_knowledge_mode.py` migration before this task. These are existing owner changes and must be preserved. Historical OKF/router documents contain dated snapshots; their old migration heads and flag values are not current-source facts.

This review did not inspect secret `.env` values, query live databases, change records, contact model providers, apply migrations, or verify physical Android behavior. Runtime rollout/readiness is not established by this document. Some scratch directories are inaccessible; application source and the selected tests were accessible.

## 3. Verified current codebase

All paths in this table refer to current files. Proposed files are listed separately in section 12.

| Area | Current evidence | Consequence |
|---|---|---|
| Voice entry | `backend/app/websocket/gateway.py` resolves pending voice confirmations after final STT, then enters `_stream_llm_response`; that method first calls `_dispatch_structured_route`. | Place mode controls and planning orchestration before a route can finish the turn. Never extract from partial STT. |
| Router | `backend/app/routing/models.py` defines `CONTROL`, `DIRECT_TOOL`, `STRUCTURED_READ`, `MEMORY_QUERY`, `MEMORY_ACTION`, `TASK_ACTION`, `GENERAL_LLM`, and `MIXED_AMBIGUOUS`. | Existing intent names remain. Planning control semantics must be integrated separately. |
| Graph | `backend/app/routing/graph.py` compiles a pure route-dispatch skeleton without model, tool, database, or TTS effects. | Keep planning writes outside this graph in the first release. |
| Router rollout | `backend/app/routing/service.py` supports off/shadow/canary/on; shadow does not own the response path. | Plan Mode must work with the legacy orchestration path too. Router shadow must never execute proposed planning actions. |
| Session state | `VoiceSession` has owner/device/auth identity and metadata; `VoiceConnectionState` has audio/connection state. No Plan Mode/active-plan fields were found. | Add durable, server-owned planning state; audio state is not the source of truth. |
| Tasks | `Task` has title, description, status, priority, due/local timestamps, timezone/source, and `source_turn_id`. No plan/project grouping exists. | Extend the existing task model rather than creating a second task table. |
| Task tools | `backend/app/llm/task_tools.py` registers create/update/complete/list. Creation flushes a new owned row; updates resolve an owned task by ID. | Reuse handlers and executor; conversational target resolution and semantic deduplication are additional work. |
| Task deletion | `backend/app/api/tasks.py` has a delete endpoint; no `delete_task` voice tool is registered in `task_tools.py`. | Add a confirmed voice deletion/cancellation path if required; do not assume it already exists. |
| Reminders | `Reminder` has optional task linkage, trigger/local timestamps, recurrence, push delivery identity, leases, retries, and worker state. No `plan_id` or direct `source_turn_id` exists. | Add grouping/provenance; preserve worker and delivery semantics. |
| Reminder tools | `backend/app/llm/reminder_tools.py` registers create/update/delete/list. | Reuse the existing tool and reminder worker path. |
| Write authorization | `ToolRegistry.register` rejects mutating tools without confirmation; `ToolExecutor.execute` checks confirmation after schema normalization/scopes. | Automatic organization needs a narrowly scoped server authorization mechanism, not a global confirmation toggle. |
| Per-turn limits | Task/reminder create tools each allow one call per turn. General LLM defaults include 8 tool calls and 4 rounds in `core/config.py`. | A six-task conversational plan cannot use current creation limits unchanged. |
| Execution replay | `PostgresToolIdempotencyStore` uses `(user_id, turn_id, tool_name, tool_call_id)`. | It prevents replay of the same invocation, not semantically repeated tasks in later turns. |
| Time handling | `device_time.py`, `task_due_dates.py`, and task/reminder normalizers provide device clock/timezone, relative expressions, future-time checks, and strict DST localization. Date-only deadlines resolve to local **23:59:00**. | Reuse these contracts, with candidate-specific evidence for turns containing multiple dates. |
| Recurrence | `services/recurrence.py` and reminder worker code support the existing bounded recurrence contract. | Initial automatic recurrence supports only that validated subset; broader recurrence is deferred. |
| Memory/OKF | `MemoryWriter` accepts grounded candidates and source IDs; the knowledge selector supports internal rag/okf/combined engines. `User.knowledge_mode` currently permits rag/okf. | Planning and knowledge modes are independent settings. Tasks remain in task storage; knowledge is not a task database. |
| Mobile UI | `AssistantScreen.tsx` shows voice conversation and confirmation cards; Tasks has existing CRUD screens/types/API clients. No Plan Mode selector or plan view was found. | Add a visible mode indicator and plan UI while retaining the global Tasks view. |
| Native transport | `VoiceSocket.ts`, `VoiceModule.ts`, native `rn/VoiceModule.kt`, and `voice/VoiceWebSocketTransport.kt` carry typed controls/events. Native payload parsing recognizes particular event families/fields. | Backend events alone will not deliver planning payloads to the UI; update the complete native-to-JS path. |
| API/schema | `api/routes.py` includes tasks/reminders/memories/sessions/voice but no plans API. | Add owner-scoped plan endpoints and schemas. |
| Migration files | Current source includes `0020_user_knowledge_mode`, following `0019_okf_owner_memory_generation`. | Recheck all heads before implementation. `0021` is only a provisional next migration number, not a verified deployed schema version. |

### Focused checks performed

From the repository root, using the existing virtual environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& '.\.venv\Scripts\python.exe' -m pytest -p no:cacheprovider `
  backend/tests/test_task_due_dates.py `
  backend/tests/test_device_aware_time.py `
  backend/tests/test_voice_protocol.py `
  backend/tests/test_llm_tool_loop.py `
  backend/tests/test_router_rules.py -q
```

**Result: 202 passed in 2.70 seconds.** These are existing focused checks, not new Plan Mode tests or full-suite/production acceptance.

An in-memory check fixed the clock at `2026-10-05T16:00:00Z`, timezone `America/Los_Angeles`, and passed this complete transcript to the current resolver:

> Monday I need to finish the API, Tuesday we have the client review, and by Friday I want the project ready.

For model expressions `Monday`, `Tuesday`, and `Friday`, all three resolved to `2026-10-13T06:59:00Z`. The complete transcript overrides the individual expression; a multi-date turn therefore needs separately validated source spans. `after lunch` and `before the meeting` were rejected as unsupported. `three days before Friday` resolved to Friday itself, ignoring the offset. Do not treat arbitrary natural-language scheduling as already supported.

## 4. Behavior and consent contract

### Activation and lifecycle

1. New sessions start in Normal mode. Enable with the UI or an explicit control utterance such as "Enable plan mode", "Turn on planning mode", or "Let's plan this". The last phrase activates only when used as a direct request, not a quote, example, or hypothetical.
2. Persist `{mode, active_plan_id, state_version, policy_version, enabled_at}` in a dedicated owner/session state record. This is proposed schema, not client metadata accepted without validation.
3. State is scoped to authenticated user, voice session, and the session's device/auth binding. A supported resume of that same session restores state. A new session, conversation reset, logout, session termination/timeout, or ownership change starts Normal; saved plans/tasks remain available.
4. Explicit "Disable plan mode" or "Stop planning" immediately revokes automatic-action authority and increments the state version. Recognize these before generic confirmation interpretation so "stop planning" cannot merely deny a pending tool.
5. An enable utterance with additional commitments applies only to content after the explicit activation span. If the boundary is unclear, enable the mode and ask for the plan details on the next turn; never extract earlier unrelated history retroactively.
6. Mode controls must work even when the reasoning model is unavailable. Server flags can deny enablement with a clear feature-unavailable response.
7. UI state changes only after server acknowledgement. A disconnected UI shows unknown/unavailable mode state and cannot authorize writes locally.
8. Turning mode off does not delete tasks, cancel scheduled reminders, or switch the user's rag/okf preference. Any planner-origin pending proposal whose consent is revoked is cancelled; an unrelated manually requested confirmation keeps its existing semantics.

### Actionability

| Class/example | Expected behavior in Plan Mode |
|---|---|
| INFORMATION / "The backend uses FastAPI." | Attach sourced project context if the plan is clear; no task. |
| REFERENCE / "The tutorial says to configure PostgreSQL." | No automatic task; referenced text is not the user's commitment. |
| Tentative INTENTION / "I'm thinking about starting a project." | No task. |
| Concrete INTENTION / "I want to start the backend tomorrow." | Create a task when the action and ownership are clear. |
| COMMITMENT / "We need to finish the backend by Wednesday." | Create one task with a resolved deadline. |
| DEADLINE / "The presentation and report must be ready Friday." | Create/update two tasks; share only the deadline evidence applying to both. |
| REMINDER / "We have a client meeting Friday at 4 PM." | Create an internal push reminder if the timezone/date/time are resolved. |
| PROJECT / "We're starting XYZ." | Create/select one plan and context; no invented setup task. |
| Correction / "Actually, make the presentation Thursday." | Update one uniquely resolved existing task; do not create another. |
| Progress / "I finished the backend." | Complete a uniquely resolved task; calculate plan progress from task state. |
| Ambiguous / "Friday at 4" | Clarify AM/PM if it cannot be established; do not invent an instant. |
| Negated, historical, quoted, hypothetical, question | No inferred organizational write unless there is a separate explicit commitment. |

Record actor attribution. Do not assume all third-party obligations belong to the user. "Alice might finish her report" is not a personal task; a clearly stated team deliverable can belong to the user's plan with a context note, without assigning/inviting Alice through an external service.

### Automatic-action policy

Automatic actions are limited to creation/grouping of owned plans, tasks, push reminders, sourced plan notes, and clearly resolved updates/rescheduling/completion. Explicitly stated recurrence may be automatic only within the supported validated recurrence subset. Memory promotion remains subject to memory consent and write policy.

Deletion, cancellation that removes an obligation, destructive undo, bulk reassignment, and external/consequential actions require confirmation. Sending messages/email, booking, purchasing, changing important settings, sharing data, and creating external calendar events are not existing planning tools and are outside the initial implementation scope. If added later, their confirmation requirements remain independent of Plan Mode.

Use deterministic `AUTO`, `CONFIRM`, `CLARIFY`, `NO_ACTION`, and `DENY` outcomes. Model confidence is a signal, not authorization. Never use `requires_confirmation=false` globally, mark writes read-only, or treat "Plan Mode enabled" as permission for arbitrary LLM tool calls.

## 5. Target orchestration

```text
Final STT transcript + authenticated turn + validated device time
                         |
              Deterministic mode/off controls
                         |
               Pending confirmation resolution
                         |
                 Current mode/policy snapshot
                         |
           +-------------+----------------+
           |                              |
         Normal                         Plan
           |                              |
   Existing router/legacy       Existing routing metadata + extractor
           |                              |
   Existing response path       Typed proposals and exact source spans
                                          |
                               Validation / target / dedupe / time
                                          |
                               Policy: auto / confirm / clarify
                                          |
                               Existing executor and handlers
                                          |
                               Commit structured records/results
                                          |
                               Planning events + grounded LLM/TTS
```

Implement a gateway orchestration seam shared by normal turns and supported response retries; do not duplicate the entire `_stream_llm_response` pipeline. Run one planner per eligible final transcript before direct route early returns. Read-only queries such as "What tasks do I have?" remain read-only. Explicit task commands are represented once: either the planner executes/defers them, or the existing tool path does, never both.

Keep the current route enum. Model `PLAN_MODE_ENABLE`, `PLAN_MODE_DISABLE`, and plan selection as deterministic control outcomes outside write-bearing `RouteDecision`; `CONTROL` may carry the semantic category in future metadata, but its current schema has no executable control arguments. Do not put transcripts or write arguments into persistent LangGraph state.

Support router off/shadow/canary/on. Planning shadow extraction is a separate feature flag and must be observation-only; enabling router shadow does not grant planner execution. Plan Mode must not depend on the rollout of OKF, combined knowledge, or GraphRAG.

Use the existing provider-neutral `LLMService` for a bounded extraction request. Return a JSON proposal using validated text or a proposal-only tool schema supported by the selected provider. Do not claim that `LLMRequest` already exposes a universal structured-output schema field. Never register the extraction output as a general mutation tool. No new model provider or LangGraph checkpointing dependency is required for v1.

After execution, the normal answer receives a bounded receipt containing saved changes, unresolved items, and plan context. Disable overlapping write tools for consumed proposals. Read-only conversational answering may continue. The model must never announce "added" before the receipt reports a successful commit.

## 6. Proposed data and schema

Use one first-class `Plan`; avoid a separate Project table in v1. `active_plan_id` is sufficient for grouping. A future project model can be added if it has a separate lifecycle, rather than duplicating plan identity now.

| Proposed storage | Required fields/contracts |
|---|---|
| `plans` | UUID, user ID, name, goal, status (`active`, `completed`, `archived`), deadline UTC/timezone, revision, created/updated times, optional source session/turn. Unique `(id, user_id)` for composite ownership references. |
| `plan_context_items` | UUID, user/plan IDs, kind, bounded text/structured value, grounding provenance, revision/status. Store plan-local notes separately from durable personal memory. Deduplicate context items. |
| `planning_sessions` | One record per owned voice session, mode, nullable active plan, state version, policy version, enabled/disabled times. Composite ownership constraints and server-side device/auth verification. |
| Existing `tasks` | Nullable `plan_id`, row revision for conflict checks, optional planning-origin metadata or action linkage. Keep existing fields/statuses and source-turn behavior. |
| Existing `reminders` | Nullable `plan_id`, nullable `source_turn_id`, row revision. Preserve optional `task_id`, delivery identity, recurrence and worker fields. |
| `planning_batches` | Owned original turn/session, stable batch ID, extractor/policy versions, extraction status, bounded source digest/reference, original mode-state version, retry metadata. Freeze validated proposals before execution replay. |
| `planning_actions` | Batch/user IDs, server action ID, ordinal, type, validated payload digest, disposition/reason, target IDs/revision, source span references, confidence, execution status, tool-call ID, committed result IDs/error code. Unique action identity per batch. |

Action lifecycle: `proposed -> validated -> auto_authorized|confirmation_pending|clarification_pending|no_action|denied -> executed|failed|cancelled`. Use explicit transition validation; failed extraction cannot result in execution.

All lookups and mutation targets are owner-scoped. Use composite `(resource_id, user_id)` references and test cross-owner mismatches at API and database levels. Optional source deletion must null only source references while preserving required owner IDs; do not add a composite `SET NULL` constraint that tries to null a non-null `user_id`. Define migration-safe deletion behavior explicitly.

Existing tasks/reminders default to no plan; no automatic regrouping/backfill. Source-session deletion removes extraction history/private provenance but does not silently delete explicitly saved organizational objects. Define retention for planning proposals and receipts; store bounded evidence needed for resolution/audit, not unrestricted transcripts in metrics or logs.

Plan progress is derived from owned linked tasks: completed count / included task count. Exclude cancelled tasks from the active denominator; show an empty state for zero tasks. Do not let the model write an arbitrary progress percentage or automatically mark a plan completed because it predicted success.

## 7. Extraction, time, target resolution, and deduplication

### Proposal contract

Return a strict, bounded envelope with project/context changes and zero or more candidate actions. Each action contains:

- Kind: `CREATE_TASK`, `UPDATE_TASK`, `COMPLETE_TASK`, `CREATE_REMINDER`, `UPDATE_REMINDER`, `CREATE_PLAN`, `UPDATE_PLAN`, `ADD_PLAN_CONTEXT`, or a confirmation-required destructive proposal.
- Statement classification, title/changed fields, temporal expression, recurrence expression, confidence, and grounded source offsets/lengths into the finalized transcript.
- Separate temporal source offsets when multiple actions share a clause or deadline.
- Plan/target mentions and dependencies expressed as references, not arbitrary trusted database IDs.

The backend supplies owner/session/turn/response identity, action IDs, actual target IDs, policy version, and state version. Reject extra fields, malformed/oversized payloads, invalid evidence offsets, contradictions, unsupported operations, and ungrounded dates. A model-provided explanation is useful in review but does not prove grounding.

Initial limits: at most 8 candidate organizational actions per turn, bounded context changes, bounded context/receipt text, and a configurable extraction timeout. These are proposed defaults to calibrate, not measured performance guarantees. On overflow ask the user to split the plan; do not silently truncate an actionable list.

### Candidate-specific time normalization

Validate spans against the server's final transcript, derive each action's own temporal evidence, and pass that evidence through the existing device-aware resolver. Do not pass a multi-date full transcript as every action's `source_transcript`.

Keep the current date-only task deadline of local 23:59:00. On a fixed local Monday, 2026-10-05, "Friday" is October 9; use IANA timezone rules, not a hardcoded `+05:30` from the feature illustration. Meeting reminders require a resolved time. Initial reminder trigger equals the event time unless the user supplies an advance offset; do not invent an extra notification.

Add explicit grammar/validation tests for offsets and multi-date turns. Interpret "next week" according to a documented scheduling policy or ask for a date if the statement defines a range. "After lunch", "before the meeting", and "three days before Friday" need supported anchors/offsets or clarification; the current fallback must not quietly discard their meaning. Preserve future-time checks and strict DST ambiguity/nonexistence handling. Unsupported recurrence is clarified rather than approximated.

### Entity and correction resolution

Resolve explicit plan names against the owner's plans; otherwise use the active plan only when attribution is unambiguous. Do not merge two plans solely because they share a name. Keep a bounded recent action receipt for pronouns such as "move that to Thursday".

Resolve updates through owned candidates, active-plan scope, current titles/context, and revision checks. If two presentations match, ask which one. "No, the other project" updates the active target only after resolution; it must not move existing tasks in bulk. Explicit completion/cancellation and recurrence changes must use the existing task/reminder semantics.

### Two distinct duplicate barriers

1. **Execution replay:** store the validated batch; use stable server-generated tool-call IDs per action and the existing PostgreSQL idempotency store. A response retry uses the same original turn/batch/actions, never a new set of model-generated execution identities. Keep original date resolution stable when replaying after midnight.
2. **Conversational duplicates:** within owner/plan, find active tasks using normalized action/title identity, then compare intent and scheduling. A due date is mutable data, not the sole task identity. A repeated paraphrase is a no-op; a clearly stated correction updates the existing record. Semantic similarity may help shortlist later, but an uncertain match never authorizes an automatic destructive merge.

Serialize candidate resolution/creation within an owner/plan lock or equivalent database-safe claim. Add unique action/batch keys and meaningful concurrent integration tests. Exact retry keys alone cannot prevent two simultaneous paraphrased turns from creating duplicates. Do not deduplicate different recurring occurrences or distinct work items merely because their titles match.

## 8. Execution, confirmation, and failure handling

Keep `requires_confirmation=True` for write tools. Extend the executor with a proposed server-owned `PlanningAuthorization` grant, bound to user/session/turn/action, tool name, normalized argument digest, resolved target/revision, mode-state version, policy version, expiry, and cancellation state. Ordinary LLM calls cannot create or inherit this grant. Check it after normalization; if neither a valid planning grant nor a manual confirmation exists, use the existing confirmation behavior.

The grant authorizes only the allowlisted low-risk operation/fields. Revalidate the feature flag, account/device/session authority, mode version, owned target, and current cancellation state immediately before writes/commit. A forged client flag, stale enabled snapshot, model-produced grant, or reused grant for different arguments must fail closed.

Provide a bounded planner batch budget through server execution context: multiple allowed task/reminder creates are permitted within the configured total action limit. Preserve current per-tool limits for ordinary Normal-mode calls. Respect overall tool wall time/call budgets; plan/context actions also consume budget. Plan operations need registered server-owned tools/handlers or a similarly audited executor adapter; they cannot bypass validation merely because their tables are new.

Use sequential writes through the existing async database session. For each independent auto action, persist the mutation, idempotency completion, action result, and audit in the same transaction, then commit before emitting a success event or TTS acknowledgement. Explicitly coupled operations such as creating a task with its linked reminder form one transaction group; if either fails, report both as unsaved.

Partial success is permitted across independent groups, with a per-action receipt. A failed item must not roll back already committed siblings or be described as saved. On disconnect after commit, receipts are recoverable; retries replay committed results. Cancellation before commit rolls back that group; cancellation after commit suppresses obsolete speech but does not silently reverse durable data.

Mode off must not wait behind a long extraction transaction. Use short transactions and a mode-version check with serialization that prevents an off acknowledgement preceding a stale automatic commit. Add a barrier test for this race. Consequential proposals use the current Redis voice-confirmation store and UI card, one pending proposal at a time initially; planner proposals queue without executing until each is explicitly approved. "Yes" is confirmation input, not new plan content.

If extraction times out, is malformed, or has no grounded candidate, perform no inferred writes. Explain that organization could not complete when needed and allow ordinary conversational answering with a truthful receipt. Suppress a second mutation path for actions already handled or deferred by the planner. Already explicit manual requests may retain their existing confirmed tool path when no planner action consumed them. Do not re-extract a persisted batch simply because TTS or answer generation failed.

## 9. Memory and privacy boundaries

Plan context serves the current structured plan; durable personal memory is an optional downstream write through `MemoryWriter` and existing extraction/policy controls. A plan/task is always queried from its structured source of truth. RAG/OKF can provide bounded project facts when permitted but cannot decide authorization or mutate organizational records.

For durable memory promotion, preserve grounded evidence, `source_session_id`, `source_turn_id`, supported source kind, confidence/salience checks, account memory setting, session exclusion, and write flags. Existing OKF synchronization depends on eligible structured memories and source sessions; a plan record or generic note is not automatically an OKF concept. Extend memory mapping only as needed and let the current worker perform asynchronous OKF synchronization. Memory/OKF failures do not invalidate an already saved task; report context promotion separately.

Proposed v1 privacy policy: `memory_enabled=false` disables personal-memory retrieval/promotion but does not revoke explicit Plan Mode consent for structured organization. For a session marked `memory_excluded=true`, suppress proactive inferred persistence, including plan notes and proposal content; explain that automatic organization is unavailable in that private session. Explicit Normal-style task/reminder commands keep their current policy. This conservative distinction must be visible in the UI and tested before rollout.

Session reset/off revokes execution authority and clears ephemeral referents. Session-content purge removes related extraction evidence and disallows stale memory retrieval while preserving saved tasks/plans according to the supported resource deletion policy. Deletion of a plan requires a concrete confirmation describing whether tasks remain ungrouped or will be deleted; v1 defaults to archiving the plan and retaining tasks/reminders.

Metrics contain counts, IDs, reason codes, versions, duration, and outcomes. Redaction helpers do not by themselves make a full transcript safe to log. No raw plan text, personal context, or source spans in general telemetry. Retained proposal content follows the same account/session privacy controls and documented retention lifecycle.

## 10. Proposed API and WebSocket contracts

Follow the existing API mounting convention; these are proposed relative paths, not current endpoints:

| Endpoint | Behavior |
|---|---|
| `POST /plans` | Create an owned plan; validate bounded name/goal/timezone. |
| `GET /plans` | Paginated/filterable owned plans. |
| `GET /plans/{plan_id}` | Summary/context/progress; linked items paginated. |
| `PATCH /plans/{plan_id}` | Revision-checked owned update, including archive status. |
| `GET /plans/{plan_id}/actions` | Bounded action receipts visible only to the owner. |
| Existing tasks/reminders endpoints | Add optional plan filtering/grouping fields without breaking old payloads. |

Use the active voice socket for mode/active-plan control in v1, avoiding conflicting REST and WebSocket authority. Add typed `client.planning.set_mode` and `client.planning.select_plan`, including event ID, session ID, expected state version, and mode/plan selection. The client never supplies action grants, ownership, executable proposal payloads, or confidence thresholds.

Server events: `server.planning.state`, `server.planning.actions`, and a bounded planning error/clarification event. Include session ID, event ID, state/plan revision, and turn/response correlation when relevant. Session-ready/resume includes a validated planning-state snapshot. Clients ignore stale-session, duplicate, and out-of-order versions. Saved action receipts remain authoritative even when their original audio response was cancelled.

Unknown new controls remain rejected by older servers; advertise planning capability before UI activation. Upgrade `protocol.py`, gateway dispatch, Kotlin transport message construction/payload parsing, native event emission, TS native types, VoiceSocket event lists/parser/reducer/snapshot, and provider together. Add fixture tests for full planning payload propagation, not just event names.

## 11. Mobile behavior

Add a Normal / Plan selector to `AssistantScreen`, a persistent "Plan Mode is on" indicator, active-plan name, and a concise explanation of automatic tasks/reminders. Provide voice/UI disable and clear feedback for private sessions, unavailable server capability, pending controls, and reconnects. Do not create a new microphone/listening lifecycle; continue using the existing audio session.

Add a plan detail screen/card with goal, deadline, context, derived progress, and grouped Today / Up next / dated tasks. Completed items remain accessible. Missing deadlines have a clear undated section. Global Tasks remains available and refreshes after committed planning receipts; stale plan/task data reloads from the API after resume.

Each automatic action appears as a saved/updated/duplicate/skipped receipt with target ID. Provide edit/reschedule and a confirmed removal action; an undo cannot silently overwrite later manual edits or delete a task that has changed. Keep external/destructive confirmations visible through the existing `ToolConfirmationCard`. Add accessibility labels, loading/error states, and localized strings using the existing theme/components.

## 12. Proposed file map

| Files | Planned work |
|---|---|
| New `backend/app/planning/{types,extraction,policy,repository,service,resolution}.py` | Strict proposals, source validation, policy, durable state/batches, orchestration, target/dedupe resolution. |
| New `backend/app/llm/plan_tools.py` | Owned plan/context handlers through the executor; registry integration. |
| New `backend/app/api/plans.py` | Owner-scoped read/update endpoints. |
| `backend/app/models/resources.py`, `models/__init__.py`, `schemas/resources.py` | Add planning models, ownership/revisions, grouping/provenance and response contracts. |
| New Alembic revision(s) after verified current head | Additive tables/nullable fields/indexes/constraints; migration checks on existing data. |
| `backend/app/core/config.py`, `.env.example`, `backend/app/main.py` | Disabled defaults, validation, service initialization, bounded extraction capacity. Do not edit local `.env` automatically. |
| `backend/app/websocket/{protocol,gateway}.py` | Mode controls/state/resume events, planning dispatch, retry/cancellation/commit integration. |
| `backend/app/services/voice_persistence.py`, `voice_confirmation.py`, `audit.py` | Owned lifecycle integration, queued confirmed proposals if needed, metadata-safe receipts. |
| `backend/app/llm/{tool_loop,task_tools,reminder_tools,context}.py` | Action-bound grants, planner budgets, grouping/source fields, candidate time evidence, truthful answer receipts. |
| `backend/app/services/task_due_dates.py` | Only validated temporal extensions needed for offset/anchor cases, preserving existing Normal behavior. |
| `backend/app/api/{routes,tasks,reminders}.py` | Register plans API and compatible plan filters/revision-aware updates. |
| Memory writer/extraction integration where needed | Optional sourced context promotion using existing memory policy; no parallel memory store. |
| New `frontend/src/plans/{api,types}.ts`, plan components/detail screen | Owned plan data and visualization. |
| `frontend/src/screens/{AssistantScreen,TasksScreen}.tsx`, navigation and strings | Visible mode, active plan, grouping, data refresh, accessibility. |
| `frontend/src/voice/{VoiceSocket,VoiceSocketProvider,conversation}.ts(x)` | State/events, acknowledgements, stale-event rules and receipts. Confirm exact extensions for each file. |
| `frontend/src/native/VoiceModule.ts` | Typed native controls and planning payloads. |
| `frontend/android/app/src/main/java/com/voiceaipoc/rn/VoiceModule.kt` | Expose planning controls and emit validated payloads. |
| `frontend/android/app/src/main/java/com/voiceaipoc/voice/VoiceWebSocketTransport.kt` | Build controls, parse state/actions, retain session correlation. |
| New focused planning backend/frontend/native tests and conversation fixtures | Validate consent, extraction, persistence, races, retries, UI and transport. |

Existing task mutations are implemented in REST endpoints and tool handlers rather than one universal task domain service. Initially extend the shared models/executor and existing handlers. If a shared mutation service becomes necessary for revision/dedup consistency across REST and voice, extract only that bounded common logic; do not build a separate planner task implementation.

## 13. Implementation phases and todo

All feature items below are pending. Complete each phase's checks before marking it implemented.

### PM-0 — Freeze contracts and baseline

- [ ] Record HEAD, existing working-tree changes, migration heads, effective non-secret settings, and applicable acceptance status at implementation start.
- [ ] Freeze actionability examples, consent/private-session policy, session lifecycle, plan identity, date-only behavior, recurrence subset, and automatic-operation allowlist.
- [ ] Add labeled conversation fixtures covering the supplied scenarios and negative/correction/replay cases.
- [ ] Establish configuration defaults: `PLAN_MODE_ENABLED=false`, `PLAN_EXTRACTION_MODE=off` (`off|shadow|on`), `PLAN_AUTO_ACTIONS_ENABLED=false`, explicit test-owner allowlist, bounded timeout/concurrency/action count, and `PLAN_POLICY_VERSION=plan-v1`.
- [ ] Require feature enablement plus extractor `on` plus auto flag plus active session consent for automatic writes. Shadow forbids persistence of actions/plan context; keep privacy-filtered observations only.

**Gate:** behavior is concrete and reviewable; all planning defaults preserve current behavior.

### PM-1 — Durable schema and mode controls

- [ ] Add owner-scoped plans/context, planning sessions, batches/actions, and nullable task/reminder grouping/provenance with revision checks.
- [ ] Add mode enable/disable/select controls and shared voice recognition, server acknowledgements and resume snapshots.
- [ ] Revoke state on reset/end/timeout/logout and serialize disable against execution.
- [ ] Add compatible owner-scoped plan APIs and tests; existing task/reminder payloads continue working.

**Gate:** UI and voice can select a persistent session mode; this phase performs no inferred organizational writes.

### PM-2 — Extraction and validation in shadow

- [ ] Implement one bounded structured extraction request per eligible final turn with the existing model service.
- [ ] Validate source/action/temporal evidence, actor attribution, negation/quotes/hypotheticals, multi-action output, and active-plan context.
- [ ] Add candidate-specific time resolution and unsupported-anchor/offset clarification.
- [ ] Persist proposals only where privacy policy permits; shadow observes without mutations or response-path ownership.
- [ ] Measure candidate precision, false actions, target ambiguity, duplicate decisions, timeout rate, and added latency using labeled fixtures and held-out conversations.

**Gate:** grounded proposals are accurate enough for the defined corpus; no shadow-created task/reminder/memory exists.

### PM-3 — Policy and existing tool integration

- [ ] Implement action-bound authorization grants and Normal-mode confirmation regression tests.
- [ ] Add the bounded planner budget so several tasks/reminders can be executed within one turn without globally raising limits.
- [ ] Route plan/task/reminder/context writes through validated owned handlers/executor.
- [ ] Add stable replay identity, semantic duplicate resolution, target revisions and concurrent creation protection.
- [ ] Commit each transaction group and receipt before any success event or speech; support partial outcomes.
- [ ] Queue consequential proposals through existing confirmation machinery, preserving manual confirmation behavior.

**Gate:** an enabled conversational turn creates multiple intended records once, with correct per-action dates; consequential operations cannot execute automatically.

### PM-4 — Continuity, corrections, and privacy

- [ ] Resolve pronouns, explicit plan switches, duplicate paraphrases, rescheduling and completion against owned structured state.
- [ ] Connect optional context promotion to the existing memory consent/source-session/write policy.
- [ ] Test memory disabled, private/excluded sessions, source purge, mode disable races, cancellation before/after commit, restart and replay after midnight.
- [ ] Keep reminder occurrence/delivery identities intact during updates.
- [ ] Add confirmed archive/removal semantics and prevent stale undo from overwriting later user edits.

**Gate:** realistic multi-turn conversation remains consistent and cannot cross user/device/session privacy boundaries.

### PM-5 — Mobile/native integration

- [ ] Update the complete WebSocket -> Kotlin parser -> native bridge -> TS parser/state -> UI path.
- [ ] Add mode selector/status, active-plan card/detail, receipts and grouped task presentation.
- [ ] Refresh global tasks/plans after commits; recover server state on resume; handle stale events and unsupported servers.
- [ ] Add targeted Jest/native tests, type checks, accessibility/error-state coverage.

**Gate:** user can clearly see consent/mode state and every saved change, including after reconnect.

### PM-6 — Acceptance and controlled rollout

- [ ] Run focused backend tests plus PostgreSQL/Redis integration checks for migrations, transactions, grants, concurrent dedupe, restart, worker delivery and privacy.
- [ ] Run focused frontend tests/type checks and targeted native transport tests; build/install only when native transport changes require a device artifact.
- [ ] Validate held-out end-to-end conversation corpus and existing Normal-mode/router regressions.
- [ ] Capture actual provider and physical Android evidence for enable -> conversation -> persisted multi-action plan -> correction -> reminder receipt -> disable -> Normal.
- [ ] Enable only explicit disposable/test owners first, then a measured cohort, while preserving existing router/voice release gates.
- [ ] Demonstrate rollback: feature/execution switches revoke grants immediately and keep existing saved data readable; scheduled reminders continue unless explicitly cancelled.

**Gate:** all critical policy/ownership/replay/race cases pass; live evidence covers the intended UI and voice flow. Existing unrelated acceptance gates remain separate.

## 14. Acceptance matrix

| Scenario | Required result |
|---|---|
| Normal mode, casual project discussion | No planner writes; existing behavior preserved. |
| UI/voice enable and disable | Acknowledged server state, visible UI mode; false activation examples rejected. |
| XYZ technologies only | Plan/context as applicable; zero inferred setup tasks. |
| XYZ backend tomorrow + presentation/report Friday + meeting Friday 4 PM | Three tasks with distinct correct deadlines and one reminder; task grouping and provenance present. |
| Monday API / Tuesday review / Friday project in one turn | Each action uses its own grounded date; no transcript-wide date override. |
| Six explicit commitments in one turn | Six tasks saved once within the planner budget; Normal per-tool limits unchanged. |
| "I'm thinking about a project" / quote / negation / historical statement | No inferred task/reminder. |
| "Friday at 4" / unsupported relative anchor | Clarification before reminder write; unsupported meaning not dropped. |
| Repeated presentation paraphrase | One task; duplicate receipt or clearly supported update. |
| "Actually, Thursday" with one/two possible targets | One owned revision-checked update, or clarification; never duplicate creation. |
| "I finished it" | Complete only a uniquely resolved owned task; progress follows committed state. |
| Supported/unsupported recurrence | Validated existing recurrence semantics, or clarification. |
| Send email / book / buy / share | No automatic external action; unsupported capability stated or explicit confirmation if separately implemented. |
| Delete/cancel/undo task or plan | Concrete confirmed scope; later edits protected. |
| Forged grants/client mode/IDs or cross-user resources | Denied/owner-safe not found; no writes. |
| Disable while extraction/write is paused | No automatic commit after acknowledged revocation. |
| Response retry, duplicate event, reconnect, restart | Same committed action IDs/results; no repeated task/reminder. |
| Two concurrent duplicate turns | One canonical task/reminder according to defined identity rules. |
| Cancellation before/after commit | Unsaved group rolled back / saved receipt recoverable without obsolete speech. |
| Task + linked reminder transaction fails | Both reported unsaved; no dangling scheduled reminder. |
| One independent action fails | Prior saved actions remain; failure reported accurately. |
| Memory off / session excluded / source purge | Defined separation of structured consent and memory privacy; no prohibited context persistence/retrieval. |
| Router off/shadow/on, rag/okf preferences | Planning independent; shadow cannot write and knowledge choice stays intact. |
| Native event carries plan/action fields | Full payload reaches JS; stale-session/revision events ignored. |
| Feature disabled or older server | Normal behavior and existing APIs preserved; no optimistic Plan Mode UI. |

Release requirements include zero observed unauthorized/destructive/external actions, zero cross-owner writes, zero duplicate writes in retry/concurrency fixtures, and correct per-action dates in all defined temporal acceptance cases. Suggested extraction targets are at least 95% actionable-candidate precision and 90% recall on a held-out labeled corpus; calibrate thresholds with measured results rather than trusting the model's self-reported confidence. Report coverage and uncertainty when the corpus is small. Set a latency target after baseline measurements; the current review establishes no live planning latency guarantee.

## 15. Completion definition

The feature is complete when a user can explicitly enable Plan Mode, speak actionable plans naturally, receive accurate saved tasks/reminders and organized context without repeated organizational confirmation, correct the same plan across turns, and disable the mode reliably. All writes must remain owned, grounded, audited, replay-safe and governed by the operation-specific confirmation policy. The current work completes only the source review and implementation document; every implementation checkbox remains pending.
