# Phase 3 shadow observation enablement — 2026-09-28

## Scope

Enabled observation mode for the local disposable test environment. This does
not enable canary or production routing.

```text
ROUTER_MODE=shadow
ROUTER_COHORT_PERCENT=100
ROUTER_TIMEOUT_MS=250
```

The 100% cohort applies only to the local disposable test user. Production
configuration remains `ROUTER_MODE=off` with cohort `0`.

## Verification

- Backend was reloaded with the updated local settings.
- `/health` returned HTTP 200 and `{"status":"ok"}`.
- `/ready` returned HTTP 200 with PostgreSQL, Redis, LLM, TTS, embedding, and
  reranker readiness.
- `Settings()` resolved `router_mode=shadow`, `router_cohort_percent=100`, and
  `router_timeout_ms=250`.
- Updated the settings comment to recognize the explicit Phase 0 owner/manual
  override as an accepted prerequisite for observational shadow mode.
- Existing router tests passed (33 tests). The gateway shadow implementation
  keeps the legacy response/TTS/tool path authoritative.

## Remaining work

No physical sample was claimed by this enablement record. Run the fresh labeled
sample from the Phase 3 plan and review `router.shadow.observation` records
before treating Phase 3 as passed. Restore `ROUTER_MODE=off` and cohort `0` on
any failure or when the observation window ends.
