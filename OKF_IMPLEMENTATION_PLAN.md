# OKF structured knowledge layer implementation plan

**Plan date:** 2026-09-28; verified and revised 2026-09-29 (America/Los_Angeles)
**Status:** OKF-0 through OKF-6 safeguards are implemented in the working tree; OKF-5 passed focused integration after remediation on 2026-09-29/30; OKF-4 and OKF-6 remain acceptance-review pending (follow-up evidence: `docs/20260929_1725_okf_blocker_remediation.md`)
**Default rollout:** `OKF_ENABLED=false`, `OKF_SYNC_ENABLED=false`, `KNOWLEDGE_MODE=rag`  
**Scope:** Add a user-scoped structured knowledge capability beside the existing Hybrid RAG. Subscription and entitlement policy are explicitly deferred.

## 1. Executive decision

Implement OKF as a separate internal engine behind the current memory/orchestration boundary.

```text
Final voice transcript / supported text request
                    |
                    v
       Confirmation and policy pre-checks
                    |
          +---------+---------+
          |                   |
          v                   v
 Router off/shadow       Router canary/on
 legacy orchestration    LangGraph decision
          |                   |
          +---------+---------+
                    |
       Eligible personal-memory query
                    |
                    v
           Knowledge Selector
                    |
          +---------+---------+
          |                   |
          v                   v
 Existing Hybrid RAG       OKF engine
          |                   |
          +---------+---------+
                    |
                    v
      Knowledge evaluation and context
                    |
          +---------+---------+
          |                   |
          v                   v
  Safe direct answer     Existing LLM / TTS
```

The first release must satisfy these invariants:

1. Do not migrate, rewrite, delete, or reinterpret existing `memory_items`, chunks, embeddings, FTS, RRF, reranker, or GraphRAG data.
2. Do not add subscription, pricing, plan, or entitlement assumptions to OKF domain code.
3. Keep `RAG` as the default knowledge mode and preserve equivalent behavior while OKF is disabled.
4. Build OKF from already-persisted, user-owned durable memories. Do not make the voice response wait for OKF extraction or restructuring.
5. Keep every content-bearing OKF concept/assertion/version traceable to active owned source memory and a policy version.
6. Apply the existing account memory setting, session exclusion, user ownership, cancellation, and privacy rules to OKF.
7. Do not create a second LLM pipeline. Feed bounded OKF evidence into the existing direct-answer/evaluation and context-building boundaries.
8. A user-authorized delete or exclusion must make affected OKF data non-readable in the same transaction; asynchronous reconciliation may rebuild from remaining evidence but may not create a stale-read window.

For this project, **OKF** means the structured user-knowledge capability described in this plan. It does not imply adoption of an external library or wire standard.

## 2. Verified current project status

The repository was re-inspected on 2026-09-29 after the original plan was written.

| Area | Verified state | Consequence for OKF |
|---|---|---|
| Working tree | Clean at the 2026-09-29 verification point. | Record a new implementation-start snapshot and preserve any later owner changes. |
| Durable memory | `memory_items`, `memory_chunks`, source IDs, status, validity, supersession, confidence, salience, and owner-scoped constraints exist. | Use memories as OKF source evidence; do not replace them. |
| Hybrid retrieval | Structured, FTS, dense BGE-M3, RRF, and reranking are implemented in `MemoryRetrievalService`. | Treat this service as the unchanged RAG engine. |
| Memory writes | Explicit/manual/automatic writes converge through `MemoryWriter`; database-backed extraction and embedding jobs exist. | Add an asynchronous OKF enqueue seam after a new durable memory is accepted. |
| Existing graph | `backend/app/graph/` and migration `0012` implement a separate GraphRAG entity/relationship index. | Do not rename or repurpose GraphRAG as OKF. Keep its flags and tables independent. |
| Context assembly | Memory evidence is bounded and passed through `backend/app/memory/context.py` into `backend/app/llm/context.py`. | Add a knowledge composition layer at this boundary. |
| Router | `MEMORY_QUERY` exists. Checked-in defaults remain `ROUTER_MODE=off`, cohort `0`, while the local ignored `.env` currently contains `ROUTER_MODE=on`, cohort `100`. Live process state was not inferred from the file. | Do not add a new top-level router intent. Support both the legacy off/shadow path and the explicit canary/on `MEMORY_QUERY` path; local configuration does not constitute rollout acceptance. |
| Local memory flags | `MEMORY_RETRIEVAL_MODE=inject`, `MEMORY_WRITE_ENABLED=true`. | OKF must not change the currently active local RAG path when disabled. |
| Local GraphRAG flags | `GRAPH_RAG_MODE=off`, `GRAPH_WRITE_ENABLED=false`. | OKF rollout must not silently enable GraphRAG. |
| Router gates | Phase 0 is an effective pass by owner override while its automated evidence remains incomplete; Phase 7 implementation/action-safety checks pass; Phase 3 and Phase 9 remain not passed. | Offline OKF work may proceed, but live voice injection/canary must respect the existing router gates. |
| Memory acceptance | Core retrieval, extraction, worker, ownership, and grounded-answer checks pass; broader physical/privacy rollout evidence remains pending. | Reuse proven boundaries and do not claim OKF production readiness from unit tests alone. |
| Latest backend evidence | The latest recorded full suite before the 2026-09-29 follow-up changes reports 598 passed and 56 skipped. Later records report focused runs of 124 and 310 passing tests. This revision ran 200 focused configuration/router/memory tests successfully. | These are separate evidence points, not a claim that the current full suite was rerun after every later change. |
| OKF | No OKF files, settings, tables, APIs, or tests were found. | Implementation starts at Phase OKF-0 below. |
| Migration head | `0016_one_self_entity_per_owner`. | The first OKF migration is `0017`; verify the head again immediately before implementation. |
| Account deletion | No user/account-deletion workflow exists in the current API. Memory delete-all exists. | Integrate delete-all now; treat account deletion as a future integration requirement unless that workflow is separately added. |

Known unrelated release blockers remain in force: physical voice/TTS/barge-in acceptance, live router shadow evidence, canary/rollback evidence, and previously documented repository-wide frontend/format checks. OKF must not be used to mark any of those gates complete.

## 3. Scope and non-goals

### In scope

- Durable structured concepts for profile facts, preferences, projects, decisions, relationships, and other stable facts.
- Immutable version history and source-memory provenance.
- Asynchronous memory-to-OKF synchronization and safe reconciliation after update, supersession, deletion, opt-out, or session purge.
- Owner-scoped structured lookup.
- Internal `rag`, `okf`, and `combined` knowledge modes.
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
    # Chooses rag, okf, or combined from validated configuration/policy.
    ...
```

Do not pass subscription objects into `HybridRagEngine` or `OkfEngine`. A future entitlement check may produce an allowed mode before `KnowledgeSelector`, but the engines remain unaware of why a mode was selected.

The existing `MEMORY_QUERY` route remains the semantic intent in router `canary|on`. In router `off|shadow`, the current legacy eligibility behavior remains authoritative. `KnowledgeSelector` decides how an eligible personal-memory query is fulfilled without changing which top-level intent owns the turn.

The selector returns a provider-neutral result to a knowledge-evaluation layer. That layer must preserve the current safety dispositions rather than forcing every result into the LLM:

```text
direct_answer | continue_with_evidence | no_result | unavailable | conflict | cancelled
```

`HybridRagEngine` adapts both `MemoryRetrievalService` and its existing `evaluate_memory_result` / direct-answer behavior. OKF and combined modes must produce equivalent typed dispositions before the gateway decides whether to speak directly or continue through the existing LLM/TTS pipeline.

## 5. Configuration contract

Add settings with safe defaults:

```text
OKF_ENABLED=false
OKF_SYNC_ENABLED=false
KNOWLEDGE_MODE=rag              # rag | okf | combined
KNOWLEDGE_RAG_TIMEOUT_MS=30000  # bounded only when an opted-in selector runs RAG
OKF_SHADOW_READS=false
OKF_SHADOW_USER_IDS=[]        # explicit disposable/test owner UUID allowlist; required when shadow reads are enabled
OKF_CONTEXT_MAX_CHARS=4000
OKF_QUERY_LIMIT=20
OKF_RETRIEVAL_TIMEOUT_MS=75
OKF_SHADOW_MAX_CONCURRENT=4
OKF_WORKER_MODE=in_process       # in_process | standalone
OKF_WORKER_POLL_INTERVAL_SECONDS=1.0
OKF_WORKER_SHUTDOWN_TIMEOUT_SECONDS=30.0
OKF_JOB_LEASE_SECONDS=120
OKF_JOB_MAX_ATTEMPTS=5
OKF_POLICY_VERSION=okf-v1
```

Validation rules:

- `KNOWLEDGE_MODE=okf|combined` requires `OKF_ENABLED=true`.
- `OKF_SYNC_ENABLED=true` requires `OKF_ENABLED=true`.
- `OKF_SHADOW_READS=true` requires `OKF_ENABLED=true`, `KNOWLEDGE_MODE=rag`, and a non-empty allowlist of disposable/test owner UUIDs; it never injects context.
- `OKF_WORKER_MODE=standalone` requires the web process not to start the OKF worker; deployment must start `worker_main` separately.
- `KNOWLEDGE_MODE=rag` invokes the current retrieval behavior only.
- Existing `MEMORY_RETRIEVAL_MODE`, `MEMORY_WRITE_ENABLED`, `GRAPH_RAG_MODE`, and `GRAPH_WRITE_ENABLED` retain their current meanings.
- `okf` and `combined` must still honor account `memory_enabled=false` and session `memory_excluded=true`.
- No subscription environment variables are added.

`OKF_ENABLED` is the emergency master switch. `KNOWLEDGE_MODE=rag` is the response-path rollback switch. They serve different operational purposes.

`OKF_SYNC_ENABLED` controls new enqueue and worker consumption; it is not a privacy control. Deletes and exclusions must still execute their synchronous OKF privacy barrier even when background sync is disabled. `OKF_ENABLED=false` prevents OKF retrieval and worker startup but does not delete retained rows.

### Configuration interaction matrix

| Configuration | Required behavior |
|---|---|
| `OKF_ENABLED=false` | Preserve current behavior exactly; do not enqueue, run shadow reads, start an OKF worker, or expose OKF readiness. |
| `KNOWLEDGE_MODE=rag` | Use the current `MemoryRetrievalService`/evaluation path. Existing GraphRAG flags keep their independent meaning. |
| `KNOWLEDGE_MODE=okf` | Use only OKF for eligible personal-memory reads; fail closed to `unavailable`/`no_result`, never silently fall back to an unsupported personal claim. |
| `KNOWLEDGE_MODE=combined` | Run RAG and OKF concurrently with separate deadlines and evidence groups, then evaluate/deduplicate without mixing score scales. |
| `OKF_SHADOW_READS=true` | Valid only with `KNOWLEDGE_MODE=rag` and explicit `OKF_SHADOW_USER_IDS` disposable/test owners. Run bounded OKF reads only when the authoritative path considers the turn memory-eligible. Use an independent read session, timeout, and non-blocking capacity gate; skip on capacity and never delay or alter the response. |
| `GRAPH_RAG_MODE=*` | Remains independent. OKF does not enable, write, query, or rename GraphRAG. Any later three-engine composition requires a separate accepted plan. |

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

### Deterministic v1 mapping contract

V1 does not infer concept structure from arbitrary memory prose. A source is eligible only when its existing structured fields are sufficient for one of these allow-listed rules:

| Source memory contract | OKF output | Rule |
|---|---|---|
| `preference` with non-blank normalized `subject` and `predicate`, scalar/bounded `object_json` | `preference` | Key is generated from the normalized subject/predicate; value comes only from `object_json`. |
| `project` with an explicit project subject | project identity and, when predicate/object are present, one project child fact | The project name and child key use allow-listed normalization; free text is display evidence only. |
| `relationship` with explicit subject, allow-listed predicate, and scalar target object | `relationship` | Predicates are mapped through a fixed relationship allow-list; unknown predicates abstain. |
| `fact` with explicit subject, predicate, and scalar/bounded object | `fact`; optionally `profile` or `decision` only through an approved predicate map | `profile` and `decision` are not inferred from prose. Their source predicates must be explicitly allow-listed and versioned. |
| `event`, `routine`, or `summary`; missing structured fields; free-text-only memory | none | V1 abstains and leaves the source available to RAG. |

The mapper never parses the source `content` to manufacture a value, identity, relationship, stability judgment, or decision. Content may be used only for display/provenance after the proposal is grounded in the structured source fields. Secrets are rejected before proposal creation. Any future model-based or prose parser is a new policy version and rollout stage.

## 7. Data model

Add new tables only. Do not change the meaning or contents of existing memory or graph tables. V1 uses five tables because concept identity and independently supported competing assertions must not be collapsed into one row.

### `okf_concepts`

Stable concept identity and aggregate state. Values live in assertions so a contested concept can retain multiple current claims without silently selecting one.

```text
id UUID PK
user_id UUID NOT NULL
parent_concept_id UUID NULL
concept_type VARCHAR(32) NOT NULL
canonical_key VARCHAR(512) NOT NULL
title VARCHAR(512) NOT NULL
status active | contested | retired
policy_version VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Required constraints:

- Composite ownership keys/FKs include `user_id`.
- Unique `(id, user_id)` supports owner-scoped references.
- Unique `(user_id, canonical_key)` for current identity.
- Parent concept must belong to the same user; deleting a parent sets only `parent_concept_id` to null and retains the non-null owner.
- Status and concept type have database checks.
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

### `okf_concept_assertions`

Current independently supported values for a concept. One active assertion means the concept is `active`; two or more incompatible active assertions mean it is `contested`; zero means it is `retired`.

```text
id UUID PK
user_id UUID NOT NULL
concept_id UUID NOT NULL
value_json JSONB NOT NULL
display_text TEXT NOT NULL
status active | superseded | retired
current_version INTEGER NOT NULL
confidence REAL NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
policy_version VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Required constraints:

- Unique `(id, user_id)` and owner-scoped FK `(concept_id, user_id)`; equivalent active values are deduplicated under a locked concept transaction.
- JSON size/depth limits are validated before persistence.
- Status, confidence, and validity ranges have database checks.
- Index `(user_id, concept_id, status)`.
- `current_version` must match the assertion's latest version. Enforce this with a deferred composite FK after both tables exist, or, if the migration cannot safely create the cycle, with a transaction-level invariant plus database integration tests. It may not remain an undocumented application convention.

### `okf_concept_versions`

Immutable snapshots of each accepted assertion change.

```text
id UUID PK
user_id UUID NOT NULL
concept_id UUID NOT NULL
assertion_id UUID NOT NULL
version INTEGER NOT NULL
value_json JSONB NOT NULL
display_text TEXT NOT NULL
status active | superseded | retired
confidence REAL NOT NULL
valid_from TIMESTAMPTZ NULL
valid_to TIMESTAMPTZ NULL
change_kind create | update | supersede | retire | restore
policy_version VARCHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Unique `(id, user_id)` and unique `(user_id, assertion_id, version)`. Both concept and assertion foreign keys are owner-scoped. The assertion is a materialized projection of its latest accepted version. Multiple active assertions are intentional only for a contested concept.

### `okf_concept_sources`

Many-to-many provenance from a concept version to original memory evidence.

```text
user_id UUID NOT NULL
concept_version_id UUID NOT NULL
memory_id UUID NOT NULL
evidence_role supports | contradicts | supersedes
created_at TIMESTAMPTZ NOT NULL
```

All FKs are owner-scoped. Primary key `(user_id, concept_version_id, memory_id, evidence_role)` prevents duplicate provenance. Index `(user_id, memory_id)` supports deletion lookup. Do not duplicate raw memory text or a reversible/plain content hash in this link table. The existing immutable memory UUID plus policy version is sufficient for v1 reconciliation. If a future fingerprint is required, it must be a domain-separated keyed HMAC with a documented key-rotation and deletion policy.

### `okf_sync_jobs`

A dedicated durable outbox/worker queue keeps OKF lifecycle independent from existing embedding and GraphRAG jobs.

```text
id UUID PK
user_id UUID NOT NULL
memory_id UUID NULL
event_type upsert_memory | remove_memory | rebuild_user | purge_user
idempotency_key VARCHAR(512) NOT NULL
status pending | running | retry_wait | completed | dead | cancelled
attempts INTEGER NOT NULL
available_at / locked_at / completed_at TIMESTAMPTZ
last_error_code VARCHAR(128) NULL
policy_version VARCHAR(64) NOT NULL
created_at / updated_at TIMESTAMPTZ NOT NULL
```

Unique `(user_id, idempotency_key)` matches the current owner-scoped job convention. Claiming uses `FOR UPDATE SKIP LOCKED`, a lease, and bounded retry/dead-letter handling.

`memory_id` is an event correlation UUID, not a foreign key to `memory_items`, because a removal job must survive deletion of the source row. It never carries source content. Upsert workers must independently load and owner-check an active source before writing. Removal/rebuild keys include user, event type, source UUID or generation, and policy version so retries deduplicate without preventing a later explicit rebuild.

### Privacy and version retention rule

Version history is not allowed to defeat user deletion. The user-authorized transaction that forgets, excludes, or purges a source must first identify affected concept/assertion IDs and establish a synchronous privacy barrier:

1. Remove the source's provenance links.
2. Delete content-bearing versions supported only by erased sources.
3. Retire assertions that remain supported but are no longer current; delete assertions/versions and content-bearing concept identity when erased evidence is their only remaining support.
4. Recalculate the parent concept state (`active`, `contested`, or `retired`) from the remaining readable assertions.
5. Ensure retrieval requires at least one current active provenance row, so an interrupted reconciliation cannot return unsupported content.
6. Enqueue a content-free `remove_memory` or `rebuild_user` job in the same transaction for any deeper recomputation from remaining sources.

The transaction may conservatively retire all affected assertions and let the worker restore supported ones. It may not leave deleted or excluded evidence readable while waiting for the worker.

Memory delete-all must synchronously purge OKF concepts, assertions, versions, sources, and non-running jobs for that user in the same user-authorized transaction. A running job must be cancelled or made unable to commit through a user memory-version/generation check. Future account deletion must call the same purge service before or through owner-cascade deletion; account deletion itself is not part of the current API and is not claimed as a v1 delivered flow.

Account `memory_enabled=false` is access disablement, not deletion, matching current project semantics. It immediately denies OKF reads, stops new ordinary sync work, cancels pending/retry jobs, and prevents running jobs from committing after a fresh policy check. Derived rows may remain encrypted/retained with the source memories. Re-enabling schedules a bounded reconciliation. A future product decision to purge on disable requires a separate migration/privacy policy.

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
OkfAssertion
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
- Require at least one current active provenance row for every retrievable assertion.
- Use optimistic version checks or row locks to serialize concurrent updates.
- Lock the concept identity while deduplicating equivalent assertions or changing contested state.
- Make repeated jobs idempotent.
- Never execute tools or treat retrieved content as instructions.
- Return typed unavailable/degraded/no-result outcomes; do not convert failures into “the user has no knowledge.”

## 9. Synchronization worker

### Enqueue boundary

When `MemoryWriter` accepts a newly created durable memory and `OKF_SYNC_ENABLED=true`, enqueue one `upsert_memory` outbox row in the same database transaction. The enqueue is a small local database write; concept extraction and restructuring remain outside the voice-response path. Worker commit rechecks the user's memory generation/version so delete-all, disablement, or exclusion racing with a claimed job cannot reintroduce removed knowledge.

No job is added for an exact duplicate where `MemoryWriter` returns `created=false`, unless reconciliation proves its provenance is missing.

Lifecycle paths that must invoke the shared synchronous privacy barrier and/or enqueue reconciliation include:

- manual memory update/supersession;
- `memory_forget` in the confirmed deletion transaction;
- REST deletion and delete-all;
- session deletion, purge, and transition to excluded;
- account memory disablement and re-enablement;
- future retention cleanup.

Do not scatter raw OKF row mutation through these endpoints. Add one owner-scoped lifecycle service used by `memory_forget`, REST update/delete/delete-all, session exclusion/deletion, and any future retention/account-deletion flow.

Memory update/supersession must synchronously make assertions supported only by the old now-inactive source non-current before commit, then enqueue the new memory. This is primarily a correctness barrier; hard deletion/exclusion additionally applies the content-removal privacy rules above.

### Mapping policy

Start deterministic and narrow:

1. Load one active, owned memory.
2. Re-check user memory setting, user memory generation/version, and source-session exclusion.
3. Apply only the deterministic v1 mapping table; abstain when structured fields are insufficient.
4. Produce zero or more bounded `OkfConceptProposal` and assertion objects.
5. Validate types, canonical keys, predicate allow-lists, JSON limits, confidence, secrets, and provenance.
6. Lock/upsert the concept, deduplicate or create assertions, append immutable versions, link provenance, and derive concept state transactionally.
7. Re-check the privacy generation/version before commit.
8. Mark the job complete only after provenance is durable.

An optional model-based mapper is a later stage. If added, its output is only a proposal and must pass the same schema, grounding, source-span, allow-list, size, secret, and ownership checks. It cannot write directly.

### Conflict and supersession policy

- Same canonical key and equivalent value: attach new provenance to the active assertion/version; do not create noise versions.
- Same canonical key and a clearly newer explicit value that explicitly supersedes the old source: append a version, supersede the old assertion, and make the new assertion active.
- Two current, credible, incompatible values without explicit supersession: retain two active assertions and mark the concept `contested`; no single materialized value is authoritative.
- Superseded/deleted source: recompute supported active assertions and concept state from remaining sources; do not choose a winner when incompatible assertions remain.
- Never use model confidence alone to silently overwrite an explicit user-authored value.

### Worker deployment

V1 follows the existing `MemoryWorkerService` lifecycle: with `OKF_WORKER_MODE=in_process`, each web instance may start an `OkfWorkerService` when OKF and sync are enabled. Database claiming with `FOR UPDATE SKIP LOCKED` makes multiple instances safe. `worker_main.py` supports a later standalone deployment, but the same deployment must not run both modes. Readiness reports the configured mode, queue health, and oldest job age without making ordinary RAG/voice unavailable when OKF is not authoritative. If standalone mode is selected, its service/process configuration is a required deployment change rather than an undocumented manual command.

### Backfill

Provide a bounded, resumable operator script that scans active eligible memories by `(user_id, created_at, id)` and enqueues idempotent jobs. Backfill is opt-in and is not run automatically on migration or process startup.

## 10. Structured retrieval

`OkfRetrievalService` receives the same authenticated query context as RAG but uses structured fields rather than embeddings in v1.

Retrieval stages:

1. Build a deterministic bounded query plan from the approved `MEMORY_QUERY` transcript.
2. Select concept types and canonical-key/title terms.
3. Resolve exact canonical keys and normalized titles first. V1 has no alias store; alias expansion is deferred unless an explicit bounded alias column/table is added to the schema and tests.
4. Expand only one parent/child level for projects and preferences in v1.
5. Fetch active assertions, their current versions, and bounded active provenance IDs.
6. Detect contested concepts and missing support.
7. Return typed evidence ordered by exactness, concept specificity, validity, and recency.

Do not combine RAG scores and OKF scores as though they have the same scale.

### Knowledge modes

| Mode | Behavior |
|---|---|
| `rag` | Existing `MemoryRetrievalService` only. This is the default and rollback mode. |
| `okf` | OKF structured retrieval only. Missing/degraded evidence must abstain for personal facts. |
| `combined` | Run eligible RAG and OKF reads concurrently, preserve separate evidence groups, deduplicate by source memory ID/canonical fact, then compose one bounded context. |

In `combined`, one engine may degrade without discarding valid evidence from the other. The response context must identify which engine supplied each item and whether the result is partial. A personal-memory answer still requires supporting evidence. RAG and OKF scores remain in separate namespaces; deterministic precedence is exact supported OKF assertion, then accepted RAG evaluation, with conflicts forcing an explicit conflict disposition rather than a guessed winner.

## 11. Router and orchestration integration

Do not add `OKF_QUERY` to `RouteName` in v1. That would mix user intent with an internal retrieval strategy and expand the frozen router corpus unnecessarily.

Integration order:

```text
final transcript
  -> existing confirmation pre-check
  -> router canary/on: existing LangGraph route decision -> MEMORY_QUERY only
     OR router off/shadow: existing authoritative legacy memory eligibility
  -> KnowledgeSelector(KNOWLEDGE_MODE)
  -> selected engine(s)
  -> KnowledgeEvaluator
  -> safe direct answer OR KnowledgeContextBuilder
  -> existing LLM request / TTS path when evidence requires the LLM
```

Required behavior:

- In router `canary|on`, `GENERAL_LLM`, tool, task/reminder, memory action, and control routes continue to skip knowledge retrieval.
- In router `off|shadow`, preserve the current legacy retrieval decision and call pattern exactly while `OKF_ENABLED=false` or `KNOWLEDGE_MODE=rag`; do not incorrectly assume every legacy general turn currently skips RAG.
- Router `off` and `shadow` retain their current authoritative legacy behavior. OKF must not create side effects from router shadow observation.
- OKF shadow reads are triggered only for turns considered memory-eligible by the authoritative path. They use a separate read-only session, deadline, and capacity limit; they are never awaited by the user-visible response and never reuse the request session after it closes.
- Before the router canary is accepted, OKF retrieval is exercised through unit/integration tests, an operator harness, and optional privacy-safe shadow reads only. This does not require enabling router canary/on.
- Cancellation checks occur before and after each engine call and before context injection.
- No OKF result can authorize a write tool.
- Preserve the current RAG evaluation dispositions and evidence audit IDs in `rag` mode. Add equivalent concept/assertion/source IDs for OKF without exposing UUIDs in speech.

## 12. Context builder integration

Add a provider-neutral `KnowledgeContext` envelope rather than concatenating arbitrary strings in the gateway.

```text
KnowledgeContext
  mode
  disposition
  rag_evidence[]
  okf_evidence[]
  source_memory_ids[]
  concept_ids[]
  assertion_ids[]
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
- Reserve budgets per engine in `combined`, then return unused budget to the other engine.
- Prefer compact atomic OKF facts over repeating the same source memory text.
- Include source IDs internally for audit, but do not expose UUIDs in normal assistant speech.
- Treat both sections as untrusted evidence, never system instructions.
- Explicitly represent conflict, stale validity, partial/degraded results, and no-result.
- `rag` mode must preserve the current `<memories>` prompt bytes/semantics for regression parity while OKF is disabled. The new `<rag_evidence>`/`<okf_evidence>` envelope is used only by enabled OKF modes until a separately tested prompt migration is accepted.

## 13. Internal management surface

The first release is internal-only. It needs service methods and test/operator tooling, not a new mobile screen.

Minimum internal operations:

- inspect one concept, its assertions, versions, and sources;
- list concepts by user/type/status with bounded pagination;
- enqueue/retry/reconcile one source memory;
- rebuild or purge one disposable user;
- compare RAG, OKF, and COMBINED results without producing a user-visible response.

If HTTP endpoints are later added, place them behind authenticated owner/admin scopes, return 404 for foreign IDs, apply bounded pagination, and keep mutation paths auditable. Do not expose a debug endpoint in production by default.

## 14. Observability and readiness

Add structured events without transcript, memory text, or concept values:

```text
okf.sync.enqueued
okf.sync.completed
okf.sync.skipped
okf.sync.failed
okf.privacy_barrier.completed
okf.privacy_barrier.failed
okf.retrieval.completed
okf.retrieval.degraded
knowledge.selection.completed
knowledge.context.completed
```

Safe fields include correlation IDs, short user/source/concept IDs, mode, counts, policy version, reason codes, queue age, attempts, and durations.

Metrics:

- sync jobs pending/running/retry/dead and oldest age;
- concepts and assertions created/updated/contested/retired;
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
- With `KNOWLEDGE_MODE=combined`, report each engine separately and mark the result degraded according to an explicit rollout policy; do not hide partial failure.

## 15. Security and privacy requirements

- Every table and query is owner-scoped by `user_id`; composite FKs prevent cross-owner references.
- Account memory disablement and session exclusion apply before enqueue, sync, retrieve, and context injection.
- Foreign IDs return no existence signal beyond the existing ownership policy.
- Reuse secret detection and bounded JSON validation; add adversarial nested/oversized payload tests.
- Retrieved OKF data is untrusted prompt data and cannot change routing, tools, authorization, or system instructions.
- User forget, exclusion, session deletion, and delete-all establish the synchronous privacy barrier before commit; future account deletion must reuse the same purge service when that workflow exists.
- Do not log raw transcript, memory content, concept title/value, source excerpt, or rendered context.
- Backups and retention policy must include OKF tables once enabled.

## 16. Phased implementation todo and gates

### OKF-0 — Freeze contracts and baseline

- [x] Record a clean implementation-start snapshot: revision, dirty files, active flags, migration head, test evidence, and current router/memory gates.
- [x] Freeze the eligible concept types, deterministic source-field mapping, canonical-key grammar, assertion/conflict policy, synchronous deletion barrier, and typed service contracts.
- [x] Create a labeled OKF corpus covering preferences, projects, decisions, relationships, stable facts, transient facts, secrets, contradictions, supersession, deletion, session exclusion, and cross-user attempts.
- [x] Capture unchanged RAG results and call/latency counts for the corpus.

**Gate: PASS (2026-09-29).** The frozen contract, 14-case synthetic corpus, implementation-start snapshot, reproducible post-retrieval RAG decision baseline, and verification record are stored under `backend/tests/fixtures/`, `backend/scripts/`, and `docs/evidence/okf/`. The baseline passed all cases and changed no runtime behavior. Live provider/database retrieval remains governed by the existing Phase 6 evidence and must be rechecked at OKF-5 integration.

### OKF-1 — Configuration and schema

- [x] Add safe settings/defaults and validation.
- [x] Add SQLAlchemy models and one additive Alembic migration for the five OKF tables.
- [x] Prove upgrade/downgrade on an empty database and upgrade with existing memory/graph rows present.
- [x] Prove no existing memory, chunk, embedding, FTS, graph, task, reminder, or confirmation row is changed by migration.

**Gate: PASS (2026-09-29).** The additive `0017_okf_configuration_schema` migration passed disposable-PostgreSQL empty upgrade/downgrade, populated upgrade, live-row downgrade guard, owner-scope, deferred current-version, partial parent-detach, and content-free removal-job checks. Before/after fingerprints were identical for existing memory, chunk (including embedding and generated FTS), graph, task, reminder, and durable tool-confirmation execution rows. Configuration/model tests passed, and both code defaults and the example environment remain `OKF_ENABLED=false`, `OKF_SYNC_ENABLED=false`, `OKF_SHADOW_READS=false`, and `KNOWLEDGE_MODE=rag`.

### OKF-2 — Domain service and provenance

- [x] Implement typed contracts, normalization, policy, repository, and `OkfKnowledgeService`.
- [x] Implement atomic create/update/version/contested/retire behavior.
- [x] Implement owner-scoped provenance links and privacy-safe version cleanup.
- [x] Implement the shared lifecycle/privacy-barrier service before wiring any delete or exclusion endpoint.
- [x] Add concurrency/idempotency tests for duplicate and conflicting proposals.

**Gate: PASS (2026-09-29).** Typed request/result/proposal/evidence/outcome contracts, deterministic NFKC canonical mapping, JSON bounds and secret rejection, owner-scoped repository writes, immutable assertion-version appends, duplicate provenance handling, explicit supersession, contested claims, and retirement are implemented. Writes reload and lock an active memory under an enabled owner and an included owned session; proposals must exactly match its deterministic structured-field mapping. The lifecycle barrier synchronously removes unsupported current assertions/versions, cancels pending source upserts, advances the owner's memory generation, and enqueues content-free removal/purge jobs. Disposable PostgreSQL tests passed for concurrent conflicts, duplicate/older replay, owner isolation, project parent/child mapping, retirement, forget cleanup, session exclusion, and user purge. No delete/exclusion endpoint was wired in this phase.

### OKF-3 — Async synchronization

- [x] Add transactional job enqueue behind `OKF_SYNC_ENABLED`.
- [x] Implement lease/retry/dead-letter/recovery behavior in the dedicated worker.
- [x] Add deterministic structured-field mapping and reconciliation for create, supersede, forget, delete-all, session deletion/purge/exclusion, memory disablement, and re-enablement.
- [x] Add user memory-generation/version checks that prevent a claimed job from resurrecting data after a privacy transition.
- [x] Add the resumable backfill command but do not run it automatically.
- [x] Verify voice response completion does not await concept processing.

**Gate: PASS (2026-09-29).** The writer enqueues content-free source jobs in the source transaction only when both OKF flags are enabled. The standalone/in-process worker claims with `SKIP LOCKED`, uses expiring leases, bounded exponential retries, dead-letters exhausted/expired work, and commits concept changes with job completion atomically. Claim and source processing are separate transactions so crashes are recoverable. Generation-stamped jobs are revalidated while the owned source and user rows are locked; stale eligible upserts are rebased, while unavailable/excluded sources are cancelled. Supersede, forget, delete-all, memory disable/re-enable, session exclusion/deletion, and session purge are wired to the synchronous lifecycle barrier. The explicit `backend/scripts/okf_backfill.py` command pages by memory ID and only enqueues work; it is never started automatically. Concept mapping is called only by the worker, not by the voice gateway; the voice path performs at most the transactional enqueue. Disposable PostgreSQL migration and service/worker integration checks now pass; the backfill remains unrun.

Verification: the OKF-focused suite (including disposable PostgreSQL migration and service/worker integration with explicit opt-in flags) passed 48 tests on 2026-09-29. The backfill was not run.

### OKF-4 — Structured retrieval and evaluation

- [x] Implement deterministic query planning and bounded owner-scoped retrieval.
- [x] Cover parent/child project expansion, exact preferences, relationships, one active assertion, contested assertions, no-result, and degraded database cases.
- [x] Add an operator comparison harness for RAG vs OKF vs COMBINED.
- [x] Measure accuracy, provenance coverage, no-result correctness, and latency on the labeled corpus.

**Gate: IMPLEMENTED; ACCEPTANCE REVIEW PENDING (2026-09-30).** The 2026-09-29 labeled run remains RAG 13/16, OKF 11/16, COMBINED 13/16; its exact five OKF misses and three RAG/COMBINED misses were not retained case-by-case, so they cannot be responsibly reclassified. The no-result defect was corrected with query-anchor checks for type-scoped candidates; the frozen 14-case OKF-0 RAG baseline still passes with identical routes and corpus hash, and new regressions cover unrelated preferences, type-mismatched distractors, and valid matching facts. A read-only live PostgreSQL diagnostic on the two approved owners completed 48/48 shadow service reads at a process-local 300 ms deadline; however, both owners had no readable OKF concepts after prior cleanup, so every query returned no-result and provenance/accuracy are not meaningful labeled-cohort measurements. Same-process live retrieval percentiles were RAG 2.628/3.826/75.209 ms, OKF 1.809/2.842/9.653 ms, and sequential COMBINED estimate 4.705/6.294/84.892 ms (P50/P95/P99, n=48). Historical completed-upsert sync lag remains P50/P95/P99 1,382.568/1,466.486/1,468.182 ms (n=16). No thresholds have been approved; see the dated remediation record. OKF-4 is not PASS.

### OKF-5 — Knowledge selector and context builder

- [x] Add the engine interface/adapters, typed evaluation dispositions, and validated `rag|okf|combined` selector.
- [x] Add the bounded `KnowledgeContext` envelope and render separate untrusted evidence sections.
- [x] Preserve current RAG direct-answer, no-result, unavailable, conflict, evidence, prompt, and audit behavior in `rag` mode and when OKF is disabled.
- [x] Cover both router `off|shadow` legacy orchestration and router `canary|on` `MEMORY_QUERY` orchestration.
- [x] Add cancellation, conflict, partial failure, duplicate evidence, and prompt-injection tests.

**Gate: PASS (2026-09-30; focused scope).** OKF lifecycle fencing now uses additive `users.memory_generation`, leaving public `memory_version` at the intended 1 → 2 sequence for create → supersede instead of incrementing twice. The migration backfills existing generations from `memory_version`; the development DB is at `0019_okf_owner_memory_generation`. The frozen RAG baseline is unchanged, the RAG-default/selector/context/gateway test set passes, and the opt-in PostgreSQL migration, OKF service/worker, and memory lifecycle integrations pass. A safe-state backend restart loaded the current code with OKF and sync disabled, shadow off, an empty allowlist, and `KNOWLEDGE_MODE=rag`; `/health` and `/ready` passed. See `docs/20260929_1725_okf_blocker_remediation.md`.

### OKF-6 — Shadow and development cohort

- [x] Run OKF sync for a randomly generated disposable PostgreSQL fixture owner only; inspect concepts and active provenance before any retrieval comparison. The fixture test also verifies post-forget non-readability.
- [x] Add explicit `OKF_SHADOW_USER_IDS` owner allowlisting; configuration rejects shadow mode unless OKF is enabled and `KNOWLEDGE_MODE=rag` remains authoritative. Defaults remain off/empty.
- [x] Enable `OKF_SHADOW_READS=true` only for the isolated 2026-09-29 development acceptance run, restricted to the two approved disposable owner UUIDs; restore the local `.env` to safe values and restart afterward.
- [x] Prove via focused tests that shadow reads use independent sessions, honor timeout and non-blocking capacity skips, and are scheduled without awaiting on the response path.
- [x] Add content-free shadow metrics and an operator JSONL report (`python scripts/okf_shadow_report.py <structured-log.jsonl>`) for quality dispositions, set overlap, no-result agreement, sync lag, provenance coverage, and latency percentiles; forbidden content-bearing log fields are rejected.
- [ ] Complete live labeled quality/overlap/no-result/provenance review: the initial three normal voice shadow tasks timed out. Follow-up direct PostgreSQL service profiling completed 48 reads at a local-only 300 ms deadline with zero timeouts/errors, but the old synthetic corpus had been cleaned and all reads returned no-result (0 facts/provenance); this is not a replacement for normal-path cohort acceptance. The measured connection-acquisition tail explains why 75 ms was unreliable. Proposed trial timeout is 300 ms based on observed connection-acquisition and retrieval tails; keep the checked-in default at 75 ms and obtain owner approval before using any local cohort override. See the dated remediation record.
- [x] Exercise conflict and deletion behavior against labeled/disposable database fixtures; no policy tuning was made without live labeled evidence.

**Gate: IMPLEMENTATION COMPLETE; LIVE ACCEPTANCE PENDING (2026-09-30).** The prior live voice run had three timeouts; follow-up live PostgreSQL diagnostics recorded per-mode service timings and 48/48 completed reads under a process-local 300 ms timeout, but no concepts or provenance were available after cleanup. The real response-path cohort was not re-enabled in this remediation run, so non-blocking behavior rests on the prior normal voice observation plus current independent-session/non-await/capacity tests. Safe config is restored and backend health/readiness pass. OKF-6 remains pending until a labeled disposable cohort is resynchronized, successful normal-path shadow queries are collected, and quality/provenance/deletion metrics meet owner-approved thresholds. Existing router Phase 3/9 gates remain separate.

### OKF-7 — Controlled mode trials

- [ ] Trial `KNOWLEDGE_MODE=okf` on the disposable cohort.
- [ ] Trial `KNOWLEDGE_MODE=combined` and measure incremental value versus cost/latency.
- [ ] Run text-path acceptance first, then physical voice regression only when current router rollout prerequisites allow it.
- [ ] Verify rollback to `KNOWLEDGE_MODE=rag` and emergency `OKF_ENABLED=false` without data migration.

**Gate:** accepted comparison report, rollback drill, zero unsupported personal claims, zero cross-user leakage, zero deletion regressions, and no material voice-protocol regression.

**Status (2026-09-29): NOT ACCEPTED / LIVE TRIAL BLOCKED.** A separate frozen 14-case text-fixture replay is recorded at `docs/evidence/okf/okf7_offline_text_comparison_20260929.json`; it is explicitly offline (no database/provider calls) and does not satisfy either controlled mode trial. OKF-4 and OKF-6 remain acceptance-review pending, and Phase 9 router/physical-voice prerequisites remain open. No runtime mode was changed, no cohort data was written, no rollback drill was claimed, and no voice regression was run. See `docs/evidence/okf/okf7_controlled_mode_trial_20260929.md`.

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
| Schema | Empty upgrade/downgrade, existing-data upgrade, five-table constraints, owner-scoped FKs, assertion/current-version invariant, downgrade guard with live rows. |
| Mapping | Each structured-field allow-list rule, missing subject/predicate/object abstention, ineligible event/routine/summary, free-text-only abstention, secrets, oversized/deep JSON, Unicode normalization. |
| Versioning | Exact duplicate, equivalent assertion with new provenance, newer explicit supersession, older replay, multiple active contested assertions, restore, concurrent updates. |
| Provenance | One source to many concepts, many sources to one concept, missing/foreign/deleted source, policy-version audit. |
| Jobs | Duplicate delivery, lease expiry, retry, permanent failure, crash after write/before completion, two users concurrently, backfill resume. |
| Privacy | Account disable/re-enable, excluded session, session deletion, forget, delete-all, synchronous non-readability before worker completion, claimed-job race, source-only version cleanup, logs contain no content; future account-delete integration is tested when that workflow exists. |
| Retrieval | Exact project, preference, relationship, child expansion, conflict, no result, partial/degraded, bounded limits. |
| Modes | RAG parity, OKF-only, COMBINED dedupe, separate engine deadlines, one-engine failure, invalid configuration, master switch rollback. |
| Router | Canary/on retrieval only for `MEMORY_QUERY`; zero routed retrieval for general/actions/tools/control; off/shadow legacy parity; shadow has no user-visible effect. |
| Context | Character budget, source IDs, untrusted delimiters, conflict language, prompt injection, cancellation. |
| End to end | Source memory -> async job -> concept -> query -> context -> grounded answer; then update and forget the source. |

Do not run every web/API/mobile build for each phase. Use focused backend tests and migration checks first; run broader suites and physical/mobile validation only at their defined gates.

## 18. Proposed file touch points

Expected new files:

```text
backend/app/okf/*.py
backend/migrations/versions/0017_okf_configuration_schema.py
backend/migrations/versions/0018_okf_sync_generation.py
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
backend/app/memory/jobs.py
backend/app/memory/tool_tools.py
backend/app/api/memories.py
backend/app/api/sessions.py
backend/app/websocket/gateway.py
backend/app/llm/context.py
backend/app/api/routes.py              # required for mode-aware readiness
docker-compose.yml                     # required only for standalone worker deployment
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

1. Set `OKF_SYNC_ENABLED=false` to stop ordinary enqueue/worker processing; synchronous privacy barriers remain active.
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
- OKF and COMBINED results are source-grounded, bounded, owner-scoped, and deletion-correct;
- voice responses never wait for OKF synchronization;
- feature flags and rollback are proven;
- no subscription assumption exists in OKF storage or services;
- current Hybrid RAG and GraphRAG data remain intact;
- a dated work record lists every edited file, verification command, result, limitation, and remaining gate for each implementation phase.
