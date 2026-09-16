# Phase F2 — Ownership and lifecycle contracts

Date: 2026-09-16

## Product decisions

- `MEMORY_WRITE_ENABLED` gates voice extraction, memory tools, embedding/re-embedding workers, and other background writes. Manual REST create/edit remains available while the account's `memory_enabled` setting is true. List, get, delete, and re-enable remain available for user control.
- Session exclusion is prospective. It blocks subsequent retrieval and automatic writes for the session; it does not silently delete existing automatic or manual memories.
- Memory database failures degrade to no memory context and do not fail the voice turn. The retrieval savepoint boundary from F1 remains the isolation mechanism.
- A no-result personal-memory query produces no injected memory context; the configured LLM may continue with its normal answer behavior.

## Ownership and lifecycle changes

- Added migration `0014_memory_supersession_owner`, after `0013_graph_index_job_type`.
- Replaced the single-column `supersedes_id` foreign key with `(supersedes_id, user_id) -> (memory_items.id, memory_items.user_id)`. PostgreSQL uses column-list `SET NULL` so deleting an older source clears only `supersedes_id` and preserves the child owner.
- Existing cross-owner references are rejected during migration preflight rather than silently rewritten.
- `MemoryWriter` atomically increments `users.memory_version` once for each newly committed memory. Dedupe replays do not increment it. API settings, delete, delete-all, automatic extraction, and confirmed tool deletion use the same atomic counter update.
- Confirmed `memory_save` records the authenticated session and turn, and resolves the final user message for `source_message_id` when present. Manual REST writes remain explicitly provenance-free with `source_kind=manual_api`.
- `reembed_memory` now uses the embedding handler and has idempotent repository scheduling. Unsupported job types are dead-lettered immediately with `memory_job_type_unsupported` instead of retrying.
- Added bounded, owner-scoped orphan-entity cleanup. Manual and canonical aliases are retained; cleanup is optional and replay-safe.

## Validation

- Disposable PostgreSQL migration test: **1 passed**; existing memory rows were fingerprint-preserved and cross-owner supersession was rejected by PostgreSQL.
- Memory lifecycle integration: **4 passed**.
- Graph indexing/schema integration: **14 passed**.
- Focused unit tests: **28 passed**.
- Complete backend suite: **296 passed, 53 skipped** (one existing Starlette/httpx deprecation warning).
- Ruff check and format checks: **passed**.

Repository head is `0014_memory_supersession_owner`; the shared database remains at `0013_graph_index_job_type` and was not changed during this implementation.
