# Run-scoped OKF synchronization

## Work checklist

- [x] Add an explicit run-scoped synchronization entry point that processes only caller-supplied job IDs.
- [x] Validate run identity, approved disposable owner, job type, source run tag, eligibility, and lease state before mutating any job.
- [x] Preserve the normal worker processing, generation fences, lifecycle checks, retry policy, and idempotency behavior.
- [x] Add unit and disposable-PostgreSQL isolation coverage for unrelated jobs, run/owner mismatch, lifecycle changes, retries, and fail-closed cases.
- [x] Run targeted lint, format, compile, diff, and database integration checks.
- [x] Leave runtime in RAG-only safe configuration; do not begin live OKF-6 acceptance.

## Implementation

Added `backend/app/okf/scoped_sync.py` with `process_only(...)`. This is an opt-in test/evaluation path; it does not replace or alter the global production worker. It locks and validates the full requested batch before changing job state, and it never calls the worker's global `claim()` method. Unsupported broad jobs (`rebuild_user` and `purge_user`) are rejected. Scope identity is tied to the run-specific policy version/idempotency key and the source memory's `metadata.okf_run_id` tag.

The scoped audit event (`okf.scoped_sync.execution`) contains run/owner identifiers, job identifiers and types, counts, timestamps, and stable failure codes only; it does not log memory content.

## Files changed for this work

- `backend/app/okf/scoped_sync.py` — new explicit scoped executor and audit result.
- `backend/tests/test_okf_scoped_sync.py` — fail-closed unit tests.
- `backend/tests/test_okf_service_integration.py` — disposable-PostgreSQL checks that process the selected run only, preserve unrelated job snapshots, and retain lifecycle, generation, retry, and idempotency behavior.
- `docs/20260930_2108_run_scoped_okf_sync.md` — this implementation record.

No migration, `.env` edit, checked-in default change, or production worker behavior change was made as part of this remediation.

## Verification

- `pytest -q tests/test_okf_scoped_sync.py tests/test_okf_worker.py` — 4 passed.
- `RUN_INTEGRATION_TESTS=1 RUN_OKF_SERVICE_TESTS=1 pytest -q tests/test_okf_service_integration.py::test_okf_domain_transactions_are_versioned_isolated_and_idempotent` — 1 passed against a disposable PostgreSQL database. The integration exercise includes the scoped run-A/run-B/unrelated-job isolation assertions.
- `ruff check backend/app/okf/scoped_sync.py backend/tests/test_okf_scoped_sync.py backend/tests/test_okf_service_integration.py` — passed.
- `ruff format --check backend/app/okf/scoped_sync.py backend/tests/test_okf_scoped_sync.py` — passed. The pre-existing integration test file was not reformatted wholesale to avoid unrelated changes.
- `python -m compileall -q ...` for the scoped module and changed tests — passed.
- `git diff --check` — passed; Git emitted only existing line-ending conversion warnings for unrelated dirty files.
- Backend `/health` and `/ready` — `ok` / `ready`.
- The previous blocked report `docs/evidence/okf/okf6-20260930t203218z-4bcb55-acceptance-blocked.md` remains unchanged.

## Result and next step

Run-scoped synchronization isolation is ready for a future OKF-6 acceptance attempt. This is not OKF-6 acceptance evidence and does not pass the OKF-6 gate. Before resuming, use only the disposable owner, seed a fresh run-tagged corpus, and invoke `process_only(...)` with the exact IDs returned for that run. Keep `KNOWLEDGE_MODE=rag`; any live shadow enablement requires the separate acceptance-run procedure and safe-state restoration.
