# Phase 0 task marker matching fix

Date: 2026-09-25

## Context

The physical Phase 0 write retry committed exactly one task, but the live driver aborted because the product title used `clean up` while the acceptance marker used `cleanup`. An older disposable test task with the same wording also remained, so accepting the result would have weakened the safety check.

## Changes

- Updated `backend/scripts/phase0_live_driver.py` so task marker queries recognize both `cleanup` and `clean up` spellings while remaining owner-scoped.
- Removed only the two identified disposable Phase 0 test tasks from the disposable account before retrying. No unrelated tasks were touched.

## Verification

- `backend/tests/test_phase0_live_driver.py` and `backend/tests/test_live_latency.py`: 40 passed.
- Router remains `off`, cohort remains `0`.
- A new physical write/cleanup capture is required after this fix; prior aborted artifacts remain preserved.
