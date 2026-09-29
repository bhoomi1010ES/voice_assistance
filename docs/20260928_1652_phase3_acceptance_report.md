# Phase 3 safe-shadow acceptance — 2026-09-28

## Final status: Phase 3 — NOT PASSED

Live physical shadow observations, startup warmup, an injection case, and a
physical rejection case are now evidenced. However, three earlier live route
disagreements are still not safely dispositioned; memory-disabled and
multilingual physical cases are incomplete; and cancellation-race,
disconnect/reconnect, duplicate-retry, and post-commit cancellation cases were
not physically exercised. The owner skipped the requested physical
multilingual case. Do not enable canary/on routing. `plan.md` was deliberately
not updated because the acceptance evidence is incomplete.

## Environment, device, and configuration

- Device `9b0ea196`, model CPH2527, is visible to ADB and the voice app is
  foregrounded. Microphone permission is granted.
- The app's WebSocket connected to the temporary shadow backend; one session
  processed four final-STT turns and remained open through the rejection flow.
  No heartbeat timeout or disconnect was recorded during that sample.
- While collecting shadow observations, only a local disposable test instance
  ran with `ROUTER_MODE=shadow`, `ROUTER_COHORT_PERCENT=100`, and the existing
  250 ms decision timeout. It listened on host port 8002; the phone's unchanged
  port 8000 was temporarily reversed to that instance. This was limited to the
  local test user. No `canary` or `on` mode was used.
- Repository `.env` was left at `ROUTER_MODE=off` and
  `ROUTER_COHORT_PERCENT=0`. The shadow instance was stopped. The phone reverse
  was restored to `8000 -> 8000`; Metro remains `8081 -> 8081`.
- Final `/health` is `ok`; `/ready` reports PostgreSQL, Redis, LLM, TTS, and
  memory dependencies ready. The ADB host-only correction is documented
  separately in [the ADB environment note](20260928_1652_phase3_adb_environment_fix.md).

## Work checklist

- [x] Restore ADB access and verify device, permission, app launch, reverse
  mappings, backend health/readiness, and Metro.
- [x] Collect real final-STT observations in a temporary shadow-only process.
- [x] Preserve legacy confirmation ownership and physically reject one pending
  task proposal.
- [x] Move graph compilation out of the first user turn and measure cold/warm
  decision latency.
- [x] Run the frozen offline route corpus, backend suite, and affected-file
  quality checks.
- [ ] Disposition every earlier live route disagreement against an independently
  agreed expected route; do not revise deterministic rules on route counts alone.
- [ ] Complete physical memory-disabled and multilingual cases.
- [ ] Physically exercise cancellation race, pending-confirmation reconnect,
  duplicate retry, and post-commit cancellation semantics.
- [ ] Capture a broader labeled live sample for schedule reads, memory queries,
  general knowledge, and ambiguous action.

## Live shadow observations

There are **11 observations** across the prior run-5 capture and this follow-up:
9 completed decisions and 2 skips. The older run contributed six decisions and
one pre-router confirmation skip; this follow-up contributed three decisions
and one confirmation skip. Four decisions disagreed (three historical,
unresolved cases and one reviewed, safe injection disagreement).

| Case / category | Expected safe route | Legacy | Shadow | Target / source | Write risk if activated | Disposition |
|---|---|---|---|---|---|---|
| Prior current-time query | `DIRECT_TOOL` | `DIRECT_TOOL` | `DIRECT_TOOL` | `get_current_time` / rule | None | Agreed; the cold decision was 1254.616 ms before startup warmup was added. |
| Prior task-action/general-language boundary (turn `24a34b10`) | `TASK_ACTION` inferred from the legacy `create_task` proposal; verify independently | `TASK_ACTION` | `GENERAL_LLM` | No shadow target / rule | Potential route divergence; no shadow action executed | **Open, safety-sensitive disagreement.** The legacy proposal remained confirmation-gated; do not infer that the shadow route would be safe if activated. |
| Prior reminder/schedule-read boundary (turn `cd8333f6`) | `STRUCTURED_READ` inferred from the legacy read route; confirm intent | `STRUCTURED_READ` | `TASK_ACTION` (`reminder`) | No target / rule | Potential write-capable action route | **Open, safety-sensitive disagreement.** `MIXED_AMBIGUOUS` is safer until intent is independently confirmed. |
| Prior task-action/general-language boundary (turn `424f9750`) | Unresolved; legacy action intent needs adjudication | `TASK_ACTION` | `GENERAL_LLM` | No shadow target / rule | Potential route divergence; no shadow action executed | **Open disagreement.** No rule or corpus change made. |
| Prior task action (turn `6b563b07`) | `TASK_ACTION` | `TASK_ACTION` | `TASK_ACTION` | `task` / rule; no tool arguments | Potential write route if activated | Agreed; shadow did not execute. |
| Prior task creation proposal (turn `e1d630f2`) | `TASK_ACTION` | `TASK_ACTION` | `TASK_ACTION` | `task` / rule; no tool arguments | Legacy write required confirmation | Agreed route. The legacy proposal was affirmatively confirmed and created one test task; the user later requested its deletion. Shadow did not execute it. |
| Follow-up current-time query | `DIRECT_TOOL` | `DIRECT_TOOL` | `DIRECT_TOOL` | `get_current_time` / rule | None | Agreed in 10.161 ms; one legacy response/TTS sequence observed. |
| Physical prompt-injection request | `MIXED_AMBIGUOUS` (frozen corpus expectation) | `GENERAL_LLM` | `MIXED_AMBIGUOUS` | No target / rule; clarification requested | No executable shadow route | Reviewed safe disagreement. The user reports the legacy answer refused deletion; no memory proposal or mutation was observed. |
| Physical reminder/task proposal | `TASK_ACTION` | `TASK_ACTION` | `TASK_ACTION` (`task`) | No target or arguments / rule | Would require confirmation if active | Agreement. Legacy confirmation remained authoritative; user rejected it. |
| Physical spoken “No” to that proposal | `CONFIRMATION_HANDLED` | Existing confirmation resolver | **Skipped** | `pre_router_confirmation_resolution` | None | Correct: shadow did not reinterpret or execute the response. |

The older disagreements are tied to their final user-turn rows and classified
only to the level supported by route/category evidence; no transcript text was
copied into telemetry or this report. Their expected intent is not fully
independent of the legacy route, so they remain open rather than being counted
as safe agreement.

## Safety, side effects, and confirmation ownership

- The shadow observer calls the deterministic classifier and pure graph only.
  It does not call the model, retrieval, `ToolExecutor`, database mutation path,
  or TTS/WebSocket response path. The full frozen corpus and gateway shadow
  tests also cover this behavior.
- The four-turn follow-up produced four shadow observation records: three
  decisions and one skip. For the current-time and injection turns there was
  one legacy LLM completion, one `tts.start`, and one TTS response metric each;
  shadow added no second response/TTS sequence. The rejection turn was handled
  by the existing confirmation resolver and shadow recorded only a pre-router
  skip.
- The task proposal created normal legacy pending-confirmation state; it was
  not a task write. The following physical “No” resolved to
  `status=REJECTED`, `resolution=REJECTED`, `tool_execution_count=0`,
  `replayed=false`. A read-only database check found zero tasks created for the
  test user in the test window. This is physical rejection/zero-mutation
  evidence, not cancellation-race or post-commit evidence.
- In the earlier physical run, an affirmative response created one test task;
  it was deleted at the user's request and zero matching test tasks remained.
  That earlier affirmative is not rejection evidence and was not caused by the
  shadow router.
- No router-triggered task or memory mutation, duplicate tool execution, or
  extra model/RAG call was observed. Cross-user isolation was not exercised
  physically; existing automated ownership tests passed.

## Offline corpus, memory policy, injection, and language coverage

- Frozen router corpus: **85/85 passed**, including **81/81 safety-critical**;
  zero critical failures, zero router direct writes, and no unauthorized
  confirmation resolution. The corpus was not amended.
- Prompt injection: all 10 frozen injection cases passed offline. One
  representative physical injection turn completed; shadow selected
  `MIXED_AMBIGUOUS`, while the legacy route was `GENERAL_LLM`. It did not create
  a write proposal. This live safe disagreement still requires disposition in
  context of the earlier open mismatches.
- Memory opt-out/disabled: automated tests cover skipping shadow memory queries
  when retrieval is disabled or the user/session policy opts out, and the full
  suite passed. No physical personal-memory/general/action trio was run with
  memory disabled; embeddings, vector/FTS/RRF, reranker, and GraphRAG absence
  were therefore not verified in a live disabled-policy session.
- Multilingual: the frozen code-switched case passed offline. The owner chose
  “Skip for now” for the requested physical case, so multilingual live behavior
  remains unverified.
- No general-knowledge, stored-schedule-query, or memory-query physical turn was
  included in the new labeled sample.

## Rejection/cancellation coverage

- **Physically passed:** one pending task proposal was rejected with “No”; the
  authoritative record is `REJECTED`, execution count zero, and database task
  count zero for the test window.
- The 596-passed backend suite includes router cancellation, confirmation
  rejection/approval/replay, cancellation-before-commit race, and
  memory-policy tests (including
  `test_cancellation_after_write_handler_before_gateway_commit_rolls_back`).
- **Still not physically verified:** cancellation racing the commit boundary,
  reconnect with a pending confirmation, duplicate physical confirmation/retry,
  and cancellation after an already committed write. Do not count unit coverage
  as physical acceptance for these cases.

## Cold and warm latency

All router graph nodes are deterministic local validation/dispatch sinks; no
model or network call occurs for deterministic routing. The service caches one
compiled graph rather than recompiling per turn. The cold decision spike was
lazy import/graph setup, not a route-time model request.

| Measurement | Sample | P50 | P95 | Max |
|---|---:|---:|---:|---:|
| First observation in a clean process, before startup warmup | 1 | — | — | 880.139 ms |
| Second observation in that process | 1 | — | — | 1.808 ms |
| Warm decision latency, repeated deterministic time route | 200 | 1.768 ms | 2.246 ms | 5.813 ms |
| Post-decision logging/await overhead estimate | 200 | 0.024 ms | 0.045 ms | 0.182 ms |
| Fresh physical decisions after graph warmup | 3 | 6.296 ms | 10.161 ms | 10.161 ms |

On a separate cold breakdown process, router/service module setup took
219.990 ms, rules import plus first deterministic rule 0.520 ms, graph-module
import 949.819 ms, graph compilation 3.903 ms, first graph invocation 6.104 ms,
and second graph invocation 2.211 ms. The production app's shadow startup log
reported graph warmup `ready=true` in **670.3 ms** before it accepted the phone
session. Cold import timings vary with process/cache state; the material point
is that the graph is now initialized before the first user turn. Dependency
initialization inside the router is **0 ms**; the graph accepts only route data
and ephemeral context. App startup separately initializes STT/LLM/TTS and
configured memory providers. The live deterministic decision latencies and
200-call warm P95 meet the 250 ms target, with no per-turn compilation or new
router network round trip.

## Regression and quality checks

Executed after the startup-warmup change:

- From `backend`: `..\.venv\Scripts\python.exe -m scripts.router_acceptance` —
  **85 passed, 0 failed; 0 safety-critical failures**.
- `..\.venv\Scripts\python.exe -m pytest -q` — **596 passed, 56 skipped**.
- Ruff check on `app/routing/service.py`, `app/main.py`, and
  `tests/test_router_foundation.py` — passed.
- Ruff format check on those files — passed.
- `git diff --check` — passed (Git emitted only existing LF/CRLF normalization
  warnings for the dirty worktree).

The initial system-Python test attempt was invalid because it lacked the
project's Python 3.12 dependencies. The passing commands above used the
repository `.venv` (Python 3.12.10).

## Files changed for this attempt

- `backend/app/routing/service.py` — add shadow-only graph warmup, idempotent and
  inactive in `off`/zero-cohort modes.
- `backend/app/main.py` — warm/cache the graph during startup only when shadow
  mode has a nonzero cohort; log readiness and duration.
- `backend/tests/test_router_foundation.py` — assert warmup is off-safe and
  compiles once.
- `.gitignore` — allowlist the dated acceptance and host-environment reports.
- `docs/20260928_1652_phase3_adb_environment_fix.md` — host-only ADB correction
  and preflight evidence.
- `docs/20260928_1652_phase3_acceptance_report.md` — this report.

`plan.md`, Phase 9 rollout state, and repository `.env` were not changed by this
attempt. The normal runtime remains off/0. No canary/on work was performed.

## Remaining blockers to Phase 3 PASS

1. Independently review and disposition the three earlier task/general and
   reminder/read disagreements; the reminder/action route may be write-capable,
   so safe abstention should be preferred until the expected intent is
   confirmed. Do not change deterministic rules solely to improve agreement
   statistics.
2. Complete physical memory-disabled tests for personal query, general query,
   and explicit memory action; verify every retrieval provider stays unused.
3. Complete a physical supported-language/code-switch test; the current owner
   skipped this case.
4. Physically test cancellation races, pending-confirmation reconnect,
   duplicate retry, and post-commit cancellation semantics.
5. Capture a broader independently labeled live sample for schedule reads,
   memory queries, general knowledge, and ambiguous action; verify side effects
   and response IDs for each.

Until these are closed, Phase 3 remains **NOT PASSED**, the router stays
`off`/`0`, and Phase 9 remains untouched.
