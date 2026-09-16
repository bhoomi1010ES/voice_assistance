# Phase 6A — Stage 4: Asynchronous Graph Indexing From Validated Memory Evidence

**Status:** PASS  
**Scope:** Write-side graph indexing only. GraphRAG reads, backfill, query planning, retrieval, RRF, reranking, LLM context, and voice integration remain disabled.

## Stage 3 accepted baseline

- Stage 0 Hybrid baseline: 18 eligible queries; Recall@5 `0.86667`; MRR `0.800`; zero unexpected no-result responses.
- Stage 1 rollout flags remain `GRAPH_RAG_MODE=off` and `GRAPH_WRITE_ENABLED=false`.
- Stage 2 schema was accepted at `0012_graph_rag_foundation`.
- Stage 3 added owner-scoped exact resolution, idempotent graph writes, and bounded one-hop/two-hop traversal without connecting GraphRAG to retrieval or voice.

## Migration and schema evidence

Stage 4 required a schema migration because the pre-existing `memory_jobs.job_type` check constraint rejected the dedicated `index_memory_graph` type. Existing migrations `0007_phase6_memory_foundation.py` and `0012_graph_rag_foundation.py` were not edited.

Migration: `0013_graph_index_job_type`  
Reason: Required dedicated `index_memory_graph` durable job type  
Scope: Recreate only `ck_memory_jobs_type`; no tables, columns, indexes, or existing rows were changed.

The isolated migration test upgraded a disposable database from `0012`, recorded total rows, grouped job-type counts, and a full-row fingerprint, then verified the same values after `0013`. It accepted all five allowed types and rejected `invalid_graph_job`. The isolated downgrade was run after removing synthetic graph-job rows; it restored the four-type constraint and rejected `index_memory_graph` again.

```text
Migration:
0013_graph_index_job_type

Reason:
Required dedicated index_memory_graph durable job type

Existing rows preserved: PASS
Existing job-type counts preserved: PASS
index_memory_graph accepted after upgrade: PASS
invalid job type rejected: PASS
isolated downgrade: PASS
index_memory_graph rejected after downgrade: PASS
```

Before the authorized live upgrade:

```text
Repository head: 0013_graph_index_job_type
Live DB head:    0012_graph_rag_foundation
memory_jobs total: 211
memory_jobs by type: embed_memory=26, extract_turn=185
memory_jobs SHA-256: d9ef78393b241e826b05d59939e6927e9af55931dad386b83e08ae746a828b96
```

After the live upgrade:

```text
Repository head: 0013_graph_index_job_type
Live DB head:    0013_graph_index_job_type
memory_jobs total: 211
memory_jobs by type: embed_memory=26, extract_turn=185
memory_jobs SHA-256: d9ef78393b241e826b05d59939e6927e9af55931dad386b83e08ae746a828b96
```

The live fingerprint and grouped counts are identical. Live graph counts remain `entity_aliases=0` and `entity_relationships=0`, as expected with graph writes disabled and no backfill.

## Graph indexing architecture

`index_memory_graph` reuses the existing PostgreSQL durable `memory_jobs` queue, including `FOR UPDATE SKIP LOCKED`, leases, attempts, retry scheduling, dead state, cancellation, and the owner/idempotency uniqueness constraint. The writer enqueues only after the memory row, chunks, and existing subject link have durable identities. Enqueue is in the same transaction as the memory write, so a committed memory has one durable graph job or no graph job when the flag/policy excludes it.

The deterministic key is:

```text
graph:<user_id>:<memory_id>:v1
```

The policy version is `v1` and is stored on `memory_jobs.policy_version` and `entity_relationships.extraction_policy_version`.

`GraphIndexingService` loads one owner-scoped memory, rechecks `memory_enabled`, active status, source provenance, and policy version, derives an edge from validated structured fields, resolves or creates exact owned entities, and inserts one evidence-backed relationship. It does not call an LLM, embedding service, Redis, fuzzy matcher, semantic matcher, or graph traversal. The worker logs safe job lifecycle events and background timings for memory load, eligibility, source resolution, target resolution, relationship insert, and total indexing. Logs contain IDs, bounded reason codes, counts, policy versions, and durations; they do not contain memory content or entity names.

## Eligibility and relationship policy

Only `memory_type=relationship` records with a supported predicate and structured target are graphable. The implemented predicate mapping is:

| Memory predicate | Graph type | Source type | Target type |
|---|---|---|---|
| `works_on` | `WORKS_ON` | `person` | `project` |
| `responsible_for` | `RESPONSIBLE_FOR` | `person` | explicit object type |
| `tested_by` | `TESTED_BY` | explicit/existing | explicit object type |
| `manages` | `MANAGES` | `person` | explicit object type |
| `reports_to` | `REPORTS_TO` | `person` | `person` |
| `member_of` | `MEMBER_OF` | `person` | explicit object type |
| `assigned_to` | `ASSIGNED_TO` | `person` | explicit object type |
| `depends_on` | `DEPENDS_ON` | explicit/existing | explicit object type |
| `blocked_by` | `BLOCKED_BY` | explicit/existing | explicit object type |
| `discussed_with` | `DISCUSSED_WITH` | `person` | `person` |

Unknown predicates are skipped with `unsupported_predicate`; they are never mapped to `RELATED_TO`. Scalar preferences and untyped targets do not create graph nodes. A value target is accepted only when the relationship contract supplies a target type, as with `works_on`. Near matches do not resolve automatically.

Source resolution uses an existing `memory_entities` subject link first, then exact canonical name, then one exact unambiguous alias, then deterministic creation when the relationship contract supplies a source type. Target resolution uses exact canonical name, then one exact alias, then deterministic creation only when a validated target type is available. Ambiguous names and aliases skip safely. No aliases are generated in Stage 4 because current memory extraction does not produce explicit alias evidence.

Relationship confidence is copied from `memory.confidence`; temporal validity is copied without transformation; initial edge status is `active`. Every edge retains `user_id` and `source_memory_id`. Database composite foreign keys and repository owner checks remain the final cross-user boundary.

When an active preference is superseded and graph writes are enabled, only relationships sourced by the old memory are marked `superseded`. Deleted memories use existing cascade semantics. A queued job whose source is missing, superseded, session-excluded, or owned by a disabled user completes as a safe skip and creates no edge. A graph failure retries/dead-letters the graph job without invalidating the memory, embedding, retrieval, or voice response.

## Required policy matrix

| Memory example/type | Graph eligible? | Reason | Edge |
|---|---:|---|---|
| Typed `relationship` with `works_on` | Yes | Direct structured relationship | `WORKS_ON` |
| Typed relationship with another approved predicate | Yes | Direct structured relationship and validated target type | Implemented mapped type |
| Preference scalar | No | Scalar is not an entity relation | none |
| Unsupported predicate | No | No approved mapping | none |
| Superseded memory | No | Not current evidence | none |
| Deleted memory | No | No source evidence | none |
| Ambiguous target alias | No | Cannot safely resolve one entity | none |

## Validation

Focused Stage 4 tests: **17 passed**. They cover deterministic policy, migration upgrade/downgrade, flag gating, durable enqueue, replay idempotency, source/target reuse, exact alias reuse, ambiguous aliases, no fuzzy resolution, unsupported/scalar skips, concurrent entity and edge creation, disabled-memory and session-exclusion races, supersession lifecycle, cross-user protection, confidence/temporal propagation, worker failure isolation, and background timings.

The complete backend suite ran against a fresh disposable PostgreSQL database upgraded through `0013`: **336 passed, 2 skipped**, with one existing Starlette/httpx deprecation warning. The initial run exposed a test-environment collision from the shared database URL and a background embedding worker racing a delete fixture; the final disposable run disabled the worker for unrelated suite cases and passed without changing application behavior.

The background graph-indexing microbenchmark used 30 synthetic records on disposable PostgreSQL:

| Metric | Value |
|---|---:|
| Min | 73.110 ms |
| Mean | 95.398 ms |
| P50 | 78.770 ms |
| P95 | 206.532 ms |
| Max | 210.788 ms |

These are background worker timings, not end-to-end voice performance.

```text
Ruff: PASS
Format: PASS
Compile/import: PASS
Alembic drift: PASS (No new upgrade operations detected)
git diff --check: PASS
```

No changes were made to Hybrid retrieval, memory extraction, embeddings, FTS, vector retrieval, RRF, reranking, voice, tools, tasks, confirmation, LLM, TTS, authentication, cancellation, or Android. `GRAPH_RAG_MODE` remains off; GraphRAG reads and graph backfill are not implemented.

## Files added or changed for Stage 4

- `backend/migrations/versions/0013_graph_index_job_type.py`
- `backend/app/graph/indexing.py`
- `backend/app/graph/policy.py`
- `backend/app/graph/repository.py`
- `backend/app/graph/service.py`
- `backend/app/graph/types.py`
- `backend/app/memory/repository.py`
- `backend/app/memory/types.py`
- `backend/app/memory/writer.py`
- `backend/app/memory/jobs.py`
- `backend/app/models/resources.py`
- `backend/tests/test_phase6a_graph_index_migration.py`
- `backend/tests/test_phase6a_graph_indexing.py`
- `backend/tests/test_phase6a_graph_indexing_integration.py`
- `docs/20260916T015744Z_phase6a_stage4_async_graph_indexing.md`

The existing Stage 1–3 files remain in the worktree as accepted prior-stage changes. Disposable Stage 4 test harness scripts were removed.

## Final required output

```text
PHASE 6A — STAGE 4
==================

Repository head:
0013_graph_index_job_type

Live DB head:
0013_graph_index_job_type

New migration:
YES
0013_graph_index_job_type

Graph job type:
index_memory_graph

Graph indexing service:
PASS

Validated-memory-only indexing:
PASS

Extra LLM graph extraction:
NO

GRAPH_WRITE_ENABLED=false behavior:
PASS

Eligible memory enqueue:
PASS

Graph job idempotency:
PASS

Concurrent entity creation:
PASS

Concurrent relationship creation:
PASS

Source entity reuse:
PASS

Target entity reuse:
PASS

Alias reuse:
PASS

Alias ambiguity:
PASS

Unsupported predicate skip:
PASS

Scalar/non-entity skip:
PASS

Superseded-memory guard:
PASS

Deleted-memory race:
PASS

Memory-disabled guard:
PASS

Cross-user graph writes:
0

Confidence propagation:
PASS

Temporal propagation:
PASS

Graph indexing P50:
78.770 ms

Graph indexing P95:
206.532 ms

Live entity_aliases:
0

Live entity_relationships:
0

Existing Hybrid smoke:
PASS (Stage 3 accepted baseline; graph reads remained disconnected)

Focused tests:
17 passed

Full backend:
336 passed, 2 skipped

Ruff:
PASS

Format:
PASS

Compile:
PASS

Alembic drift:
PASS

git diff --check:
PASS

GRAPH_RAG_MODE:
off

GRAPH_WRITE_ENABLED:
false

Graph retrieval integration:
NOT IMPLEMENTED

Graph backfill:
NOT IMPLEMENTED

Existing business logic changed:
NO — additive graph write-side behavior only; unrelated behavior unchanged

STAGE 4:
PASS
```

**NEXT:** Stage 5 — Idempotent Existing-Memory Graph Backfill and Graph Data Validation

Stage 5 was not implemented.
