# Phase 6 — Hybrid RAG Current Implementation Verification & Audit

**Audit date:** 2026-09-15  
**Scope:** Current repository design and code, persisted test/evaluation evidence, and the available 2026-09-15 device trace. This is a verification report, not an implementation proposal.

## Audit checklist

- [x] Read the Phase 6 plan and compare its retrieval/context design with the implementation.
- [x] Inspect memory models, migration files, ownership constraints, read/write paths, lifecycle, tool and voice integration.
- [x] Inspect the tests, saved quality/latency evidence, and 2026-09-15 physical-device trace.
- [x] Attempt the focused backend tests and inspect local runtime/process readiness.
- [x] Record differences, unverified claims, and constraints relevant to later GraphRAG planning.
- [ ] Verify the live database revision and current service/provider health. No local app/database/Redis/Metro process was running, and test dependencies prevent the available migration CLI from loading; these facts cannot be established from this workstation state.

No application code, business behavior, database rows, or runtime configuration was changed. The only repository change accompanying this report is a .gitignore exception for this report itself.

## A. Executive verdict

**Phase 6 is partially verified.** The repository contains a real user-scoped memory subsystem with deterministic extraction, durable PostgreSQL jobs, structured/FTS/vector retrieval, reciprocal-rank fusion (RRF), reranking, bounded prompt context, voice integration, and memory tools. Saved 2026-09-09 evaluation artifacts show good retrieval quality and historical tests passed. The current checkout’s tests could not be rerun in this environment, the current database revision and active provider state are unknown, and no services were running for a live smoke test.

The code is a hybrid retrieval system, not a graph retrieval system. Entity and MemoryEntity currently provide per-memory subject labels and ownership-scoped links; they do not implement entity alias resolution or graph traversal. The plan’s proposed query composition, richer temporal handling, context contents, and some lifecycle semantics are not fully implemented.

The available 2026-09-15 device trace attributes most of the measured STT-final-to-HTTP-request gap to **turn persistence (about 1.82 s p50)**, followed by **memory/RAG decision and retrieval (about 0.56 s p50)**. The trace also records roughly 0.22 s p50 in confirmation routing for the sampled turns. These stages explain most of the observed 2.58 s p50 pre-request interval, subject to repeated calls, retries, and clock/analyzer limitations described in section K. This is evidence from that saved run, not proof of current live behavior.

## B. Current architecture

    Device audio / STT
      └─ final transcript
          └─ VoiceGateway final-turn path
              ├─ persist final user message in PostgreSQL
              ├─ enqueue extract_turn job (when memory writes are enabled/allowed)
              ├─ resolve confirmation/tool state
              ├─ memory retrieval when enabled and session is not excluded
              │   ├─ deterministic query-plan heuristics
              │   ├─ optional structured PostgreSQL search
              │   ├─ PostgreSQL full-text search on memory_items
              │   ├─ remote query embedding → PostgreSQL vector search on memory_chunks
              │   ├─ reciprocal-rank fusion
              │   └─ remote reranking
              ├─ pack bounded memory evidence into untrusted user-role context
              ├─ build voice LLM request and dispatch provider stream
              └─ process tool calls / stream assistant output / voice response

    Background memory worker
      └─ claim PostgreSQL job with FOR UPDATE SKIP LOCKED
          ├─ validate owner, final user message, user preference, and session exclusion
          ├─ deterministic explicit-memory extraction
          ├─ deduplicate / preference supersession / write memory and entity link
          └─ enqueue and later process embedding job

Memory extraction and embedding are asynchronous relative to the assistant response; the voice path does not wait for the worker to finish them. Final-message persistence and memory retrieval are on the response path. The retrieval implementation executes its DB and remote stages sequentially because it shares an AsyncSession and explicitly avoids concurrent DB operations.

## C. Current read path

Main path: backend/app/websocket/gateway.py, VoiceGateway._memory_context_for_transcript → backend/app/memory/retrieval.py, MemoryRetrievalService.retrieve → backend/app/memory/context.py, assemble_context → backend/app/llm/context.py, build_voice_llm_request → backend/app/llm/providers/openai_chat.py (including the NVIDIA-compatible streaming adapter).

1. Gateway skips retrieval when retrieval is disabled, the user has disabled memory, or the voice session is excluded. Otherwise it invokes retrieval for an eligible final transcript, including ordinary requests; it is not limited to explicit personal-memory questions.
2. build_memory_query_plan in backend/app/memory/types.py uses deterministic keyword heuristics. It does not call an LLM or generate SQL. It supports general/latest/oldest/time-range/preference/relationship/fact intent labels and a bounded result limit. Subject and predicate are not populated by the current parser. Temporal recognition is limited; “today” and “yesterday” are recognized, while general before/after/between parsing and robust event-time extraction are absent. Intent precedence can cause a combined request such as “latest preference” to lose the latest ordering intent.
3. structured_retrieve applies owner and active-status predicates, plus available type/subject/date predicates. Latest/oldest order by creation time rather than occurrence time. Date filtering falls back to creation time when occurrence dates are missing.
4. fts_retrieve queries MemoryItem.search_tsv on memory_items; it does not use the FTS vector on memory_chunks. It applies the base owner/active filters and ranks with PostgreSQL text rank.
5. dense_retrieve gets a query embedding from the remote embedding provider, searches MemoryChunk.embedding, joins using both memory ID and user ID, filters active memories, aggregates chunk distance per memory, and returns the nearest candidates. Current dense SQL does not apply the planner’s type/subject/date constraints.
6. Structured results (when applicable), FTS, and dense results are run sequentially. fuse_candidates collapses duplicate memory IDs and uses unweighted reciprocal-rank fusion with k=60; ties are stable by memory ID. The usual candidate cap is 30.
7. rerank_fused calls the configured remote reranker (default model BAAI/bge-reranker-v2-m3); the usual final cap is 8. Provider failure falls back to fused candidates. A successful partial score response can omit unscored candidates.
8. assemble_context includes candidate text only, in retrieval order, up to a default 12,000-character budget. It stops when the next item does not fit. It does not add recent conversation history, a user profile, or graph-neighbor context.
9. The voice LLM builder wraps memory as untrusted user-role evidence. The prompt has instructions to treat user and memory data as untrusted. A character ceiling is derived from context tokens; an oversized request is rejected rather than tokenized and truncated by a provider tokenizer.

The structured, FTS, and vector database queries are async SQLAlchemy operations. Query embedding and reranking use async HTTPX calls. PostgreSQL connection-pool wait is inside the measured DB operation; it is not emitted as its own wait interval. Memory-provider semaphore and HTTP connection-pool waits are likewise not independently measured.

## D. Current write path

Voice final-turn persistence and job enqueueing are in backend/app/websocket/gateway.py and backend/app persistence/repository services. When global memory writes are enabled, the user’s memory_enabled flag is true, and the session is not excluded, the final user message is persisted and an extract_turn job is enqueued. The assistant response does not await extraction or embedding.

backend/app/memory/jobs.py, MemoryJobWorker.run_once, claims jobs transactionally with SELECT … FOR UPDATE SKIP LOCKED, validates the job’s user/session/source message, requires a final user-role message, checks the user memory setting and session exclusion, then calls extract_explicit_candidates from backend/app/memory/extraction.py. Extraction is deterministic regex/rule logic, not an LLM extraction contract. It accepts explicit “remember/save” wording and a limited set of preference, fact, relationship, and routine forms; it rejects temporary/uncertain/task-like and some third-party statements. It is bounded to 2,000 content characters and validates source grounding and policy constraints.

backend/app/memory/writer.py checks configured confidence/salience thresholds, queries for an active dedupe key, writes the item and chunks, optionally creates a subject entity/link, and enqueues embed_memory. Dedupe normalizes NFKC, casefolding, and whitespace and hashes stable candidate fields. A partial unique index is the concurrent-write backstop. Preference candidates with matching subject and predicate supersede the existing active preference; other fact/project conflicts are not automatically reconciled. The old preference is marked superseded and linked from the new record, but valid_to is not filled. The caller/worker transaction commits the work.

The worker retries with backoff and a configured attempt cap of five. The PostgreSQL job table is the durable queue; Redis is not the memory-job queue. A historical worker artifact reports 18 jobs without loss/double/cross-user failures, but that is saved 2026-09-09 evidence rather than a rerun in this audit.

## E. Database reality

Static schema is in backend/app/models/resources.py, backend/app/models/auth.py, and migration 0007_phase6_memory_foundation.py.

| Table | Relevant fields and constraints |
|---|---|
| memory_items | user_id, content/metadata, type, subject/predicate/object, occurrence and validity timestamps, confidence/salience, source message/turn/session, source kind, supersedes_id, dedupe key, extraction version, status, computed simple-language search_tsv; user/status/type/date/source indexes, GIN FTS index, partial unique active (user_id, dedupe_key). Type/status/source/range checks. |
| memory_chunks | memory/user ownership, chunk index/content, 1024-dimension vector and embedding model/time, computed FTS vector, unique memory/chunk, composite memory+user cascading FK, HNSW cosine index, GIN FTS index. Current FTS retrieval does not query the chunk FTS index. |
| entities | user, free-text type, canonical and normalized names, metadata; unique (user_id, entity_type, normalized_name) and owner-scoped ID uniqueness. No alias table/alias array or constrained type vocabulary. |
| memory_entities | memory/entity/user composite key and optional relation; composite cascading ownership FKs to both records. This stores a memory-to-entity association, not an entity-to-entity edge. |
| memory_jobs | owner, type, source provenance, optional memory, idempotency key, status/attempts/lease/error/policy/model/timestamps; owner-scoped unique idempotency key and claim/status indexes. Allowed job types include extract_turn, embed_memory, reembed_memory, and purge_session. |
| messages / users | Finality and ownership are represented in application/model constraints. Message has is_final, source IDs are owner-scoped, and user has memory_enabled, timezone/locale, and memory_version. The database does not globally require is_final=true; the worker checks it. |

The repository migration chain is 0001→…→0007 Phase 6→0008/0009 Phase 7→0010→0011_device_aware_task_times; **static repository head is 0011_device_aware_task_times**. A saved September 9 validation note claiming current/head 0007 is stale relative to this checkout. **The live database head is unknown.** alembic heads/alembic current could not load in the current environment because the backend environment lacks pgvector; no DB connection or mutation was performed. Static migration files do not prove which migrations are applied to any remote database.

The HNSW index exists. A saved small-corpus production query plan reported explain_uses_hnsw=false; a separate 5,000-row isolated query selected HNSW. Neither proves that the current live corpus and current runtime query use HNSW.

## F. Current entity architecture

Entities are created by the writer only when an extraction candidate has a subject. The normalized name uses Unicode NFKC, casefolding, and whitespace normalization. The entity is owner-scoped and typed with the candidate’s subject type/name; the writer adds a MemoryEntity row whose relation is the candidate predicate.

There is no alias storage or alias-resolution pass, no canonicalization against known entities during extraction/retrieval, and no entity lookup in the structured/FTS/vector query path. Dense similarity may happen to retrieve a paraphrase or alias-like query, but that is not entity resolution. The writer associates at most one subject entity per candidate. Multiple memories may share that entity. Deleting a memory cascades its memory-entity link, but an entity row is not deleted when its last link disappears; orphan entities can remain. There are no entity-to-entity edges or graph traversal queries.
## G. Retrieval reality: plan, implementation, evidence, runtime

| Component | Planned | Implemented | Tested in saved evidence | Current runtime verified |
|---|---|---|---|---|
| Query planning | Structured intent and temporal/person constraints | Deterministic keyword heuristics; no LLM, aliases, or rich date parsing | Unit/evaluation cases include latest, oldest, preference, relationship, relative date | No |
| Structured retrieval | User-scoped structured facts | Optional PostgreSQL query; owner/active filters; latest by creation time | Historical focused tests and corpus evaluation | No |
| Lexical retrieval | Full-text memory/chunk retrieval | PostgreSQL FTS on memory_items.search_tsv | Historical tests/evaluation | No |
| Dense retrieval | Vector candidate search | Remote query embedding followed by owner-filtered chunk vector SQL | Historical tests/evaluation; saved HNSW evidence is mixed | No |
| Hybrid fusion | Combine structured, lexical, vector evidence | Sequential stages; unweighted RRF k=60, candidate limit 30 | Historical unit and evaluation evidence | No |
| Reranking | Rerank candidate set | Remote NVIDIA-compatible reranker, usually final 8; provider-failure RRF fallback | Historical provider/evaluation evidence | No |
| Context | Profile, recent history, task state, memory evidence | Memory text only, bounded by character budgets; untrusted role | Prompt/safety historical tests | No |
| Redis/cache | Fast state/cache expected by broader architecture | Redis is used for voice/session/confirmation state; no RAG result cache or memory-version cache | General Redis-backed voice evidence is historical | No |
| Latency | Per-stage retrieval/runtime breakdown | Several RAG spans exist; pool/provider wait and some context/query-plan pieces are not separated; analyzer has clock-selection issues | Saved 2026-09-15 device trace and earlier evidence | Not currently live |

Saved docs/evidence/phase6/phase6_retrieval_metrics.json reports a 25-case evaluation: hybrid Recall@5 1.00, reranked Recall@5 0.95, MRR 0.875, nDCG 0.8946, top-1 accuracy 0.80, and no recorded cross-user/deleted/superseded/no-result leakage or injection violation. This is historical evidence. It does not cover all listed limitations or prove the current runtime.

## H. Memory lifecycle and controls

- **Create:** automatic explicit extraction and manual/tool API paths write through the memory writer. Automatic write jobs are asynchronous. The extraction parser’s direct explicit “remember” path is not the same as a proposed tool write and does not wait for tool confirmation; confirm that this is intended product policy before treating it as a policy violation.
- **Deduplicate:** deterministic owner-scoped key, active-row lookup, plus partial unique index. Idempotent job keys protect job enqueueing.
- **Correction/supersession:** automatic supersession is implemented for preferences only. Other memory types can coexist with contradictions. valid_from/valid_to exist but are not generally maintained by writer/update paths.
- **Search:** owner-scoped structured/FTS/vector paths and owner-scoped tool/API queries. No entity graph or alias lookup.
- **Disable:** user setting cancels pending/retry jobs and workers check the setting. Retrieval returns no memory context. API create checks disabled state; update does not apply the same explicit setting check. Historical tests need rerun for the current checkout.
- **Session exclusion:** session metadata memory_excluded prevents current-session retrieval/enqueue and makes the worker skip pending extraction. Setting exclusion does not enqueue the available purge_session job, so already persisted memories from that session are not retroactively removed and may be retrieved later in other sessions. The saved physical exclusion artifact verifies no memory was written for its test session, not cleanup of prior memories.
- **Delete/forget:** owner-scoped tool/API deletes cascade chunks, links, and related jobs. Entity rows can remain orphaned. memory_version is bumped for some settings/delete-all routes, but not consistently for create/update/single-delete/tool writes; no memory cache currently consumes this counter.
- **Background jobs:** purge_session has a handler but no enqueue path was found; reembed_memory is an allowed type but the worker does not implement it and treats unsupported jobs as errors/retries. These are lifecycle gaps, not evidence of active job corruption.

## I. Ownership and security

Ownership is enforced in application queries and reinforced by composite user-scoped foreign keys for source messages/turns/sessions, memory chunks, and memory-entity links. Structured and FTS queries use the user-scoped base query; dense search joins chunk to memory with both memory and owner IDs and filters active status. Tool APIs obtain the user from authenticated server context; model/tool arguments do not select an arbitrary owner. Memory search is read-scoped; memory save/forget require write scope and confirmation/idempotency/rate-limit gates in the tool executor.

The supersedes_id foreign key itself is a single ID reference rather than a composite same-user reference. The writer’s lookup is user-scoped, but the database constraint alone does not enforce that the referenced row belongs to the same user. This should be a GraphRAG/lifecycle design constraint if supersession links are extended.

Stored memory is injected as untrusted user-role evidence, with system instructions to avoid treating it as trusted policy or secret-bearing content. Historical 8-case NVIDIA adversarial prompt evidence passed; it is not a current provider run. No credentials or raw user utterances are reproduced in this report.

## J. Failure and degradation behavior

| Failure/condition | Current behavior | Assessment |
|---|---|---|
| Memory disabled / session excluded | Retrieval skipped; worker checks before extraction | Fail closed for new session writes; exclusion does not purge old session rows |
| Embedding provider error | MemoryProviderError degrades to structured/FTS results | Search can continue without dense retrieval |
| Reranker provider error | Uses fused candidates | Graceful fallback; a successful partial response may omit unscored candidates |
| PostgreSQL retrieval error | Not converted to a MemoryProviderError fallback in the service path | Can fail the voice request; DB outage behavior is not equivalent to provider-outage behavior |
| No memory result | Empty context/tool result | Does not guarantee a special deterministic personal-memory unavailable/not-found response; the model still answers from the remaining context |
| Worker source/user/session validation fails | Job is skipped/rejected according to worker path | Prevents invalid source writes; saved worker evidence is historical |
| Worker transient error | Retry with backoff and attempt limit | Durable queue behavior exists; rerun/current DB state unavailable |
| Voice cancellation/stale response | Async awaits propagate cancellation; gateway/LLM cancellation guards suppress stale output; semaphore release is in finally | General cancellation tests exist, but this audit could not execute them and does not establish every memory-stage cancellation case |

## K. Current latency baseline and measurement limits

Two evidence sets must be kept separate:

1. **Saved evaluation artifact (2026-09-09):** 25 retrieval cases, total retrieval min 543.508 ms, mean 690.963 ms, p50 637.826 ms, p95 1,225.169 ms, p99 1,323.093 ms, max 1,326.633 ms. This is a retrieval evaluation, not a current device-session measurement. The artifact labels one stage hnsw_vector_stage, while its small-corpus explain evidence says HNSW was not used.
2. **Physical-device trace (2026-09-15):** scratch/latency_trace_device_test_20260915.jsonl has 8 distinct turns with RAG spans and 9 retrieval calls (a repeated call exists). The trace provides useful backend monotonic intervals, but small-sample tail percentiles are unstable, machine/warm-cold state is absent, and the capture does not establish current live process state.

Selected per-turn statistics from the 8-turn trace (milliseconds; p95/p99 from eight samples are descriptive only):

| Interval/stage | n | p50 | p95 | max | Notes |
|---|---:|---:|---:|---:|---|
| STT final → orchestration start | 8 | 0.5 | 0.6 | 0.7 | Small; same backend trace clock |
| Final-turn persistence | 8 | 1,817.3 | 1,885.8 | 1,897.9 | Largest measured pre-request stage |
| Session/context loading | 8 | 3.6 | — | 11.2 | Small relative to persistence |
| Confirmation routing | 8 | 221.6 | — | 228.9 | Present in captured route; not a RAG operation |
| Memory decision / RAG retrieval | 8 | 564.2 | 1,625.0 | 1,811.4 | Sequential retrieval total; includes remote work |
| Tool routing | 8 | 0.9 | — | 2.1 | Small for these spans |
| Prompt build | 8 | 1.4 | — | 2.7 | Does not include earlier retrieval/context work |
| Token budget | 8 | 0.4 | — | 0.6 | Small |
| Provider prepare | 8 | 0.9 | — | 4.4 | Provider preparation span |
| LLM semaphore wait | 8 | 0.4 | — | 2.3 | Does not explain the recurring multi-second delay |
| LLM HTTP start → connection acquired | 8 | 121.0 | — | 1,136.0 | Includes HTTP/client pool/network connection behavior as observed; not a dedicated pool-only measure |
| STT final → first HTTP request start | 8 | 2,582.3 | 3,318.0 | 3,387.4 | Best current estimate of pre-dispatch gap in this capture |

RAG substage medians: query embedding 201.9 ms; vector SQL 8.9 ms; FTS total 6.9 ms; structured query (3 applicable turns) 6.1 ms; RRF 0.10 ms; reranking 266.2 ms. Embedding and vector SQL roll up into the vector-search total and must not be added again to that total. The slow remote embedding/reranker calls dominate the retrieval stage; PostgreSQL search itself is much smaller in this sample.

**Interpretation:** For the captured run, the recurring pre-request interval is real in same-clock stt_final_received → http_request_started events. Persistence is the largest contributor (about 1.82 s p50), RAG is next (about 0.56 s p50), and confirmation routing adds roughly 0.22 s in this sample. Together with small context/tool/prompt/provider stages these are consistent with the observed approximately 2.58 s p50. The measurements do not justify attributing the whole gap to RAG. Database-stage timings include any PostgreSQL pool wait; provider timings do not separately distinguish semaphore, HTTP pool, DNS, TCP/TLS, or server wait beyond emitted events.

**Clock/analyzer limitation:** backend/scripts/analyze_latency.py currently selects llm_first_token for provider TTFT instead of using the provider’s http_request_started → first_content_token interval. The saved trace includes same high-resolution monotonic provider events (http_request_started, connection, response headers/stream, and first_content_token), but also legacy events with different timestamp construction while sharing a broad clock_domain label. On the saved trace, the analyzer reports zero provider TTFT / zero STT-final-to-token or negative/unreasonable residuals for some turns. Those analyzer fields are invalid; do not cite them as provider latency. Multi-request tool/retry turns also mean there may not be one HTTP interval per turn. The first user-visible provider content token should be measured from the relevant request’s http_request_started to its first_content_token, selecting the actual request attempt.

LatencyTracer.emit performs synchronous file open/write/flush and takes a threading.Lock from async-path code. Each trace row records the previous trace-writer cost, but this audit did not establish aggregate overhead or remove it. This is synchronous blocking I/O in the async request path. There is no separate Redis-memory access in the RAG path and no memory Redis cache. The 2026-09-15 capture has no reliable device-side speech-end-to-backend monotonic bridge, so speech-end-to-first-token/audio cannot be recomputed from backend monotonic values alone.
## L. Test and evaluation evidence

**Current focused test attempt:**

    python -m pytest tests/test_phase6_memory.py tests/test_phase6_foundation.py tests/test_phase6_acceptance_manifest.py tests/test_phase6_memory_integration.py tests/test_llm_context.py tests/test_llm_tool_loop.py -q

Collection failed before the tests ran: the selected Python 3.14 environment lacks httpx and pydantic_settings, Starlette’s test client requires httpx, and pytest reports an unknown asyncio_mode option. Backend project metadata requires Python >=3.12,<3.13; no matching interpreter was available via py -0p. The migration CLI also failed to import because pgvector is missing. No dependencies were installed, no migration was applied, and no test results are claimed for this audit run.

Historical evidence in the repository includes a focused Phase 6 run of 13 passed / 2 skipped, Phase 6 integration of 25 passed, and standard backend suite of 190 passed / 25 skipped; these were recorded in September 2026 validation notes, not rerun here. The historical repository notes also mention four Ruff UP038 failures in the then-current full Ruff run. Saved retrieval quality, worker, physical exclusion, and prompt-injection reports are referenced above. They should be treated as prior evidence, not present-day green checks.

Local process/port inspection found no local backend, Metro, device bridge, PostgreSQL, or Redis process/listener on the checked ports. Remote hosts in environment configuration were not contacted. Thus “current runtime verified” is **No**.

## M. Plan-versus-code differences

1. The design describes structured, lexical, and vector retrieval with rank fusion; code has those sources, but runs them sequentially rather than concurrently.
2. Planned FTS coverage includes chunks; code’s active lexical query uses memory_items.search_tsv, leaving the chunk GIN FTS index unused by this path.
3. Planned context includes profile essentials, recent conversation, task state, and memory. Current memory context contains retrieved memory text only; recent history/profile/task context is not assembled in assemble_context.
4. The design discusses temporal/event facts. Current parsing is limited, subject/predicate query-plan slots are not populated, occurrence times are not extracted, and latest/oldest sort by creation time.
5. Entity labels are not a graph, aliases, or retrieval-time canonicalizer. The saved alias-like case succeeding does not establish alias resolution.
6. A saved validation note says migration head 0007; the checked-in repository has subsequent migrations through 0011. Live DB head is unknown.
7. The code supports a purge_session job but no enqueue path was found; session exclusion prevents new retrieval/write but does not retroactively erase previously written session memories. reembed_memory is accepted by schema but unsupported by the worker.
8. Memory outage handling is asymmetric: remote embedding/reranker provider errors degrade; PostgreSQL retrieval errors can escape the voice path.
9. Current latency aggregation does not reliably select provider first-content-token events and collapses/reconciles repeated events imperfectly. Do not use its zero or negative TTFT/residual fields as proof.
10. Environment defaults and local .env values are not the same as process-effective runtime configuration. No process is live to confirm current flags, DB head, or provider health.

The root .env currently sets MEMORY_RETRIEVAL_MODE=inject and MEMORY_WRITE_ENABLED=true. These are file values only; with no running local server, the process-effective settings were not verified. Secret values are intentionally omitted.

## N. Existing components reusable for later GraphRAG work

- Owner-scoped memory_items, memory_chunks, entities, and memory_entities foundations and composite ownership FKs.
- Durable idempotent job queue, lease/claim/retry behavior, source provenance, policy versioning, and worker lifecycle.
- Deterministic memory query plan types, retrieval candidate model, structured/FTS/dense source adapters, RRF implementation, and reranker boundary.
- User memory disable flag, session exclusion checks, auth-scoped memory API/tool operations, and untrusted-memory prompt boundary.
- Existing latency tracing conventions and Phase 6 evaluation corpus/artifact format, after clock/attempt selection and live-vs-historical labeling are corrected in a separately authorized follow-up.

These are foundations only. Reuse does not mean graph nodes/edges, aliases, traversal, graph ranking, lifecycle, or correctness guarantees already exist.

## O. Constraints before implementation planning becomes an implementation

- Treat static schema head and live database head as different facts; inspect the connected DB before migration-dependent work.
- Keep every new graph read/write owner-scoped in both query predicates and relational constraints; explicitly constrain cross-owner edge and supersession cases.
- Decide alias/canonical-entity resolution, entity merge, edge provenance/confidence, temporal validity, correction, deletion, and orphan cleanup before relying on entity identity.
- Ensure disable/exclusion/delete semantics cover queued jobs, already-written nodes/edges, derived chunks/embeddings, caches, and any future graph indexes.
- Preserve voice cancellation, confirmation, tool authorization, and memory-as-untrusted behavior. Do not put graph writes on the response path unless latency and consistency trade-offs are explicitly accepted.
- Benchmark a live retrieval query plan on representative corpus sizes and measure pool/provider wait separately; index existence alone is not query-plan evidence.
- Fix provider TTFT selection and use a single monotonic source per interval before comparing a new graph path to this baseline.
- Re-run the relevant test/evaluation suite under the repository’s supported Python/dependency environment and capture live DB/provider/runtime versions before accepting implementation claims.

## Final state

PHASE 6 CURRENT STATE
=====================

Migration head:
Repository files: 0011_device_aware_task_times
Live database: UNVERIFIED

Hybrid RAG status:
PARTIAL — implemented sources and historical evaluation exist; current tests/runtime could not be verified here.

Entity model:
PARTIAL — owner-scoped subject entities and memory links; no aliases, graph edges, or traversal.

Retrieval quality:
PASS in saved 2026-09-09 25-case artifact; not rerun in this audit.

Memory lifecycle:
PARTIAL — dedupe, preference supersession, disable/delete controls exist; retroactive session purge and re-embedding have gaps.

Ownership/security:
PASS by scoped code paths and static relational constraints, with a noted non-composite supersession FK; current runtime tests unavailable.

Latency instrumentation:
PARTIAL — useful same-clock backend spans exist; provider TTFT analyzer selection and blocking trace I/O limit confidence.

Current Hybrid RAG P50/P95/P99/max:
637.826 / 1,225.169 / 1,323.093 / 1,326.633 ms in the saved 25-case evaluation (not current live runtime).

Safe to begin GraphRAG planning:
YES — planning only, using this audit as the baseline. Do not treat this as approval or evidence to begin implementation; first close the live DB/runtime and supported-test-environment verification gates above.
