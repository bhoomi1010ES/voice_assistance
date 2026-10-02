# OKF-6 Step 3 - Memory Sync Blocker Assessment

**Overall result: INCOMPLETE. Stopped before modifying memory or starting physical voice testing.**

**Inspection snapshot:** 2026-10-01 14:53 PDT (21:53 UTC).  
**Physical test:** Not performed.  
**Data changes in this assessment:** None. Database inspection was read-only; `.env`, application code, Step 1, Step 2, and the historical report were not modified.

## Current configuration and backend readiness

The latest application `Settings()` snapshot agrees with the requested `.env` settings: `KNOWLEDGE_MODE=rag`, `OKF_ENABLED=true`, `OKF_SYNC_ENABLED=true`, `OKF_SHADOW_READS=true`, one UUID in the allowlist, `ROUTER_MODE=off`, and `ROUTER_COHORT_PERCENT=0`. UUIDs and secrets are redacted. The allowlisted owner is active, is marked as the disposable OKF account, and has memory enabled.

Direct requests at 14:53 PDT returned HTTP 200 from `/health` (`status=ok`) and `/ready` (`status=ready`). PostgreSQL, Redis, LLM, TTS, embedding, and reranker dependencies reported ready/ok. The readiness payload reports `llm.live_verified=false` while the LLM dependency is ready.

## Phase 1 - Supported memory workflow and policy inspection

The current source memory is an active `fact` row and has an existing, included voice session. Its structured fields are absent. Under `backend/app/okf/policy.py` (`map_memory_to_proposal`), an active supported memory type must have:

- A nonblank `subject` that passes normalization and secret checks.
- A nonblank `predicate` that passes normalization and secret checks.
- `object_json` as a nonempty dictionary that passes the OKF safe-value checks (including size, depth, and sensitive-value validation).

The `MemoryCreateRequest` and `MemoryUpdateRequest` schemas make these fields optional and bound their lengths. That does not make them eligible for OKF when absent. The policy does not require one universal predicate or a particular object key for a generic `fact`; the values must accurately represent the existing memory and pass the existing policy. No values were invented or submitted.

The API exposes `PATCH /memories/{memory_id}` in `backend/app/api/memories.py`. It requires the authenticated principal, an active owner with memory enabled, and a memory owned by that principal. Ownership misses are recorded by the existing audit helper. However, this route is a supersession workflow:

1. It marks the old row `superseded` and removes that source's OKF provenance.
2. It sends a candidate to `MemoryWriter` with `source_kind=manual_api`.
3. `MemoryWriter` creates a new memory row; the route links it to the old row through `supersedes_id` and returns the new ID.

The update route does not pass a `source_session_id`. The writer only enqueues OKF synchronization for writes with a source session, and `OkfRepository.lock_eligible_source` rejects a source with no session. So this endpoint would both change the memory ID and fail to provide the source-session basis the worker requires. It cannot satisfy the request to preserve the existing identity and synchronize that same row.

The existing memory integration test exercises the patch workflow and then uses the returned memory ID (`backend/tests/test_phase6_memory_integration.py`). The writer integration test explicitly verifies that a changed candidate supersedes its earlier row (`backend/tests/test_phase6_memory_integration.py`). A repository search found no other in-place memory update API or supported service operation.

## Phase 2 - Update disposition

**No mutation was attempted.** The available supported edit API cannot preserve the current memory ID, and its write path does not enqueue an eligible OKF source. Creating another memory or updating database tables directly would violate the request. The existing memory remains active, with its original ID and content unmodified by this assessment.

## Phase 3 - Worker status evidence

The worker is configured as `in_process`. Application startup stores its worker service internally as `app.state.okf_worker`; the available `/health` and `/ready` endpoints do not expose that object's `running` property. No API route exposing worker status was found. The retained `okf.worker.started` events are from September 29 and September 30; no current start event or current worker output was available in the retained logs.

Therefore:

- **Worker running:** UNKNOWN.
- **Worker processing jobs:** UNKNOWN. There is no job for this memory to process.
- **Relevant job created/completed:** No relevant job exists.
- **Retries/dead letters for this memory:** None exist because there are no job rows; this does not prove a successful synchronization.

The worker implementation emits a start event and completion/failure signals, but the current deployment does not expose a live status endpoint. Worker status must remain UNKNOWN until current runtime evidence is available.

## Phase 4 - Read-only memory, job, and provenance verification

The disposable owner still has six active memories and two active Orion-related memories. The known Step 1/2 memory is present under the allowlisted owner and remains active. It has a source session that exists and is not marked excluded, but it is not eligible under the structured OKF policy because its `subject`, `predicate`, and `object_json` are absent.

For the known memory:

- OKF sync jobs in all statuses: **0**.
- OKF provenance source links: **0**.
- Replacement rows superseding the known memory: **0**.

No OKF record or provenance link can be claimed. Since no mutation was submitted, no unrelated memory was changed by this assessment. The memory's exact content is omitted here; its row and identity remain the known Step 1/2 source.

## Results

| Check | Result | Evidence |
|---|---|---|
| Configuration | PASS | Current `Settings()` matches requested `.env` flags; allowlist resolves to the active disposable owner. UUID redacted. |
| Backend readiness | PASS | `/health` and `/ready` returned HTTP 200; readiness dependencies reported ready/ok. |
| Worker running | UNKNOWN | No live worker status endpoint or current `okf.worker.started` log; worker state is internal to app lifespan. |
| Memory update through supported workflow while preserving ID | FAIL | The only exposed edit API supersedes the original row and creates a new ID; its writer call also omits `source_session_id`. No mutation was attempted. |
| Memory eligible under OKF policy | FAIL | The existing active `fact` row lacks nonblank subject, predicate, and a nonempty structured object. |
| Sync job created | FAIL | Zero OKF sync jobs exist for the known memory. |
| Sync job completed | UNKNOWN | No job exists to complete. |
| OKF record available | FAIL | No source links or synchronized record tied to the known memory. |
| Provenance and user isolation | INCOMPLETE | Owner scoping is confirmed for the source row; no OKF record/link exists to validate provenance. |
| Original memory identity/content preserved | PASS | No write was attempted; the existing source row remains active and no superseding child exists. |
| Unrelated memories changed | PASS | This assessment performed only read-only database inspection. |
| Physical voice test | UNKNOWN | Not performed; the synchronization preflight has not passed. |

## Overall result and next action

**INCOMPLETE.** The supported `PATCH` workflow cannot meet the required identity-preservation constraint and does not create a sync-eligible source. I stopped before changing data. The earlier synchronization blocker also remains: the known memory has no structured subject, predicate, or object, and has no sync job or provenance link. Worker running status is UNKNOWN.

To continue under the current requirements, the application needs an authorized, validated identity-preserving update workflow that retains the source session/provenance and enqueues OKF synchronization for the same source row. No such workflow exists in the inspected backend. Once that workflow is available, the memory can be updated through it, and the worker/job/provenance checks can be completed before any physical voice test.

The exact first voice question remains: **“What do you remember about my project?”** No voice turn was performed or counted. This is not an OKF-6 acceptance result; at least 16 valid physical voice turns with completed RAG/shadow pairs are still required.

### Todo

- [x] Inspect memory schemas, validation, update API, writer, OKF policy, worker, provenance, and relevant existing tests.
- [x] Recheck configuration, backend health/readiness, and the target memory/job/provenance state read-only.
- [x] Stop before mutation when the supported update path cannot preserve identity or enqueue a valid source.
- [ ] Provide an authorized identity-preserving update path that queues OKF sync for the same memory.
- [ ] Verify live worker status, completed job, OKF record, provenance, and owner isolation.
- [ ] Rerun the Step 3 preflight; only after it passes, request the physical Android voice test.

---

## Current Step 3 recheck — 2026-10-01 17:55 PDT (2026-10-02 00:55 UTC)

This addendum preserves the earlier 14:53 PDT assessment above. This recheck did not modify `.env`, application code, or database rows. Database queries were read-only. No Step 3 physical voice turn was solicited or counted because the synchronization prerequisite failed.

### Effective configuration and backend

The fresh `Settings()` snapshot reported the required non-secret settings:

| Setting | Required | Observed |
|---|---|---|
| `KNOWLEDGE_MODE` | `rag` | `rag` |
| `OKF_ENABLED` | `true` | `true` |
| `OKF_SYNC_ENABLED` | `true` | `true` |
| `OKF_SHADOW_READS` | `true` | `true` |
| `OKF_SHADOW_USER_IDS` | One disposable test owner | One entry; matches the known disposable owner (UUID redacted) |
| `ROUTER_MODE` | `off` | `off` |
| `ROUTER_COHORT_PERCENT` | `0` | `0` |
| `OKF_WORKER_MODE` | `in_process` | `in_process` |

The `.env` last-write time was 17:48:34 PDT. The currently serving backend process started at 17:52:43 PDT, after that write. At approximately 17:55 PDT, Windows requests to `/health` and `/ready` returned HTTP 200. `/ready` reported PostgreSQL, Redis, LLM, TTS, and memory dependencies ready; it did not include a router component, consistent with router mode `off`. Secrets and the allowlisted UUID are omitted.

### Worker and synchronization evidence

The configured worker mode is `in_process`, but **current worker running status is UNKNOWN**: the live `/ready` response does not expose worker state, and no current startup log for the serving process was available. A separate duplicate Uvicorn start attempt emitted `okf.worker.started` but then failed to bind port 8000 and shut down; that event is not evidence about the worker in the currently serving process.

Read-only PostgreSQL inspection found no pending, running, retry-wait, or dead-letter OKF jobs globally. The disposable owner had no OKF jobs in any status. The known Step 1/2 Orion memory is owned by the allowlisted disposable account, remains active, and has an existing source session that exists and is not excluded. Its `subject` and `predicate` are blank; `object_json` is nonempty. It therefore does not meet the current mapper's structured-field requirements. It has zero OKF sync jobs and zero OKF provenance source links. No synchronization success can be claimed.

### Physical test status

- Exact requested question: “What do you remember about my project?”
- Test time, session ID, and turn ID: none for this Step 3 preflight; no test was solicited or counted.
- RAG retrieval, transcript, answer, and same-turn OKF shadow evidence: unavailable for this preflight.
- Prior Step 1 and Step 2 physical results remain separate evidence and are not reused as a Step 3 RAG/shadow pair.

| Check | Result | Evidence |
|---|---|---|
| Configuration | PASS | Fresh settings matched required flags; allowlist contains the disposable owner; serving process started after `.env` write. |
| Backend readiness | PASS | Windows `/health` and `/ready` returned HTTP 200; configured dependencies reported ready. |
| Sync worker | UNKNOWN | In-process mode configured; current worker state is not exposed by `/ready`, and there is no current startup log for the serving process. |
| Memory synchronization | FAIL | The known memory has blank subject/predicate, zero jobs, and zero provenance links. |
| Physical voice request | UNKNOWN | No Step 3 test was solicited or counted. |
| STT completed | UNKNOWN | No Step 3 test transcript. |
| RAG retrieval | UNKNOWN | No Step 3 retrieval evidence. |
| Expected memory retrieved | UNKNOWN | No Step 3 retrieval evidence. |
| Assistant answer | UNKNOWN | No Step 3 response evidence. |
| OKF shadow scheduled | UNKNOWN | No Step 3 voice turn. |
| OKF shadow completed | UNKNOWN | No Step 3 shadow completion record. |
| Shadow observation recorded | UNKNOWN | No Step 3 observation record. |
| Same-turn RAG and OKF pair | UNKNOWN | No Step 3 turn IDs. |
| Provenance and isolation | INCOMPLETE | Source ownership is confirmed, but no OKF provenance exists; retrieval isolation was not exercised. |
| User-facing answer unaffected | UNKNOWN | No Step 3 response or paired shadow trace. |

### Current result and next action

**INCOMPLETE — stop before the physical test.** Configuration and backend readiness pass. The earliest confirmed gate failure is that the known Orion source lacks the `subject` and `predicate` required for OKF mapping, so no eligible sync job or provenance exists. The worker's live state also remains unknown. Do not create or edit database rows manually. Resume only after an authorized supported workflow produces an eligible source tied to an included session, its sync job completes, provenance is verified, and current worker status is observable. Then rerun this preflight before asking for the physical voice test. OKF-6 still requires at least 16 valid physical RAG/shadow pairs; this addendum does not count any.

### Recheck todo

- [x] Verify non-secret settings, disposable-owner allowlist membership, backend health, and readiness.
- [x] Inspect the known memory, source session, jobs, and provenance read-only.
- [x] Stop before physical testing because the known memory is ineligible and unsynchronized.
- [ ] Establish an authorized supported path to provide the required structured fields while retaining eligible source provenance.
- [ ] Verify current worker running status and successful synchronization/provenance for the source.
- [ ] Rerun the preflight; request the physical voice test only if all gates pass.
