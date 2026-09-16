# Phase F1 — Privacy and retrieval correctness

Date: 2026-09-16

## Scope

Implemented the five P0 fixes requested for Phase F1. GraphRAG flags remain unchanged (`GRAPH_RAG_MODE=off` and `GRAPH_WRITE_ENABLED=false`); no Android, build, or unrelated instrumentation work was performed.

## Changes

- `backend/app/websocket/gateway.py`
  - Refreshes `memory_excluded` from the owner-scoped `voice_sessions` row for every memory policy decision, so a live gateway does not rely on stale connection metadata.
  - Uses nested transaction boundaries for user-policy and exclusion reads; policy lookup failures fail closed for memory access.
  - Treats database failures during optional retrieval like provider failures, allowing the voice turn to continue without memory context.
- `backend/app/memory/retrieval.py`
  - Adds an optional bounded `limit` to retrieval and preserves the configured final-context cap.
  - Runs retrieval inside a savepoint and returns a `degraded` empty result with `memory_database_error` after SQLAlchemy failures, leaving the caller transaction usable.
  - On reranker failure, keeps only FTS/structured RRF evidence and drops dense-only candidates; reranker score thresholds are not applied to RRF scores.
- `backend/app/api/memories.py`
  - Forwards hybrid search limits and filters the final memory read to active, owner-scoped rows.
  - Applies the same enabled-user write guard to `PATCH /memories/{memory_id}` used by memory creation.
- Tests cover live exclusion refresh, reranker fallback, optional retrieval database failure, hybrid limit propagation, disabled-user edits, and existing cross-user/deleted/superseded retrieval protections.

## Validation

- Focused unit/gateway/memory tests: **23 passed**, 3 skipped when integration mode was unset.
- PostgreSQL memory integration tests with `RUN_INTEGRATION_TESTS=1`: **3 passed**.
- Cross-user/resource and GraphRAG integration regression tests with `RUN_INTEGRATION_TESTS=1`: **12 passed**.
- Complete backend suite: **292 passed, 50 skipped** (one existing Starlette/httpx deprecation warning).
- Ruff check and format check: **passed**.

Session exclusion is prospective: it prevents subsequent retrieval and memory writes for the excluded live session; it does not retroactively delete memories already persisted before the exclusion change.
