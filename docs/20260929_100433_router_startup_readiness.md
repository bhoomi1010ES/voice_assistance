# Router startup and readiness hardening

Date: 2026-09-29 10:04:33 America/Los_Angeles

## Context

`ROUTER_MODE=on` previously left LangGraph compilation until the first user turn. The `/ready` endpoint also had no router dependency check, so it could return ready even when the graph could not import, compile, expose every route handler, or execute a decision.

## Completed work

- [x] Create one `DecisionRouterService` during application startup for every mode.
- [x] Precompile the graph during lifespan startup whenever routing is active, including `on`, active `shadow`, and active `canary` modes.
- [x] Preserve the original `warm_shadow_graph()` entry point as a compatibility alias.
- [x] Add a side-effect-free router readiness probe.
- [x] Verify the configured router mode and report `mode: on` when enabled.
- [x] Verify that the graph was compiled before readiness succeeds.
- [x] Inspect graph topology for every `RouteName` sink and its required inbound/outbound edges.
- [x] Invoke a pure `GENERAL_LLM` decision through the compiled graph and validate the returned outcome.
- [x] Add the active router as a `/ready` dependency and return HTTP 503 for router readiness failures.
- [x] Add success and failure regression coverage.

## Files changed

- `.gitignore`
  - Unignores this required implementation record while preserving the existing documentation rules.
- `backend/app/main.py`
  - Creates and stores the router service at startup.
  - Warms all active router modes before serving requests.
  - Emits mode-aware startup warmup telemetry.
- `backend/app/routing/service.py`
  - Adds active-mode detection and generalized startup warmup.
  - Adds compiled graph, handler topology, and decision-execution readiness checks.
- `backend/app/api/routes.py`
  - Includes active router health in `/ready` and makes failures affect the HTTP readiness status.
- `backend/tests/test_router_foundation.py`
  - Covers warmup across active modes, `ROUTER_MODE=on` loading, healthy readiness, incomplete topology, and failed probe execution.
- `backend/tests/test_api.py`
  - Proves `on` compiles exactly once at startup, reports ready when healthy, and returns 503 when compilation fails.
- `docs/20260929_100433_router_startup_readiness.md`
  - Records this implementation and its verification.

## Readiness behavior

An active router reports safe operational metadata only: mode, compiled state, route-handler counts, decision-probe status, and a stable error code on failure. Disabled or zero-cohort modes remain omitted from `/ready` dependencies so existing off-mode response contracts are unchanged.

## Verification

Run from `backend/`:

```text
..\.venv\Scripts\python.exe -m ruff format app/routing/service.py app/main.py app/api/routes.py tests/test_router_foundation.py tests/test_api.py
..\.venv\Scripts\python.exe -m ruff check app/routing/service.py app/main.py app/api/routes.py tests/test_router_foundation.py tests/test_api.py
..\.venv\Scripts\python.exe -m pytest tests/test_router_foundation.py tests/test_router_rules.py tests/test_api.py -q
```

Results:

- Ruff formatting: passed.
- Ruff lint: passed.
- Targeted tests: 124 passed.
- `git diff --check` for backend application and tests: passed (Git emitted only the repository's LF-to-CRLF working-copy notices).

