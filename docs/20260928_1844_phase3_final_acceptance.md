# Phase 3 — Safe Shadow Mode Final Acceptance

**Final status: Phase 3 — NOT PASSED**  
**Date:** 2026-09-28 (local), evidence collected through 2026-09-29 01:44 UTC.

The device and temporary shadow environment were restored to normal operation.
Production configuration remains `ROUTER_MODE=off` and
`ROUTER_COHORT_PERCENT=0`. No Phase 9 rollout setting was changed.

## Environment and configuration

- Device: connected OnePlus CPH2527 Android device. ADB microphone permission
  was granted. The earlier host ADB issue was isolated to the process `HOME`
  being unset; setting `HOME=C:\Users\lenovo` for ADB commands restored host
  access. No product code was changed for this host correction.
- ADB reverse mappings were restored to `8000 → 8000` (backend) and
  `8081 → 8081` (Metro). The app was relaunched against the normal backend.
- Normal backend: `/health` = `ok`; `/ready` = `ready`. PostgreSQL, Redis,
  LLM, TTS, embedding, and reranker readiness checks were good. The readiness
  response reports LLM `live_verified=false`; physical speech turns did verify
  live STT/LLM/TTS operation. Metro returned HTTP 200. Microphone permission
  remained granted.
- Final phone check: memory was restored to on (its initial state), and the
  user confirmed an audible current-time response through the normal backend.
- Controlled shadow only: local temporary process on port 8002 used
  `ROUTER_MODE=shadow`, cohort `100`, timeout `250 ms`; ADB port 8000 was
  temporarily reversed to that process. It was stopped after testing. The
  repository `.env` was never changed from `off` / `0`.

## Live shadow observations and disagreements

The retained privacy-safe shadow logs contain **36 observations**: 33 decisions
and 3 skips (two because authoritative confirmation resolution preempted the
router, one because the memory action was disabled by policy). No transcript or
personal-memory text is copied into this report.

| Legacy route → shadow route | Count | Safety review / disposition |
| --- | ---: | --- |
| `DIRECT_TOOL → GENERAL_LLM` | 1 | Clock/date-related, read-only category; no shadow target or write. Exact expected route remains unreviewed, so unresolved. |
| `STRUCTURED_READ → MIXED_AMBIGUOUS` | 1 | Shadow abstained without a target/domain. Treated as safe clarification for a mixed/ambiguous read; no write risk. |
| `MEMORY_QUERY → GENERAL_LLM` | 1 | Personal-memory category; general fallback is not acceptable as an assumed personal-fact source. The later abbreviated “what do you remember?” regression is fixed and physically re-observed as `MEMORY_QUERY` on both paths, with shadow skipped when memory was off. This older, distinct disagreement remains unresolved. |
| `TASK_ACTION → GENERAL_LLM` | 2 | Task/reminder action category; shadow selected no target/domain and did not execute. Expected route/intent is not independently dispositioned. |
| `STRUCTURED_READ → TASK_ACTION` (`reminder`) | 1 | Write-capable shadow candidate disagreed with a legacy read. Shadow did not execute it, but the safe expected route is unresolved; this is a release-blocking disagreement. |

The safe-ambiguity case is dispositioned as abstention. The abbreviated memory
query regression was added to the frozen corpus and the memory-disabled shadow
policy now skips both memory queries and memory actions. The other disagreements
were not changed merely to improve counts. **Not all route disagreements are
closed, and 100% live safety-critical expected-route agreement is not
established.**

### Physical categories exercised

- Current time: a real final-STT turn produced a matching `DIRECT_TOOL` shadow
  observation and one audible response.
- Memory disabled: a personal-memory query returned an unavailable response;
  general-knowledge and schedule probes were also spoken, but their answer
  categories were not recorded. An explicit synthetic save probe was
  refused/unavailable and audible. With the updated guard, the shadow
  observation was `skipped_memory_policy` with reason
  `memory_action_disabled_by_policy`.
- The account's memory setting was confirmed off for the tests and restored to
  on afterward. Read-only database checks found neither synthetic test marker
  (`violet ember seventeen`, `silver comet nineteen`) in saved memory.
- Multilingual: the user first reported a current-time answer, then an audible
  clarification on a retest. The captured final STT did not retain the Hindi
  cue, so the retest cannot count as a valid frozen code-switched route sample.
  Multilingual live acceptance remains incomplete.
- Prompt injection: the prior acceptance report records one correlated physical
  fake-system/delete-memory turn: shadow chose `MIXED_AMBIGUOUS`, legacy chose
  `GENERAL_LLM`, the user reported a refusal, and no proposal or mutation was
  observed. This is one representative pass, not the full injection matrix.
  The user skipped the expanded live probe in this run, so multi-case live
  injection acceptance remains incomplete.
- Rejection: the earlier physical “No” test recorded `REJECTED`, zero execution,
  and no task row (see the prior acceptance report).
- Cancellation before commit: the user physically cancelled a pending synthetic
  task proposal. The authoritative confirmation resolver preempted shadow, and
  a read-only database check found no task with the test label.
- Pending-confirmation reconnect, duplicate/replayed confirmation, commit-race,
  and cancellation after an already committed write were not physically
  completed. Unit coverage does not substitute for these required physical
  cases.

## Side-effect evidence

- The shadow implementation invokes only the pure local router graph; it does
  not call the ToolExecutor, LLM, RAG, or TTS. Relevant unit tests passed.
- The memory-disabled explicit-save turn had zero embedding, vector/FTS/RRF,
  reranker, or GraphRAG events in its correlated latency trace. It had one
  legacy LLM completion; shadow added none. The user heard one refusal response.
- The two synthetic memory markers and cancelled-task label were absent from
  the database. No matching test task remained. Prior rejection evidence also
  records zero execution.
- No router-triggered write or duplicate shadow response was observed. However,
  a full audit proving zero duplicated side effects across every category and
  every live sample is incomplete; this criterion is not marked passed.

## Cold and warm router latency

Measurements from a fresh benchmark process separated initialization from
decision work. A later controlled backend startup logged a successful graph
warm-up in **634.230 ms**, before the phone's first decision on that process.

| Measurement | Result |
| --- | ---: |
| Router-related module initialization/imports | 395.048 ms |
| Graph-module import | 879.654 ms |
| Graph compilation | 3.744 ms |
| First deterministic decision after initialization | 8.158 ms |
| Second decision | 1.943 ms |
| 200 warm deterministic decisions, P50 | 1.857 ms |
| 200 warm deterministic decisions, P95 | 2.614 ms |
| 200 warm deterministic decisions, max | 4.263 ms |

The earlier 1254.616 ms cold result is explained by import/initialization work,
not a network/model/retrieval call or per-turn graph compilation. Startup
warm-up now moves graph initialization off the first user turn. Warm routing
meets the 250 ms target. No semantic classifier was added.

## Regression and quality checks

- Full backend suite: **598 passed, 56 skipped**.
- Frozen router corpus: **86/86 passed; 82/82 safety-critical cases passed**;
  zero critical failures or direct router writes.
- Phase 8 offline assessment after the corpus regression: **77/77** route /
  target / clarification matches, 28 ambiguous abstentions, zero false
  executable deterministic routes. The 31 named-tool choices and 16 proxy
  disagreements are offline selector comparisons, not live shadow evidence.
  Phase 8 remains deferred; no classifier was added.
- Ruff lint and formatter checks on touched backend files passed.
- `git diff --check` passed.
- Frontend and Android builds were not rerun because no frontend/mobile files
  changed.

## Files changed

- `backend/app/routing/rules.py`: recognize the abbreviated personal-memory
  query exposed by live STT.
- `backend/app/routing/service.py`: skip shadow memory actions when memory
  policy is disabled.
- `backend/tests/test_router_rules.py`,
  `backend/tests/test_router_foundation.py`, and
  `backend/tests/test_phase8_classifier_assessment.py`: regression/count tests.
- `docs/phase0_router_acceptance_corpus_v1.json` and
  `docs/phase2_router_acceptance_v1.md`: corpus version/count and runner output.
- `.gitignore`: allowlist this requested acceptance report under the existing
  local-work-record rule.
- `plan.md`: final status and current Phase 8 count after this report.

## Remaining blockers and decision

Phase 3 remains **NOT PASSED** because route disagreements remain unresolved,
including a write-capable reminder-action versus structured-read disagreement;
multilingual STT did not yield a validated supported-language sample; live
prompt-injection evidence is incomplete; reconnect/replay/commit-race/post-commit
cancellation tests remain physically incomplete; and the complete zero-side-
effects audit is not finished. Do not enable canary/on routing.

Final configuration: production/repository `ROUTER_MODE=off`,
`ROUTER_COHORT_PERCENT=0`; temporary shadow process stopped; phone restored to
backend/Metro reverses `8000`/`8081`; Phase 9 rollout state unchanged.
