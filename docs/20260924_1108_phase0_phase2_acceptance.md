# Phase 0 and Phase 2 acceptance work (2026-09-24 11:08 local)

## TODO / completed

- [x] Inspect the supplied task and existing router baseline/plan artifacts.
- [x] Reconcile proposal pages 8-9 and 11-13 against a frozen 75-case corpus.
- [x] Fix the informational task false positive and preserve direct clock/date routing precedence.
- [x] Run full deterministic acceptance corpus, backend suite, frontend tests, lint/format/security checks where available.
- [x] Update Phase 0 baseline, Phase 2 report, and plan gate status.
- [x] Record physical barge-in separately and preserve router-off/gateway boundary.
- [x] Record remaining blockers without inventing ownership or performance data.

## Changes

- `backend/app/llm/context.py`: informational prefixes no longer swallow direct current date/time; informational task/reminder phrasing remains out of action routes.
- `backend/app/llm/task_tools.py`, `backend/app/llm/reminder_tools.py`: date/time resolution logging excludes raw expression and resolved local/UTC time values.
- `backend/app/routing/{__init__,models,service,rules}.py`: Phase 2 deterministic contracts/preflight/rules; no gateway dispatch.
- `backend/scripts/router_acceptance.py`: reproducible corpus runner with deterministic non-writing confirmation/graph fixtures.
- `backend/tests/{test_device_aware_time,test_llm_context,test_router_rules}.py`: regressions, safety/preflight/corpus checks.
- `docs/phase0_router_acceptance_corpus_v1.json`: version 1 frozen, 75 cases.
- `docs/phase2_router_acceptance_v1.md`: generated full case report.
- `docs/20260923_1739_phase0_router_acceptance_baseline.md`: proposal reconciliation, telemetry limits, physical status, privacy, test status, provisional rollout budgets; Phase 0 remains NOT PASSED.
- `plan.md`: Step 0 NOT PASSED; Step 2 PASS within deterministic/preflight scope only.

## Verification

- `PYTHONPATH=backend .venv/Scripts/python.exe backend/scripts/router_acceptance.py`: 75 passed, 0 failed, 0 critical failures; no writes/model/retrieval calls.
- `.venv/Scripts/python.exe -m pytest backend -q`: 456 passed, 56 skipped.
- Targeted and full backend Ruff checks pass; changed backend files pass Ruff format check; `git diff --check` passes.
- Frontend Jest: 18 suites, 118 tests passed. ESLint passes.
- Frontend `npm run check` is blocked by pre-existing TS error at `frontend/__tests__/phase3.test.ts:762` (`never` not callable). Frontend Prettier check reports 30 files; UI secret scan false-positive at `TaskEditorModal.tsx` because `task-date-input` matches its `sk-` pattern. No UI files were edited.
- Full backend Ruff formatting check reports 16 unformatted files repo-wide; `mypy` is not installed. No build run because no client packaging change is part of this task.

## Gate and limitations

Phase 2 PASS for deterministic preflight/rules only. Phase 0 NOT PASSED: existing trace cannot support valid route-labeled/clock-compatible before-change performance metrics; physical loudspeaker status is stale (2026-09-10) and un-retested; rollback owner is assigned as Bhoomi (backup: Backend lead / designated developer), with triggers and an `ROUTER_MODE=off` verification procedure documented. Router mode remains `off`; no shadow/canary/gateway routing occurred. The 5% stable-user-hash cohort and numeric latency budgets are provisional, inactive, and need paired-baseline validation.
