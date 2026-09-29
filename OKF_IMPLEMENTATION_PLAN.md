# OKF structured knowledge layer implementation plan

**Plan date:** 2026-09-28 (America/Los_Angeles)  
**Status:** Planning complete; implementation not started  
**Default rollout:** `OKF_ENABLED=false`, `OKF_SYNC_ENABLED=false`, `KNOWLEDGE_MODE=rag`  
**Scope:** Add a user-scoped structured knowledge capability beside the existing Hybrid RAG. Subscription and entitlement policy are explicitly deferred.

## 1. Executive decision

Implement OKF as a separate internal engine behind the current memory/orchestration boundary.

```text
Voice / Text Input
       |
       v
LangGraph Router
       |
       v
Knowledge Selector
       |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
Existing Hybrid RAG      OKF engine         Device/session
       |                    |                    |
       +--------------------+--------------------+
                            |
                            v
                     Context Builder
                            |
                            v
                           LLM
```

The first release must satisfy these invariants:

1. Do not migrate, rewrite, delete, or reinterpret existing `memory_items`, chunks, embeddings, FTS, RRF, reranker, or GraphRAG data.
2. Do not add subscription, pricing, plan, or entitlement assumptions to OKF domain code.
3. Keep `RAG` as the default knowledge mode and preserve equivalent behavior while OKF is disabled.
4. Build OKF from already-persisted, user-owned durable memories. Do not make the voice response wait for OKF extraction or restructuring.
5. Keep every OKF record traceable to its source memory and policy version.
6. Apply the existing account memory setting, session exclusion, user ownership, cancellation, and privacy rules to OKF.
7. Do not create a second LLM pipeline. Feed bounded OKF evidence into the existing context-building path.

For this project, **OKF** means the structured user-knowledge capability described in this plan. It does not imply adoption of an external library or wire standard.

## 2. Verified current project status

The repository was inspected on 2026-09-28 before this plan was written.

| Area | Verified state | Consequence for OKF |
|---|---|---|
| Working tree | 13 tracked files modified and 8 untracked paths before this plan; these are existing owner changes. | Preserve them and implement OKF in small isolated changes. |
| Durable memory | `memory_items`, `memory_chunks`, source IDs, status, validity, supersession, confidence, salience, and owner-scoped constraints exist. | Use memories as OKF source evidence; do not replace them. |
| Hybrid retrieval | Structured, FTS, dense BGE-M3, RRF, and reranking are implemented in `MemoryRetrievalService`. | Treat this service as the unchanged RAG engine. |
| Memory writes | Explicit/manual/automatic writes converge through `MemoryWriter`; database-backed extraction and embedding jobs exist. | Add an asynchronous OKF enqueue seam after a new durable memory is accepted. |
| Existing graph | `backend/app/graph/` and migration `0012` implement a separate GraphRAG entity/relationship index. | Do not rename or repurpose GraphRAG as OKF. Keep its flags and tables independent. |
| Context assembly | Memory evidence is bounded and passed through `backend/app/memory/context.py` into `backend/app/llm/context.py`. | Add a knowledge composition layer at this boundary. |
| Router | `MEMORY_QUERY` already exists. Local `ROUTER_MODE=off`, cohort `0`; Phase 3 live shadow acceptance is pending. | Do not add a new top-level router intent. Select RAG/OKF/HYBRID only after `MEMORY_QUERY`. |
| Local memory flags | `MEMORY_RETRIEVAL_MODE=inject`, `MEMORY_WRITE_ENABLED=true`. | OKF must not change the currently active local RAG path when disabled. |
| Local GraphRAG flags | `GRAPH_RAG_MODE=off`, `GRAPH_WRITE_ENABLED=false`. | OKF rollout must not silently enable GraphRAG. |
| Router gates | Phase 0 is an effective pass by owner override while its automated evidence remains incomplete; Phase 7 action safety passes; Phase 3 and Phase 9 remain not passed. | Offline OKF work may proceed, but live voice injection/canary must respect the existing router gates. |
| Memory acceptance | Core retrieval, extraction, worker, ownership, and grounded-answer checks pass; broader physical/privacy rollout evidence remains pending. | Reuse proven boundaries and do not claim OKF production readiness from unit tests alone. |
| Latest backend evidence | The latest Phase 7 record reports 589 passed and 56 skipped, with focused router/memory checks passing. | This is baseline evidence, not a test run performed for this planning-only task. |
| OKF | No OKF files, settings, tables, APIs, or tests were found. | Implementation starts at Phase OKF-0 below. |

Known unrelated release blockers remain in force: physical voice/TTS/barge-in acceptance, live router shadow evidence, canary/rollback evidence, and previously documented repository-wide frontend/format checks. OKF must not be used to mark any of those gates complete.

## 3. Scope and non-goals

### In scope

- Durable structured concepts for profile facts, preferences, projects, decisions, relationships, and other stable facts.
- Immutable version history and source-memory provenance.
- Asynchronous memory-to-OKF synchronization and safe reconciliation after update, supersession, deletion, opt-out, or session purge.
- Owner-scoped structured lookup.
- Internal `rag`, `okf`, and `hybrid` knowledge modes.
- Bounded combined context assembly, observability, evaluation, and rollback.
- Internal service interfaces that can accept an entitlement policy later.

### Out of scope for the first release

- Subscription tiers, billing, plan names, quotas, or UI paywalls.
- Replacing or migrating Hybrid RAG or GraphRAG.
- A new mobile UI for editing OKF.
- Free-form autonomous extraction from every conversation.
- Unbounded graph traversal or model-generated database queries.
- Letting OKF write tasks, reminders, memories, or other tools.
- Making OKF or its worker a hard dependency for ordinary voice responses.
- A second context window, agent, or LLM response pipeline.

## 4. Target boundaries

Introduce a provider-neutral knowledge interface so pricing policy can be added outside the engines later.

```python
class KnowledgeEngine(Protocol):
    async def retrieve(self, request: KnowledgeRequest) -> KnowledgeResult: ...

class HybridRagEngine(KnowledgeEngine):
    # Adapter over the existing MemoryRetrievalService; no algorithm changes.
    ...

class OkfEngine(KnowledgeEngine):
    # Structured owner-scoped retrieval.
    ...

class KnowledgeSelector:
    # Chooses rag, okf, or hybrid from validated configuration/policy.
    ...
```

Do not pass subscription objects into `HybridRagEngine` or `OkfEngine`. A future entitlement check may produce an allowed mode before `KnowledgeSelector`, but the engines remain unaware of why a mode was selected.

The existing `MEMORY_QUERY` route remains the semantic intent. `KnowledgeSelector` decides how that approved query is fulfilled.

## 5. Configuration contract

Add settings with safe defaults:

```text
OKF_ENABLED=false
OKF_SYNC_ENABLED=false
KNOWLEDGE_MODE=rag              # rag | okf | hybrid
OKF_SHADOW_READS=false
OKF_CONTEXT_MAX_CHARS=4000
OKF_QUERY_LIMIT=20
OKF_WORKER_POLL_INTERVAL_SECONDS=1.0
OKF_JOB_LEASE_SECONDS=120
OKF_JOB_MAX_ATTEMPTS=5
OKF_POLICY_VERSION=okf-v1
```

Validation rules:

- `KNOWLEDGE_MODE=okf|hybrid` requires `OKF_ENABLED=true`.
- `OKF_SYNC_ENABLED=true` requires `OKF_ENABLED=true`.
- `OKF_SHADOW_READS=true` requires `OKF_ENABLED=true` but never injects context.
- `KNOWLEDGE_MODE=rag` invokes the current retrieval behavior only.
- Existing `MEMORY_RETRIEVAL_MODE`, `MEMORY_WRITE_ENABLED`, `GRAPH_RAG_MODE`, and `GRAPH_WRITE_ENABLED` retain their current meanings.
- `okf` and `hybrid` must still honor account `memory_enabled=false` and session `memory_excluded=true`.
- No subscription environment variables are added.

`OKF_ENABLED` is the emergency master switch. `KNOWLEDGE_MODE=rag` is the response-path rollback switch. They serve different operational purposes.

## 6. Initial knowledge contract

Only durable knowledge is eligible in v1.

| Concept type | Examples | Initial eligibility |
|---|---|---|
| `profile` | timezone, home base, stable occupation | Yes, when source memory is explicit and durable. |
| `preference` | concise answers, preferred tools | Yes. |
| `project` | Voice Assistant and its technology choices | Yes. |
| `decision` | selected database or model for a project | Yes when explicitly stored; no inference from tentative language. |
| `relationship` | Rahul is a colleague; project depends on service | Yes with bounded predicates. |
| `fact` | stable user-owned facts not fitting another type | Yes. |
| `routine` | recurring stable behavior | Deferred until temporal semantics are specified. |
| `event` | one-time or historical event | Remain in RAG initially unless promoted by a later policy. |
| `summary` | model-generated conversation summary | Not eligible by default. |

Reject or abstain on secrets, credentials, transient state, hypothetical statements, third-party claims without explicit user intent, unsupported nested objects, and content from excluded sessions.

The current memory extractor is intentionally conservative and does not capture every ordinary statement in the example conversation. OKF v1 synchronizes only memories that already exist. Expanding memory extraction is a separate, versioned policy decision and is not hidden inside OKF.

## 7. Data model

Add new tables only. Do not change the meaning or contents of existing memory or graph tables.

### `okf_concepts`

Current materialized concept identity and value.

```text
id UUID PK
user_id UUID NOT NULL
parent_concept_id UUID NULL
concept_type VARCHAR(32) NOT NULL
canonical_key VARCHAR(512) NOT NULL
title VARCHAR(512) NOT NULL
value_json JSONB NOT NULL
display_text TEXT NOT NULL
status active | contested | retired
current_version INTEGER NOT NULL
confidence REAL NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
policy_version VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Required constraints:

- Composite ownership keys/FKs include `user_id`.
- Unique `(user_id, canonical_key)` for current identity.
- Parent concept must belong to the same user.
- JSON size/depth limits are validated before persistence.
- Status, concept type, confidence, and validity ranges have database checks.
- Index `(user_id, concept_type, status)` and `(user_id, canonical_key)`.

Canonical keys are server-generated paths, for example:

```text
preferences/response-style
projects/voice-assistant
projects/voice-assistant/database
projects/voice-assistant/vector-store
projects/voice-assistant/embedding-model
relationships/rahul/user
```

Never accept a raw canonical key from an LLM without independent normalization and allow-list validation.

### `okf_concept_versions`

Immutable snapshots of each accepted concept change.

```text
id UUID PK
user_id UUID NOT NULL
concept_id UUID NOT NULL
version INTEGER NOT NULL
value_json JSONB NOT NULL
display_text TEXT NOT NULL
status active | contested | retired
confidence REAL NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
change_kind create | update | supersede | retire | restore
policy_version VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Unique `(user_id, concept_id, version)`. The current concept is a materialized projection of its latest accepted version.

### `okf_concept_sources`

Many-to-many provenance from a concept version to original memory evidence.

```text
user_id UUID NOT NULL
concept_version_id UUID NOT NULL
memory_id UUID NOT NULL
evidence_role supports | contradicts | supersedes
source_hash VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

All FKs are owner-scoped. Do not duplicate raw memory text in this link table. `source_hash` supports reconciliation without exposing content in logs.

### `okf_sync_jobs`

A dedicated durable outbox/worker queue keeps OKF lifecycle independent from existing embedding and GraphRAG jobs.

```text
id UUID PK
user_id UUID NOT NULL
memory_id UUID NULL
event_type upsert_memory | remove_memory | rebuild_user | purge_user
idempotency_key VARCHAR(512) UNIQUE NOT NULL
status pending | running | retry_wait | completed | dead | cancelled
attempts INTEGER NOT NULL
available_at / locked_at / completed_at TIMESTAMPTZ
last_error_code VARCHAR(128) NULL
policy_version VARCHAR(64) NOT NULL
created_at / updated_at TIMESTAMPTZ NOT NULL
```

For privacy-safe removal events, the job may retain only the server-owned memory UUID and event metadata after the source row is deleted. It must not copy deleted content.

### Privacy and version retention rule

Version history is not allowed to defeat user deletion. When a source memory is forgotten, excluded, or purged:

1. Remove its provenance links.
2. Recompute each affected concept from remaining active sources.
3. Retire or delete the concept when no valid source remains.
4. Delete content-bearing historical versions that are supported only by erased sources.
5. Retain only non-content audit metadata required by policy.

Account-wide memory deletion must purge OKF concepts, versions, sources, and pending jobs for that user in the same user-authorized workflow.

## 8. OKF domain service

Create `backend/app/okf/` with clear layers:

```text
backend/app/okf/
  __init__.py
  types.py
  policy.py
  repository.py
  service.py
  synchronization.py
  retrieval.py
  context.py
  jobs.py
  worker_service.py
  worker_main.py
  evaluation.py
```

`OkfKnowledgeService` owns create, update, read, validate, version, retire, and reconcile operations. Callers submit validated proposals; callers do not mutate ORM rows directly.

Initial typed contracts:

```text
OkfConceptType
OkfConceptStatus
OkfConceptProposal
OkfConceptVersion
OkfEvidence
OkfQueryPlan
OkfRetrievalResult
OkfSyncOutcome
```

Service rules:

- Require authenticated `user_id` on every method.
- Normalize title/key/value deterministically.
- Validate safe JSON using equivalent or shared limits from memory policy.
- Require at least one active source memory for synchronized concepts.
- Use optimistic version checks or row locks to serialize concurrent updates.
- Make repeated jobs idempotent.
- Never execute tools or treat retrieved content as instructions.
- Return typed unavailable/degraded/no-result outcomes; do not convert failures into “the user has no knowledge.”

## 9. Synchronization worker

### Enqueue boundary

When `MemoryWriter` accepts a newly created durable memory and `OKF_SYNC_ENABLED=true`, enqueue one `upsert_memory` outbox row in the same database transaction. The enqueue is a small local database write; concept extraction and restructuring remain outside the voice-response path.

No job is added for an exact duplicate where `MemoryWriter` returns `created=false`, unless reconciliation proves its provenance is missing.

Deletion/update paths that must enqueue reconciliation include:

- manual memory update/supersession;
- `memory_forget` after confirmed deletion;
- REST deletion and delete-all;
- session purge/exclusion transitions;
- account memory disablement;
- future retention cleanup.

### Mapping policy

Start deterministic and narrow:

1. Load one active, owned memory.
2. Re-check user memory setting and session exclusion.
3. Apply the v1 eligibility table.
4. Produce zero or more bounded `OkfConceptProposal` objects.
5. Validate types, canonical keys, JSON limits, confidence, and provenance.
6. Upsert/version concepts transactionally.
7. Mark the job complete only after provenance is durable.

An optional model-based mapper is a later stage. If added, its output is only a proposal and must pass the same schema, grounding, source-span, allow-list, size, secret, and ownership checks. It cannot write directly.

### Conflict and supersession policy

- Same canonical key and equivalent value: attach new provenance; do not create noise versions.
- Same canonical key and a clearly newer explicit value: create a version and make it current.
- Two current, credible, incompatible values: mark the concept `contested`; retain both source links and make the context state the conflict.
- Superseded/deleted source: recompute from remaining sources before choosing a current value.
- Never use model confidence alone to silently overwrite an explicit user-authored value.

### Backfill

Provide a bounded, resumable operator script that scans active eligible memories by `(user_id, created_at, id)` and enqueues idempotent jobs. Backfill is opt-in and is not run automatically on migration or process startup.

## 10. Structured retrieval

`OkfRetrievalService` receives the same authenticated query context as RAG but uses structured fields rather than embeddings in v1.

Retrieval stages:

1. Build a deterministic bounded query plan from the approved `MEMORY_QUERY` transcript.
2. Select concept types and canonical-key/title terms.
3. Resolve exact canonical keys and normalized aliases first.
4. Expand only one parent/child level for projects and preferences in v1.
5. Fetch active/current versions and bounded provenance IDs.
6. Detect contested concepts and missing support.
7. Return typed evidence ordered by exactness, concept specificity, validity, and recency.

Do not combine RAG scores and OKF scores as though they have the same scale.

### Knowledge modes

| Mode | Behavior |
|---|---|
| `rag` | Existing `MemoryRetrievalService` only. This is the default and rollback mode. |
| `okf` | OKF structured retrieval only. Missing/degraded evidence must abstain for personal facts. |
| `hybrid` | Run eligible RAG and OKF reads, preserve separate evidence groups, deduplicate by source memory ID/canonical fact, then compose one bounded context. |

In `hybrid`, one engine may degrade without discarding valid evidence from the other. The response context must identify which engine supplied each item and whether the result is partial. A personal-memory answer still requires supporting evidence.

## 11. Router and orchestration integration

Do not add `OKF_QUERY` to `RouteName` in v1. That would mix user intent with an internal retrieval strategy and expand the frozen router corpus unnecessarily.

Integration order:

```text
final transcript
  -> existing confirmation pre-check
  -> existing LangGraph route decision
  -> MEMORY_QUERY only
  -> KnowledgeSelector(KNOWLEDGE_MODE)
  -> selected engine(s)
  -> KnowledgeContextBuilder
  -> existing LLM request / TTS path
```

Required behavior:

- `GENERAL_LLM`, tool, task/reminder, memory action, and control routes continue to skip knowledge retrieval as currently specified.
- Router `off` and `shadow` retain their current authoritative legacy behavior. OKF must not create side effects from router shadow observation.
- Before the router canary is accepted, OKF retrieval is exercised through unit/integration tests, an operator harness, and optional privacy-safe shadow reads only.
- Cancellation checks occur before and after each engine call and before context injection.
- No OKF result can authorize a write tool.

## 12. Context builder integration

Add a provider-neutral `KnowledgeContext` envelope rather than concatenating arbitrary strings in the gateway.

```text
KnowledgeContext
  mode
  rag_evidence[]
  okf_evidence[]
  source_memory_ids[]
  concept_ids[]
  conflicts[]
  degraded_engines[]
  rendered_text
```

The existing LLM request builder receives one bounded rendered context, separated into explicit untrusted sections:

```text
<rag_evidence>...</rag_evidence>
<okf_evidence>...</okf_evidence>
```

Rules:

- Keep the total within the existing memory/context ceiling; OKF does not increase the model context limit.
- Reserve budgets per engine in `hybrid`, then return unused budget to the other engine.
- Prefer compact atomic OKF facts over repeating the same source memory text.
- Include source IDs internally for audit, but do not expose UUIDs in normal assistant speech.
- Treat both sections as untrusted evidence, never system instructions.
- Explicitly represent conflict, stale validity, partial/degraded results, and no-result.

## 13. Internal management surface

The first release is internal-only. It needs service methods and test/operator tooling, not a new mobile screen.

Minimum internal operations:

- inspect one concept and its versions/sources;
- list concepts by user/type/status with bounded pagination;
- enqueue/retry/reconcile one source memory;
- rebuild or purge one disposable user;
- compare RAG, OKF, and HYBRID results without producing a user-visible response.

If HTTP endpoints are later added, place them behind authenticated owner/admin scopes, return 404 for foreign IDs, apply bounded pagination, and keep mutation paths auditable. Do not expose a debug endpoint in production by default.

## 14. Observability and readiness

Add structured events without transcript, memory text, or concept values:

```text
okf.sync.enqueued
okf.sync.completed
okf.sync.skipped
okf.sync.failed
okf.retrieval.completed
okf.retrieval.degraded
knowledge.selection.completed
knowledge.context.completed
```

Safe fields include correlation IDs, short user/source/concept IDs, mode, counts, policy version, reason codes, queue age, attempts, and durations.

Metrics:

- sync jobs pending/running/retry/dead and oldest age;
- concepts created/updated/contested/retired;
- eligible-memory coverage and orphaned concepts;
- retrieval hit/no-result/degraded rate;
- RAG/OKF overlap by source ID;
- context characters by engine;
- sync, OKF retrieval, knowledge selection, and total turn P50/P95/P99;
- cross-user denial count and deletion-reconciliation lag.

Readiness behavior:

- With `OKF_ENABLED=false`, OKF is absent from readiness.
- With background sync only, worker degradation is reported but must not make the existing voice/RAG path unavailable.
- With `KNOWLEDGE_MODE=okf`, the required OKF database/service checks affect readiness.
- With `KNOWLEDGE_MODE=hybrid`, report each engine separately and mark the result degraded according to an explicit rollout policy; do not hide partial failure.

## 15. Security and privacy requirements

- Every table and query is owner-scoped by `user_id`; composite FKs prevent cross-owner references.
- Account memory disablement and session exclusion apply before enqueue, sync, retrieve, and context injection.
- Foreign IDs return no existence signal beyond the existing ownership policy.
- Reuse secret detection and bounded JSON validation; add adversarial nested/oversized payload tests.
- Retrieved OKF data is untrusted prompt data and cannot change routing, tools, authorization, or system instructions.
- User forget/delete-all/account deletion removes derived OKF content, not only its source link.
- Do not log raw transcript, memory content, concept title/value, source excerpt, or rendered context.
- Backups and retention policy must include OKF tables once enabled.

## 16. Phased implementation todo and gates

### OKF-0 — Freeze contracts and baseline

- [ ] Record a clean implementation-start snapshot: revision, dirty files, active flags, migration head, test evidence, and current router/memory gates.
- [ ] Freeze the eligible concept types, canonical-key grammar, conflict policy, deletion policy, and typed service contracts.
- [ ] Create a labeled OKF corpus covering preferences, projects, decisions, relationships, stable facts, transient facts, secrets, contradictions, supersession, deletion, session exclusion, and cross-user attempts.
- [ ] Capture unchanged RAG results and call/latency counts for the corpus.

**Gate:** approved contracts and a reproducible baseline exist; no runtime behavior changed.

### OKF-1 — Configuration and schema

- [ ] Add safe settings/defaults and validation.
- [ ] Add SQLAlchemy models and one additive Alembic migration for the four OKF tables.
- [ ] Prove upgrade/downgrade on an empty database and upgrade with existing memory/graph rows present.
- [ ] Prove no existing memory, chunk, embedding, FTS, graph, task, reminder, or confirmation row is changed by migration.

**Gate:** migration and configuration tests pass; default startup remains OKF-off and RAG-only.

### OKF-2 — Domain service and provenance

- [ ] Implement typed contracts, normalization, policy, repository, and `OkfKnowledgeService`.
- [ ] Implement atomic create/update/version/contested/retire behavior.
- [ ] Implement owner-scoped provenance links and privacy-safe version cleanup.
- [ ] Add concurrency/idempotency tests for duplicate and conflicting proposals.

**Gate:** all concept changes are versioned, grounded in owned source memories, and isolated across users.

### OKF-3 — Async synchronization

- [ ] Add transactional job enqueue behind `OKF_SYNC_ENABLED`.
- [ ] Implement lease/retry/dead-letter/recovery behavior in the dedicated worker.
- [ ] Add deterministic v1 mapping and reconciliation for create, supersede, forget, delete-all, session purge/exclusion, and memory disablement.
- [ ] Add the resumable backfill command but do not run it automatically.
- [ ] Verify voice response completion does not await concept processing.

**Gate:** no lost or duplicate durable concept mutations under retry/concurrency, deletion converges, and OKF-off produces no new jobs.

### OKF-4 — Structured retrieval and evaluation

- [ ] Implement deterministic query planning and bounded owner-scoped retrieval.
- [ ] Cover parent/child project expansion, exact preferences, relationships, current value, conflict, no-result, and degraded database cases.
- [ ] Add an operator comparison harness for RAG vs OKF vs HYBRID.
- [ ] Measure accuracy, provenance coverage, no-result correctness, and latency on the labeled corpus.

**Gate:** zero cross-user leakage; every returned fact has active provenance; deleted facts never return; thresholds are approved from baseline data rather than invented after the run.

### OKF-5 — Knowledge selector and context builder

- [ ] Add the engine interface/adapters and validated `rag|okf|hybrid` selector.
- [ ] Add the bounded `KnowledgeContext` envelope and render separate untrusted evidence sections.
- [ ] Preserve current RAG behavior in `rag` mode and when OKF is disabled.
- [ ] Add cancellation, conflict, partial failure, duplicate evidence, and prompt-injection tests.

**Gate:** RAG regression corpus is unchanged in default mode; OKF/HYBRID are grounded and bounded; action routes still perform zero retrieval.

### OKF-6 — Shadow and development cohort

- [ ] Run OKF sync for disposable users only and inspect concepts/provenance before any response injection.
- [ ] Enable `OKF_SHADOW_READS=true` for a development cohort while keeping `KNOWLEDGE_MODE=rag` authoritative.
- [ ] Compare result quality, overlap, no-result behavior, sync lag, and P50/P95/P99 latency without logging content.
- [ ] Review conflicts and deletion convergence; tune policy only against labeled evidence.

**Gate:** shadow report passes privacy, ownership, grounding, deletion, and latency criteria. Existing router Phase 3/9 gates remain separate.

### OKF-7 — Controlled mode trials

- [ ] Trial `KNOWLEDGE_MODE=okf` on the disposable cohort.
- [ ] Trial `KNOWLEDGE_MODE=hybrid` and measure incremental value versus cost/latency.
- [ ] Run text-path acceptance first, then physical voice regression only when current router rollout prerequisites allow it.
- [ ] Verify rollback to `KNOWLEDGE_MODE=rag` and emergency `OKF_ENABLED=false` without data migration.

**Gate:** accepted comparison report, rollback drill, zero unsupported personal claims, zero cross-user leakage, zero deletion regressions, and no material voice-protocol regression.

### OKF-8 — Production readiness

- [ ] Define backup/restore, retention, on-call diagnostics, dead-letter recovery, and reconciliation runbooks.
- [ ] Resolve or explicitly disposition all current router/release blockers relevant to the deployment.
- [ ] Obtain product/privacy acceptance for what is promoted to OKF and how users inspect/correct/delete it.
- [ ] Keep subscription/entitlement work as a separate future plan.

**Gate:** production owner signs off. `KNOWLEDGE_MODE=rag` remains available as an immediate rollback.

## 17. Test matrix

At minimum, cover:

| Category | Required cases |
|---|---|
| Schema | Empty upgrade/downgrade, existing-data upgrade, constraints, owner-scoped FKs, downgrade guard with live rows. |
| Mapping | Eligible types, ineligible event/summary, tentative/transient language, secrets, oversized/deep JSON, Unicode normalization. |
| Versioning | Exact duplicate, newer value, older replay, contradiction, supersession, restore, concurrent updates. |
| Provenance | One source to many concepts, many sources to one concept, missing/foreign/deleted source, policy-version audit. |
| Jobs | Duplicate delivery, lease expiry, retry, permanent failure, crash after write/before completion, two users concurrently, backfill resume. |
| Privacy | Account opt-out, excluded session, forget, delete-all, account delete, source-only version cleanup, logs contain no content. |
| Retrieval | Exact project, preference, relationship, child expansion, conflict, no result, partial/degraded, bounded limits. |
| Modes | RAG parity, OKF-only, HYBRID dedupe, engine failure, invalid configuration, master switch rollback. |
| Router | Retrieval only for `MEMORY_QUERY`; zero retrieval for general/actions/tools/control; shadow has no user-visible effect. |
| Context | Character budget, source IDs, untrusted delimiters, conflict language, prompt injection, cancellation. |
| End to end | Source memory -> async job -> concept -> query -> context -> grounded answer; then update and forget the source. |

Do not run every web/API/mobile build for each phase. Use focused backend tests and migration checks first; run broader suites and physical/mobile validation only at their defined gates.

## 18. Proposed file touch points

Expected new files:

```text
backend/app/okf/*.py
backend/migrations/versions/0017_okf_knowledge_foundation.py
backend/tests/test_okf_*.py
backend/scripts/okf_backfill.py
backend/scripts/okf_evaluation.py
docs/evidence/okf/*
```

Expected existing integration points, changed only when their phase requires it:

```text
backend/app/core/config.py
backend/app/models/resources.py
backend/app/models/__init__.py
backend/app/main.py
backend/app/memory/writer.py
backend/app/memory/tool_tools.py
backend/app/api/memories.py
backend/app/api/sessions.py
backend/app/websocket/gateway.py
backend/app/llm/context.py
backend/app/api/routes.py              # only if an internal API is approved later
.env.example
README.md
implementation.md
```

Do not modify the retrieval algorithms in `backend/app/memory/retrieval.py` for the initial OKF implementation. If an adapter is needed, wrap `MemoryRetrievalService` from the new knowledge-selection layer.

## 19. Rollback

Response-path rollback:

1. Set `KNOWLEDGE_MODE=rag`.
2. Reload backend instances because settings are startup-loaded.
3. Verify `/health`, `/ready`, and one authenticated RAG memory query.
4. Confirm no OKF context events are injected.

Emergency OKF shutdown:

1. Set `OKF_SYNC_ENABLED=false` to stop new sync work.
2. Set `OKF_ENABLED=false` and keep `KNOWLEDGE_MODE=rag`.
3. Reload/drain all instances.
4. Preserve PostgreSQL OKF rows for diagnosis unless privacy deletion requires removal.
5. Do not alter existing memories, embeddings, graph rows, confirmations, tasks, or reminders.

Rollback must not require a reverse migration. Schema removal is a separate maintenance action after retained data has been handled safely.

## 20. Deferred entitlement seam

Later, an entitlement policy may constrain the requested mode:

```text
requested knowledge mode
        |
        v
EntitlementPolicy.allowed_mode(user, requested_mode)
        |
        v
KnowledgeSelector
```

The future layer may choose or deny a mode, but it must not fork storage, migrate memories, change concept ownership, or introduce plan checks inside either engine. No entitlement code belongs in the current OKF implementation.

## 21. Definition of done

OKF is implemented only when:

- all OKF-0 through the intended rollout phase gates are evidenced;
- default `rag` mode is regression-equivalent to the pre-OKF baseline;
- OKF and HYBRID results are source-grounded, bounded, owner-scoped, and deletion-correct;
- voice responses never wait for OKF synchronization;
- feature flags and rollback are proven;
- no subscription assumption exists in OKF storage or services;
- current Hybrid RAG and GraphRAG data remain intact;
- a dated work record lists every edited file, verification command, result, limitation, and remaining gate for each implementation phase.
