# Phase 6 — Long-Term Memory + Hybrid RAG Implementation Plan

**Status:** Implementation-ready plan  
**Prepared:** 2026-06-07 11:58 IST  
**Source of truth reviewed:** `implementation.md`, especially sections 13, 14, 18, 24, 33–37, 39–45, and the Phase 6 checklist  
**Scope of this document:** Backend memory persistence, extraction, hybrid retrieval, LLM/voice integration, user controls, operations, and acceptance. This document does not implement Phase 6.

## 1. Outcome and Definition of Done

Phase 6 is complete when an authenticated user can explicitly or automatically save useful long-term memories, retrieve them through structured temporal lookup plus hybrid dense/keyword search, use the retrieved evidence in a voice response, inspect/correct/delete the memories, disable memory, and prove that deleted, disabled, superseded, or another user's data can never enter retrieval or an LLM prompt.

The mandatory architecture is:

```text
final user message
  -> durable message + extraction job
  -> validated memory candidates
  -> exact dedupe + conflict/supersession policy
  -> memory item + chunks + BGE-M3 embeddings

new user query
  -> memory intent/structured query plan
  -> structured lookup first for dates, events, facts, and relationships
  -> BGE-M3 vector search + PostgreSQL FTS in parallel when needed
  -> dedupe + reciprocal rank fusion (RRF)
  -> BAAI/bge-reranker-v2-m3
  -> bounded, provenance-bearing memory context
  -> provider-neutral LLM request
  -> evidence-grounded answer
```

## 2. Current Implementation Audit

### 2.1 What already exists

| Area | Current implementation | Reuse in Phase 6 |
|---|---|---|
| PostgreSQL/pgvector | `pgvector/pgvector:pg16`, init SQL, Alembic `0001`, Python `pgvector` dependency, and an infrastructure integration check | Keep; add real vector columns and HNSW index in the Phase 6 migration |
| Memory persistence | Phase 2 `memories` table with `id`, `user_id`, `content`, JSON metadata, status, and timestamps | Migrate in place to the richer `memory_items` model and preserve existing rows |
| Memory API | Authenticated create/list/get/substring-search/update/delete endpoints | Replace the placeholder search and enrich CRUD without breaking existing owned records |
| Ownership | Every memory API query derives `user_id` from the access token; cross-user access returns 404 and is audited | Preserve and extend to items, chunks, entities, jobs, search, tools, cache keys, and context assembly |
| Conversation persistence | `voice_sessions` and `conversation_turns`; final transcript, STT data, LLM response text, tool state, and metrics are stored inside turn JSON metadata | Introduce first-class final `messages` records; retain turn metadata for operational metrics |
| LLM path | Provider-neutral adapters, bounded streaming service, typed events, cancellation, retries, tool registry, Pydantic validation, confirmation flow, and durable idempotency | Reuse for extraction contracts and memory tools; inject retrieval through a deterministic context assembler before provider dispatch |
| Voice integration | Final STT text is committed before LLM generation; the gateway owns `session_id`, `turn_id`, and `response_id` | Add retrieval after STT final and before LLM request; enqueue extraction without blocking the voice response |
| Redis | Available as ephemeral coordination/cache infrastructure | Use only for short-lived query cache/version keys; PostgreSQL remains authoritative |

### 2.2 Material gaps

- The current `memories` table is a CRUD placeholder, not the `memory_items`/`memory_chunks`/entity design from `implementation.md`.
- No first-class `messages` table exists. Final user and assistant text live in `conversation_turns.metadata`, which is unsuitable for provenance, recent-context queries, and `source_message_id` foreign keys.
- `users` has no `memory_enabled`, timezone, or locale fields.
- Current delete physically removes one placeholder row, but there is no coordinated deletion of chunks, cache, jobs, derived memories, or conversation-derived provenance.
- Current `/memories/search` is `ILIKE '%query%'`; there is no FTS, embedding, vector query, fusion, or reranking.
- No embedding or reranker configuration, lifecycle client, readiness check, container, model pin, dimension check, or failure mapping exists.
- No structured query planner exists for temporal/event/fact/preference/relationship questions.
- The LLM request currently contains only the current transcript. It has no recent message window or separated memory evidence block.
- No automatic extraction, salience policy, deduplication, conflict resolution, supersession, background job, or embedding backfill exists.
- The default tool registry contains `create_task`, not `memory_search`, `memory_save`, or `memory_forget`.
- There is no memory settings API, delete-all workflow, conversation exclusion workflow, memory-specific audit trail, evaluation corpus, or retrieval telemetry.
- `/ready` reports infrastructure and LLM only; it cannot distinguish memory read readiness, write-worker readiness, or degraded model services.

### 2.3 Constraints inherited from earlier phases

- Never trust a client-supplied `user_id`; identity always comes from `AuthPrincipal`.
- Preserve response cancellation and reject stale output using `response_id`.
- Keep retrieved text as untrusted data, separate from system/tool policy.
- Do not delay ordinary voice responses on background extraction/embedding.
- For a direct memory question, do not invent an answer when retrieval is unavailable or has no evidence.
- PostgreSQL is the durable source of truth; Redis is optional acceleration only.
- No raw audio or partial STT is added to long-term memory.

## 3. Authoritative Phase 6 Design Decisions

1. **Migrate, do not fork, the Phase 2 memory resource.** Rename `memories` to `memory_items`, add the Phase 6 fields, backfill defaults, then update the ORM/API. This preserves existing user data and avoids two competing memory stores.
2. **Add first-class final messages.** Persist final user, assistant, and tool messages in a `messages` table. Do not migrate operational STT/LLM metrics out of turn metadata.
3. **Structured retrieval is first-class.** Temporal events, stable facts, preferences, relationships, and exact entities use typed SQL queries before semantic retrieval. The LLM phrases answers but never supplies retrieved dates/facts.
4. **Hybrid means three candidate sources.** Structured candidates, BGE-M3 dense candidates, and PostgreSQL `simple`-configuration FTS candidates are merged by rank. Raw cosine and FTS scores are never directly added.
5. **Model inference is consumed through remote HTTP providers, matching the existing STT pattern.** This repository owns lifecycle-managed STT, embedding, and reranker clients; the separately operated inference server owns the models, devices, key file, host, port, and batch limits. In deployment, `STT_API_URL`, `EMBEDDING_API_URL`, and `RERANK_API_URL` point to the inference server through Cloudflare. The existing `STT_API_KEY` is the one shared bearer key for all three authenticated inference endpoints. No BGE model is loaded inside the FastAPI application process.
6. **Memory writes are asynchronous by default.** Final message persistence and a durable PostgreSQL job are committed together. A worker uses `FOR UPDATE SKIP LOCKED`, bounded retries, and idempotency. Explicit `memory_save` may write synchronously after validation.
7. **Only explicit user evidence becomes factual memory.** Assistant text, tool proposals, ASR partials, model guesses, and low-confidence inference cannot become factual personal memory.
8. **Supersession is historical; deletion is real deletion.** Changed preferences/facts set the old row to `superseded` and create a linked replacement. User deletion hard-deletes the item and cascades its chunks/links; audit logs retain identifiers/action metadata, not deleted content.
9. **Disabling memory is reversible and immediate.** It stops retrieval, automatic extraction, manual save, and new embedding jobs. Inspection, deletion, delete-all, and re-enable remain available. Existing memories are retained until the user deletes them.
10. **Conversation exclusion is provenance-aware.** Excluding a session prevents new extraction and removes automatically derived memories from that session; manual memories are not silently deleted unless explicitly selected.
11. **Correctness precedes caching.** Add Redis query caching only after retrieval and invalidation tests pass. Cache keys include `user_id`, normalized query hash, retrieval-policy version, and a per-user memory version.
12. **Phase 6 does not depend on a particular LLM provider.** Extraction and query planning use provider-neutral typed contracts and fail safely if the configured provider cannot satisfy them.

## 4. Target Data Model and Migration Strategy

Implement one additive Alembic revision, expected to be `0006_phase6_memory_rag.py`. If review shows the migration is too large for safe rollback, split it into `0006` foundation and `0007` search/jobs before application code is merged.

### 4.1 User memory controls

Add to `users`:

- `memory_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `timezone TEXT NOT NULL DEFAULT 'UTC'`
- `locale TEXT NOT NULL DEFAULT 'en'`
- `memory_version BIGINT NOT NULL DEFAULT 0` for cache invalidation

Timezone precedence during a voice turn is verified session IANA timezone, then stored user timezone, then `VOICE_DEFAULT_TIMEZONE`. Persist a newly verified session timezone to the user profile only through an explicit, validated profile update policy.

### 4.2 Final messages

Create `messages`:

- `id`, `turn_id`, `user_id`, `role`, `content`, `content_json`, `is_final`, `model`, `sequence_no`, `created_at`
- Add unique `(conversation_turns.id, conversation_turns.user_id)` so PostgreSQL can enforce the composite message ownership foreign key
- Composite ownership foreign key through `(turn_id, user_id)`
- Unique `(turn_id, role, sequence_no)` for idempotent replay
- Index `(user_id, created_at DESC)` and `(turn_id, sequence_no)`
- Role check for `user`, `assistant`, `tool`, `system`

Persist only final user transcripts and confirmed assistant/tool results. The gateway writes the final user message in the same transaction that commits STT turn metadata, and writes the assistant message in the same transaction that commits the terminal LLM metadata.

### 4.3 Memory items

Rename the existing `memories` table to `memory_items`, preserve IDs, and add:

- `memory_type`: `fact`, `preference`, `event`, `relationship`, `routine`, `project`, or `summary`
- `subject`, `predicate`, `object_json`
- `occurred_start_at`, `occurred_end_at`, `valid_from`, `valid_to`
- `confidence` and `salience`, each checked within `[0, 1]`
- `source_message_id`, `source_turn_id`, and `source_session_id` for deletion/exclusion provenance
- `source_kind`: `automatic`, `explicit_tool`, `manual_api`, or `legacy`
- `supersedes_id`
- `dedupe_key` and `extraction_policy_version`
- Status check for `active`, `superseded`, and `deleted`; user/API deletion still physically removes content
- Generated `search_tsv = to_tsvector('simple', coalesce(content, ''))`
- Indexes for `(user_id, status, memory_type)`, `(user_id, occurred_start_at DESC)`, structured predicate/entity fields, source provenance, and GIN FTS
- Unique `(id, user_id)` and a partial unique exact-dedupe index such as `(user_id, dedupe_key) WHERE status = 'active'`

Source provenance foreign keys must include `user_id` where the parent supports it. This makes it impossible for a memory row to claim another user's message, turn, or session as its source even if application validation regresses.

Legacy rows receive `memory_type='summary'`, `source_kind='legacy'`, `confidence=1`, `salience=0.5`, and keep their content/metadata. Backfill is deterministic and does not call a model during migration.

### 4.4 Chunks and embeddings

Create `memory_chunks`:

- `id`, `memory_id`, `user_id`, `chunk_no`, `content`, `token_count`
- `embedding VECTOR(1024)`
- `embedding_model`, `embedded_at`; optionally store an operator-supplied server deployment identifier when that becomes part of the contract
- Generated `search_tsv` with `simple` configuration
- Unique `(memory_id, chunk_no)`
- Composite foreign key `(memory_id, user_id)` to prevent corrupt cross-owner links
- Index `(user_id, memory_id)`, GIN on `search_tsv`, and HNSW with `vector_cosine_ops`

All vector SQL must include `user_id` and `status='active'` through a join to `memory_items`. Validate with `EXPLAIN (ANALYZE, BUFFERS)` that the installed pgvector version/operator/index combination is actually used. If tenant filtering causes poor approximate recall at small corpus sizes, keep an exact-search fallback until measured HNSW recall passes.

### 4.5 Entities and links

Create `entities` and `memory_entities` for exact names, aliases, locations, people, and relationships:

- Entity uniqueness: `(user_id, entity_type, normalized_name)`
- Entity fields: canonical name, normalized name, aliases/metadata
- Link fields: `memory_id`, `entity_id`, `user_id`, relation
- Composite ownership constraints on both sides

Use deterministic Unicode normalization, whitespace/case folding, and stored aliases. Do not introduce a graph database in Phase 6.

### 4.6 Durable memory jobs

Create `memory_jobs`:

- Job types: `extract_turn`, `embed_memory`, `reembed_memory`, `purge_session`
- `id`, `user_id`, source IDs, optional `memory_id`, status, attempts, available/locked/completed timestamps, last typed error, policy/model version, timestamps
- Unique idempotency key for each source/type/version
- Status check: `pending`, `running`, `retry_wait`, `completed`, `dead`, `cancelled`
- Claim index on `(status, available_at)`

Do not store secrets, full prompts, or model responses in job errors. On user disable/delete/exclusion, cancel applicable pending jobs transactionally.

### 4.7 Migration verification and rollback

- Test upgrade from the current `0005` schema with real legacy memory rows.
- Assert row IDs, owners, content, and timestamps survive the rename/backfill.
- Assert constraints reject invalid scores/statuses and cross-owner chunk/entity links.
- Test downgrade only in an isolated database. Production rollback disables feature flags and rolls application code back without immediately dropping populated Phase 6 tables.
- Record pre/post row counts and orphan checks in migration evidence.

## 5. Application Modules and Contracts

### 5.1 Proposed backend layout

```text
backend/app/memory/
  __init__.py
  types.py                 # query plans, candidates, ranked hits, context DTOs
  repository.py            # ownership-scoped SQL only
  structured.py            # temporal/fact/preference/relationship queries
  keyword.py               # PostgreSQL FTS
  vector.py                # pgvector candidate retrieval
  rrf.py                   # deterministic rank fusion
  reranker.py              # reranker client and score mapping
  embeddings.py            # embedding client and dimension validation
  retrieval.py             # end-to-end retrieval orchestration
  context.py               # safe bounded LLM evidence formatting
  extraction.py            # typed extraction prompt/contract
  policy.py                # salience, trusted-source, conflict rules
  dedup.py                 # normalization and exact/semantic duplicate checks
  writer.py                # transactional insert/supersede/delete/version bump
  jobs.py                  # durable enqueue/claim/retry/dead-letter operations
  worker.py                # extraction and embedding worker loop
  errors.py                # typed, safe failure classes
  service.py               # facade used by API, tools, and voice gateway
```

The embedding and reranker implementations run on the existing separately operated inference server, not in this repository. The client design mirrors the current STT split:

```text
STTService       -> RemoteTranscriptionEngine -> STT_API_URL
EmbeddingService -> RemoteEmbeddingProvider   -> EMBEDDING_API_URL
RerankerService  -> RemoteRerankerProvider    -> RERANK_API_URL
```

Keep the application provider-neutral by normalizing the remote server responses behind those interfaces. The confirmed remote inference-server contract is below.

#### Authentication and health

`GET /health` requires no authentication. Do not attach `STT_API_KEY` to the health request. Every inference request uses:

```http
Authorization: Bearer <STT_API_KEY>
```

#### Voice-to-text contract

```text
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

| Parameter | Required | Contract |
|---|---|---|
| `file` | Yes | `mp3`, `wav`, `m4a`, `ogg`, `flac`, or `webm`; maximum 25 MB |
| `language` | No | Language code such as `en` |

The response contains `text`, `language`, `language_probability`, `duration`, `processing_ms`, `rtf`, `device`, and `model`. The current application adapter creates WAV at commit time. The server prefers MP3 over the tunnel, but changing the existing voice upload encoding is a separately tested STT optimization and is not required to implement Phase 6.

#### Embeddings contract

```text
POST /v1/embeddings
Content-Type: application/json

{
  "input": ["first text", "second text"]
}
```

- `input` is required and is either one string or a list of 1–64 strings.
- The response contains `embeddings`, `dim`, `processing_ms`, `device`, and `model`.
- The server model is `BAAI/bge-m3`; the client does not send a model parameter.
- Every returned vector is L2-normalized and must have `dim=1024`.
- The response vector count and ordering must exactly match the submitted inputs.
- Store/search with pgvector cosine distance; do not normalize again unless a contract test proves the server response violates the stated contract.

#### Rerank contract

```text
POST /v1/rerank
Content-Type: application/json

{
  "query": "search text",
  "documents": ["candidate one", "candidate two"],
  "top_k": 2
}
```

- `query` is required.
- `documents` is required and contains 1–64 strings.
- `top_k` is optional and, when present, must be within `1..len(documents)`.
- Results are sorted by descending score and contain `index`, `score`, and `document`.
- The response also contains `processing_ms`, `device`, and `model`.
- The server model is `BAAI/bge-reranker-v2-m3`; the client does not send a model parameter.
- Validate each index is unique and in range, each returned document matches the submitted document at that index, every score is finite and within `0..1`, and ordering is high-to-low.

Enforce request item/character/byte limits, timeouts, concurrency limits, HTTPS outside loopback/private development, no redirects, response-size validation, exact cardinality, finite numeric values, and embedding dimension `1024`. Model selection remains server-owned; the application validates the returned model and never accepts a request-time model ID from a user or mobile client. Treat returned `processing_ms`, `device`, and `model` as safe structured telemetry, but never log submitted input, query, documents, transcript, or returned document text.

The embeddings endpoint is mandatory. The listed STT and rerank endpoints alone cannot provide BGE-M3 vectors for pgvector semantic search.

### 5.2 Configuration

During implementation, update this repository's `.env.example` and local ignored `.env` contract with the following Phase 6 settings. The only new required secrets-related behavior is reusing the existing `STT_API_KEY`; do not add a second key.

```dotenv
# The real deployment values use the Cloudflare HTTPS origin supplied by the operator.
# Loopback HTTP is allowed only for local development/testing.
STT_API_URL=https://<cloudflare-host>/v1/audio/transcriptions
EMBEDDING_API_URL=https://<cloudflare-host>/v1/embeddings
RERANK_API_URL=https://<cloudflare-host>/v1/rerank

# Existing key; the Phase 6 clients reuse it for embeddings and reranking.
# Never commit the real stt_live_... value.
STT_API_KEY=stt_live_replace_with_real_secret

# Required safe rollout switches. Keep both off initially.
MEMORY_RETRIEVAL_MODE=off
MEMORY_WRITE_ENABLED=false

# Optional application-side HTTP bounds; these have safe code defaults.
EMBEDDING_API_CONNECT_TIMEOUT_SECONDS=10
EMBEDDING_API_TIMEOUT_SECONDS=30
EMBEDDING_API_MAX_RESPONSE_BYTES=8388608
EMBEDDING_API_MAX_BATCH_SIZE=64
RERANK_API_CONNECT_TIMEOUT_SECONDS=10
RERANK_API_TIMEOUT_SECONDS=30
RERANK_API_MAX_RESPONSE_BYTES=2097152
RERANK_API_MAX_DOCS=64
```

Equivalent local-development values are:

```dotenv
STT_API_URL=http://127.0.0.1:8000/v1/audio/transcriptions
EMBEDDING_API_URL=http://127.0.0.1:8000/v1/embeddings
RERANK_API_URL=http://127.0.0.1:8000/v1/rerank
STT_API_KEY=stt_live_replace_with_real_secret
MEMORY_RETRIEVAL_MODE=off
MEMORY_WRITE_ENABLED=false
```

For an already configured installation, the only mandatory Phase 6 `.env` additions are `EMBEDDING_API_URL`, `RERANK_API_URL`, `MEMORY_RETRIEVAL_MODE=off`, and `MEMORY_WRITE_ENABLED=false`. Keep the existing `STT_API_KEY`; embedding and reranker clients must read that same secret and send it as `Authorization: Bearer ...`. There is no `INFERENCE_API_KEY`, `EMBEDDING_API_KEY`, or `RERANK_API_KEY` in this contract.

The following values shown by the operator are **inference-server runtime settings**, not application-client credentials, and must stay on the separately operated model server:

```dotenv
STT_MODEL=large-v3-turbo
STT_DEVICE=auto
STT_COMPUTE_TYPE=auto
STT_KEYS_FILE=keys.json
STT_MAX_UPLOAD_MB=25
STT_HOST=127.0.0.1
STT_PORT=8000
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_DEVICE=auto
RERANK_MAX_DOCS=64
```

The remote inference server must also be configured to serve BGE-M3 embeddings with its server-owned equivalent of `EMBEDDING_MODEL=BAAI/bge-m3`, an automatic/selected device, and a 64-input maximum. Exact server variable names belong to that server's contract and are not duplicated into this repository.

In addition, add validated application settings for:

- Global read/write switches and retrieval mode: `off`, `shadow`, or `inject`
- Exact embedding/rerank endpoint URLs, reuse of the shared `STT_API_KEY`, expected returned models, timeouts, batch/document limits, maximum inputs, and concurrency
- Vector/FTS candidate counts, RRF `k`, source weights, final context count and character/token budget
- Structured-confidence threshold and minimum retrieval threshold calibrated by evaluation
- Worker poll interval, batch size, lease timeout, max attempts, and retry bounds
- Extraction prompt/policy version, minimum confidence/salience, and chunk limits
- Query-cache TTL; default disabled until invalidation tests pass

Configuration validation must reject missing endpoint/key combinations, credentials embedded in URLs, redirects, query strings/fragments, non-HTTPS public URLs in production, paths other than the expected exact endpoint, invalid bounds, an embedding request above 64 inputs, a rerank request above 64 documents, or an embedding dimension other than `1024`. Local `http://127.0.0.1:8000/...` is accepted only in development/test, as with the current STT adapter.

### 5.3 Lifecycle and readiness

Create one remote embedding client and one remote reranker client during FastAPI lifespan, reuse their connection pools, and close them on shutdown, matching the current STT lifecycle. Call unauthenticated `/health` without the key, then use a bounded authenticated contract probe to prove the expected BGE-M3 model/dimension and reranker response. Do not silently fall back to another provider or model. Readiness reports these separately:

- Memory read can be `ready`, `degraded` (FTS/structured only), or `disabled`.
- Automatic memory write can be `ready`, `degraded` (jobs queue but model unavailable), or `disabled`.
- API process readiness should not fail solely because optional automatic extraction is temporarily unavailable, but injected semantic retrieval must not be reported ready when its required service is down.

## 6. End-to-End Read Path

### 6.1 Query planning

For every final user message, produce a bounded `MemoryQueryPlan` containing:

- `intent`: `none`, `structured`, `hybrid`, or `structured_plus_hybrid`
- Whether the question is memory-critical
- Memory types, subject, predicate, normalized entities/aliases
- Time direction/range: latest, oldest, before/after/between
- Query text for semantic and keyword search
- User timezone/current time used to resolve relative dates

Use deterministic rules for explicit patterns such as latest/last/first/oldest, known predicates, and exact entity aliases. A provider-neutral typed planner may assist ambiguous cases, but validation, ownership, result bounds, and SQL construction remain server-owned. Planner failure falls back to bounded hybrid search, never arbitrary model-authored SQL.

### 6.2 Retrieval order

1. Check global flags and the user's `memory_enabled` in the same ownership scope.
2. Normalize the query and resolve timezone/current time.
3. Execute the structured query first when the plan contains structured fields.
4. If structured confidence is sufficient, keep it as authoritative evidence. Run hybrid retrieval only when supporting context is useful or structured evidence is insufficient.
5. Run query embedding/vector retrieval and FTS retrieval concurrently, each returning at most the configured candidate count (initially 30).
6. Join every chunk to an active owned memory item. Exclude deleted/superseded records before ranking.
7. Collapse duplicate chunks to parent memory IDs and merge candidate ranks with deterministic RRF. Start with `k=60` and equal dense/keyword weights; tune only through the versioned evaluation corpus.
8. Send the top fused candidates (initially at most 30) to `BAAI/bge-reranker-v2-m3` and retain the configured final set (initially at most 8).
9. Preserve structured candidates regardless of reranker score when they passed the structured confidence policy.
10. Pack evidence into the memory context budget by authority, relevance, recency, confidence, and salience. Include memory ID/type/dates/source metadata internally, but expose only user-appropriate evidence.

### 6.3 LLM context integration

Replace the current single-transcript request builder with an async deterministic context assembler:

```text
system policy (trusted)
current time + verified user timezone (trusted metadata)
bounded recent final messages (untrusted conversation data)
structured memory evidence (untrusted data with typed dates/provenance)
reranked memory evidence (untrusted data with IDs/scores omitted from prose)
active tool/confirmation state (trusted state)
current user message (untrusted data)
```

- Retrieved content is serialized into a dedicated data section/message and never concatenated into system policy.
- Reserve output and tool-result capacity before adding memories.
- Deterministic truncation drops lowest-ranked memories first, then older recent messages; it never truncates system/tool policy or the current request.
- Record included memory IDs and policy/model versions in safe turn metadata for debugging/deletion propagation, without logging memory text.
- For direct memory questions, instruct the LLM to answer only from supplied evidence and to say it could not find the information when evidence is absent.
- If structured evidence provides a date/fact, the response must use that typed value. Add a post-generation grounding check for direct temporal answers; on mismatch, replace with a deterministic safe answer or return a typed grounding failure.

### 6.4 Degradation behavior

| Condition | Ordinary conversation | Direct memory question |
|---|---|---|
| Memory disabled by user | Continue without memory | State that memory is disabled; offer settings guidance |
| Embedding down, DB/FTS up | Use structured + FTS and mark degraded | Answer only if those sources provide sufficient evidence |
| Reranker down | Use bounded RRF order and mark degraded | Answer only above the calibrated fallback threshold |
| PostgreSQL/memory repository down | Continue with current/recent in-memory turn data when safe | State that memory lookup is unavailable; do not guess |
| No result | Continue normally | Explicitly state no saved memory was found |
| Request cancelled/superseded | Cancel model calls where possible and discard late results | Emit nothing for the stale response |

## 7. End-to-End Write Path

### 7.1 Durable trigger

1. Commit the final user `messages` row.
2. If global writes and user memory are enabled, the session is not excluded, and the message is an eligible final user message, insert one `extract_turn` job in the same transaction.
3. Complete the voice response without waiting for extraction.
4. The worker atomically claims jobs with a lease and `SKIP LOCKED`.

### 7.2 Extraction contract

Use a versioned extraction instruction and a forced typed output/tool schema. Each candidate contains:

- Memory type, subject, predicate, typed object
- Human-readable standalone content
- Occurrence/validity dates with precision/uncertainty metadata
- Confidence, salience, and explicit supporting source span
- Normalized entity candidates and aliases
- Proposed conflict identity key

Reject a candidate when it is unsupported by the final user text, is only an assistant inference, contains a secret class that policy forbids, has invalid dates/scores/types, duplicates filler, or falls below the configured thresholds. Store validation reason metrics, not rejected personal text.

### 7.3 Deduplication and conflict policy

- Compute a stable `dedupe_key` from normalized type/subject/predicate/object/time bucket/content as appropriate.
- Use the partial unique index plus a transaction to make exact dedupe race-safe.
- Repeated events with different occurrence windows coexist; identical event/time/entity candidates dedupe.
- Single-current-value facts/preferences/routines use a conflict identity. A genuine change sets the old item `superseded`, closes `valid_to`, inserts the new active item with `supersedes_id`, and increments the user's memory version atomically.
- Relationships accumulate only when compatible; contradictory relationship state follows an explicit predicate policy rather than generic semantic similarity.
- Semantic similarity may flag a candidate for merge/review but cannot silently overwrite a structured fact by itself.
- Manual corrections always create a new revision/supersession link so provenance remains inspectable.

### 7.4 Chunking and embedding

- Structured short memories normally produce one self-contained chunk.
- Longer project/summary memories use deterministic bounded chunks with small overlap and no empty chunks.
- Store chunk text before embedding, then enqueue/claim an idempotent embedding job.
- Batch embedding requests within configured size/character bounds.
- Atomically write vectors only when cardinality, finite-value, dimension, L2-normalization tolerance, and returned-model checks pass.
- A memory becomes searchable by structured/FTS immediately; semantic availability is tracked separately and can lag.
- Re-embedding creates/replaces vectors for a selected model/server deployment through idempotent jobs and never blocks reads.

### 7.5 Retry and poison-job behavior

- Retry timeouts, connection errors, 429, overload, and selected 5xx with bounded exponential backoff and jitter.
- Do not retry validation, unsupported contract, wrong dimension, or forbidden-source failures without a policy/model version change.
- Expired worker leases can be reclaimed.
- After max attempts, move the job to `dead`, expose counts in health/metrics, and provide a controlled admin retry command that does not accept arbitrary user IDs or text.

## 8. User APIs and Memory Tools

### 8.1 API contract

Evolve the existing authenticated `/memories` routes:

- `GET /memories` — cursor pagination and filters for type/status/date/source; active by default
- `POST /memories` — explicit manual memory with validated typed fields; ownership server-derived
- `GET /memories/{id}` — full owned, user-safe representation
- `PATCH /memories/{id}` — correction that creates a replacement/supersession record
- `DELETE /memories/{id}` — hard delete item/chunks/links and invalidate cache
- `POST /memories/search` — authenticated structured/hybrid search for product UI/debug use; return safe result explanations, not raw vectors
- `DELETE /memories` — delete all personal memory through a deliberate confirmation/re-auth flow
- `GET /memory/settings` and `PATCH /memory/settings` — inspect/toggle `memory_enabled`
- `PUT /sessions/{id}/memory-exclusion` — exclude/include an owned session; exclusion cancels jobs and removes automatically derived session memories

Retain compatibility for existing list/create/get/delete clients where practical. Deprecate the `ILIKE` GET search only after callers move to the typed POST search contract.

Every list/search endpoint has a hard maximum page/candidate size. Cross-user misses remain indistinguishable 404s and are audit logged. API responses never expose embeddings, internal prompts, raw ranker scores by default, worker errors, or another user's identifiers.

### 8.2 Tool registry

Add server-owned Pydantic tools:

- `memory_search` — read-only, bounded, authenticated; useful for targeted follow-up lookup
- `memory_save` — only for an explicit user instruction to remember; validates the source and writes synchronously/idempotently
- `memory_forget` — resolves candidates first and requires confirmation for destructive execution, especially ambiguous, multiple, or bulk matches

Add scopes such as `memory:read`, `memory:write`, and `memory:delete`; remove the gateway's current assumption that only `tasks:write` is relevant. Keep tool-call idempotency keyed by authenticated user, turn, tool name, and tool-call ID. A model-provided `user_id`, status, confidence override, raw SQL, or arbitrary filter is rejected by schema.

## 9. Security, Privacy, and Retention Controls

- Apply `user_id` at every repository method and SQL branch, including HNSW, FTS, structured joins, entities, job claims, APIs, tools, and cache keys.
- Add composite ownership constraints so application bugs cannot link one user's chunk/entity/message to another user's memory.
- Consider PostgreSQL row-level security as defense in depth after repository-level isolation tests pass; do not use it as a substitute for explicit filters.
- Treat memory text, query text, extracted candidates, and retrieved context as sensitive. Exclude them from standard logs, metrics labels, traces, and raw provider-error reporting.
- Allow audit logs to record actor, action, target ID, counts, request ID, and outcome only.
- Define forbidden automatic-memory classes before enabling writes: credentials/secrets, payment/auth data, incidental third-party sensitive data, assistant speculation, and raw audio/partial transcripts.
- Define transcript, memory, backup, and tombstone retention before production. Delete-all must document backup deletion timing honestly.
- Session/account deletion must remove or anonymize all messages, memory items/chunks/entities/jobs, and invalidate Redis memory keys.
- Keep the inference server bound to its intended interface, expose it only through the approved Cloudflare route, and prevent configured endpoint URLs from becoming request-time SSRF inputs. Never log the Cloudflare URL with credentials or the shared bearer key.
- Include prompt-injection memories in the adversarial corpus; retrieved text can never change system policy or tool authorization.

## 10. Observability

Add safe monotonic timing and counters for:

- Query plan type and memory-critical classification
- Structured, dense, FTS, fused, reranked, and final candidate counts
- Embedding, SQL branches, fusion, rerank, context packing, and total retrieval latency
- Cache hit/miss/invalidation
- Extraction jobs queued/claimed/completed/retried/dead/cancelled and lease age
- Candidates proposed/accepted/rejected/deduped/superseded by reason code
- Embedding backlog, returned model, batch size, and dimension/normalization failures
- Retrieval mode, degraded dependency, no-result, and grounding mismatch counts
- Memory saves/corrections/deletions/delete-all/exclusions without memory content labels

Persist on each turn: `retrieval_started_at`, `retrieval_completed_at`, status, policy versions, candidate counts, included memory IDs, and latency. Never persist raw hidden extraction prompts or model reasoning.

## 11. Test and Evaluation Plan

### 11.1 Unit tests

- Memory/query/candidate schemas and invalid boundary values
- Unicode/entity normalization and deterministic dedupe keys
- Latest, oldest, before/after/range temporal plan generation
- Exact duplicate, repeated event, preference change, relationship conflict, and correction policies
- Chunk boundaries, overlap, limits, and empty/oversize input
- RRF determinism, tie-breaking, source weighting, duplicate chunk collapse, and structured-result preservation
- Context budgeting, ordering, data/policy separation, and prompt-injection strings
- Memory-disabled and conversation-excluded decisions
- Retry classification, lease expiration, dead-letter behavior, cancellation, and cache key/version logic

### 11.2 Model-client contract tests

Use in-memory HTTP transports for:

- Unauthenticated `/health`, including proof that the authorization header is omitted
- One-string and 1–64-string embedding `input`, exact `embeddings` ordering/cardinality, `dim=1024`, L2 normalization, `processing_ms`, `device`, expected `model`, empty/oversized input, wrong dimension, NaN/Infinity, oversized response, timeout, 401/403/429/5xx, malformed JSON, shared-key redaction, and cancellation
- Rerank `query`, 1–64 `documents`, optional `top_k`, descending 0–1 scores, unique/in-range indexes, exact returned-document matching, `processing_ms`, `device`, expected `model`, invalid `top_k`, non-finite scores, timeout, failure fallback, shared-key redaction, and cancellation
- Typed extraction success, malformed tool/JSON output, unsupported claim, invalid date, low confidence, prompt injection, and provider unavailability

### 11.3 PostgreSQL integration tests

- Upgrade from `0005`, legacy memory preservation, all indexes/extensions, and downgrade in a disposable database
- Structured latest/oldest/range/fact/preference/relationship queries
- FTS exact name, rare token, phrase, punctuation, and no-result cases
- Vector cosine ordering and HNSW/exact recall comparison on a seeded corpus
- Concurrent exact-dedupe inserts and atomic supersession
- Worker `SKIP LOCKED`, retry, lease reclaim, idempotent replay, and deletion race
- Hard delete cascade, delete-all, account delete, session exclusion, and re-embedding
- Two-user adversarial matrix for every read/write/search/tool/job/link path

Integration tests must explicitly fail or skip with a clear reason when PostgreSQL/pgvector, Redis, embedding, or reranker dependencies are absent; a skip is not acceptance evidence.

### 11.4 Gateway/LLM end-to-end tests

- Final STT -> final message -> retrieval -> context assembler -> provider request -> grounded text event
- Structured Mumbai/latest-visit example with the date taken from the row
- Hybrid semantic paraphrase and exact-name FTS retrieval
- No result, disabled memory, unavailable retrieval, reranker fallback, and cancellation before/during retrieval
- Late embedding/reranker responses cannot reach a cancelled `response_id`
- Final user turn enqueues exactly one extraction job; worker replay creates no duplicate memory
- Explicit save/search/forget tool authorization, validation, confirmation, idempotency, and honest final response
- Deleted/superseded/cross-user memory IDs never appear in provider requests or persisted included-ID metadata

### 11.5 Versioned acceptance corpus

Create sanitized fixtures covering, at minimum, the gate in `implementation.md`:

```text
latest event
oldest event
preference change
person relationship
exact-name lookup
semantic paraphrase
conflicting memory
deleted memory
cross-user isolation
no-result query
```

Also cover multilingual/paraphrased queries, aliases such as Mumbai/Bombay, relative dates/timezones, repeated events, negation, uncertain statements, explicit remember/forget, memory-disabled mode, conversation exclusion, malicious stored instructions, and model-service degradation.

### 11.6 Mandatory acceptance thresholds

- Structured deterministic cases: 100% correct result and ordering.
- Cross-user items/chunks/entities/jobs returned or sent to a provider: **0**.
- Deleted, superseded, excluded, or disabled memories returned: **0**.
- Duplicate active memories under concurrent replay: **0** for exact duplicates.
- Unsupported automatic factual memories in the curated extraction corpus: **0**.
- Direct no-result/unavailable questions answered with fabricated memory: **0**.
- Migration legacy row/owner/content loss: **0**.
- HNSW recall@K, hybrid recall@K, MRR/nDCG, extraction precision/recall, and end-to-end retrieval P50/P95/P99 must be measured on a versioned corpus and target hardware. Numeric release thresholds beyond the hard safety gates must be agreed and written into the fixture manifest before `inject` rollout; do not invent them after seeing results.
- Record returned embedding/reranker model names, the inference-server release/deployment identifier supplied by its operator, hardware, corpus version, warm/cold state, concurrency, and resource usage with every acceptance run. The current API does not return a model revision, so the application must not fabricate one.

## 12. Staged Implementation TODO

Each stage ends with focused tests; do not default to building unrelated mobile/web components.

### Stage 0 — Policy and baseline

- [ ] Approve automatic-memory allow/deny categories, transcript/memory/backup retention, delete-all semantics, and confirmation rules.
- [ ] Freeze sanitized evaluation corpus v1 and numeric quality/latency thresholds for target hardware.
- [ ] Capture current `0005` schema/data counts and existing memory API contract.
- [ ] Add Phase 6 feature flags defaulting to `off`.

**Exit:** Privacy decisions and acceptance manifest are reviewed; no automatic write can occur yet.

### Stage 1 — Schema and repositories

- [ ] Add first-class messages and idempotent gateway persistence.
- [ ] Migrate/backfill `memories` to `memory_items`.
- [ ] Add chunks, entities, links, jobs, user settings/version, provenance, constraints, FTS, and HNSW.
- [ ] Implement ownership-required repositories and migration/integration tests.

**Exit:** Legacy data is preserved and cross-user constraints/queries fail closed.

### Stage 2 — Remote embedding and reranker providers

- [ ] Confirm the remote inference server exposes authenticated `/v1/embeddings` and `/v1/rerank` contracts through the supplied Cloudflare origin.
- [ ] Reuse the existing `STT_API_KEY` as the single bearer key for STT, embeddings, and reranking; do not introduce duplicate secret settings.
- [ ] Build bounded remote BGE-M3 embedding and `BAAI/bge-reranker-v2-m3` provider clients.
- [ ] Validate the expected returned models and add an unauthenticated health check, authenticated contract probes, a 1024-dimension/L2-normalization self-test, and in-memory HTTP contract tests.
- [ ] Add lifecycle, configuration, cancellation, safe error mapping, and readiness reporting without local model loading.

**Exit:** Reproducible authenticated remote inference through the Cloudflare URLs passes contract, cancellation, load, and resource measurements without exposing the shared key.

### Stage 3 — Retrieval core

- [ ] Implement typed query plan and entity normalization.
- [ ] Implement structured temporal/fact/preference/relationship repositories.
- [ ] Implement dense and FTS candidate queries.
- [ ] Implement duplicate collapse, RRF, reranking, thresholds, and provenance-bearing result DTOs.
- [ ] Add degradation rules and optional versioned Redis cache after correctness passes.

**Exit:** Offline/integration retrieval corpus meets the agreed structured, recall, ranking, isolation, and latency thresholds.

### Stage 4 — Memory write pipeline

- [ ] Implement durable job enqueue/claim/lease/retry/dead-letter flow.
- [ ] Implement versioned typed extraction and trusted-source validation.
- [ ] Implement exact dedupe, conflict identity rules, supersession, correction, chunking, and embedding.
- [ ] Implement re-embedding/backfill and deletion/exclusion race handling.

**Exit:** A final user message can safely produce one idempotent, searchable memory without blocking the voice turn.

### Stage 5 — LLM and voice integration

- [ ] Build the async deterministic context assembler with hard budgets.
- [ ] Retrieve after final STT and before the first LLM request.
- [ ] Separate memory evidence from trusted instructions and persist safe retrieval metadata.
- [ ] Preserve cancellation/stale-response rules and add grounding validation for direct structured answers.
- [ ] Enqueue extraction at the durable final-message boundary.

**Exit:** Voice E2E tests prove grounded answer, no-result honesty, degradation, and cancellation.

### Stage 6 — APIs, tools, and user controls

- [ ] Upgrade memory CRUD/search with pagination, filters, correction, and safe DTOs.
- [ ] Add enable/disable, delete-all, and conversation exclusion workflows.
- [ ] Add `memory_search`, `memory_save`, and confirmed `memory_forget` tools with scopes/idempotency.
- [ ] Add audit events and cache/job invalidation for every mutation.

**Exit:** Users can inspect, correct, delete, disable, and exclude; all cross-user/tool tests pass.

### Stage 7 — Evaluation, performance, and rollout

- [ ] Run full unit/contract/integration/E2E/adversarial suites.
- [ ] Run live BGE-M3/reranker evaluation on target hardware and record resource/latency evidence.
- [ ] Run `shadow` retrieval without prompt injection; compare results and inspect only sanitized diagnostics.
- [ ] Backfill legacy/manual items through idempotent embedding jobs.
- [ ] Canary `inject` for test users, then staged production cohorts.
- [ ] Publish Phase 6 acceptance report and operations/runbook documentation.

**Exit:** Every hard safety gate and agreed quality/performance threshold passes with reproducible evidence.

## 13. Suggested Change/Review Slices

Keep reviews small and reversible:

1. Configuration, feature flags, and policy DTOs
2. Messages + memory schema migration and legacy backfill
3. Ownership-scoped repositories and data-model tests
4. Shared inference authentication plus remote embedding provider/client contract tests
5. Remote reranker provider/client contract tests
6. Structured + FTS + vector retrieval, RRF, and reranking
7. Durable extraction/embedding worker, dedupe, and conflict handling
8. Context assembler and voice/LLM integration
9. APIs, tools, deletion/settings/exclusion workflows
10. Evaluation scripts, observability, shadow/canary rollout, and acceptance report

Do not merge a slice that exposes incomplete memory to the LLM. Feature flags stay `off` until the associated stage exit is satisfied.

## 14. Expected Existing Files to Modify During Implementation

This is a forecast, not a change made by this planning task:

- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/api/routes.py`
- `backend/app/api/memories.py`
- `backend/app/api/sessions.py`
- `backend/app/api/dependencies.py`
- `backend/app/models/auth.py`
- `backend/app/models/voice.py`
- `backend/app/models/resources.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/resources.py`
- `backend/app/schemas/__init__.py`
- `backend/app/services/ownership.py`
- `backend/app/services/voice_persistence.py`
- `backend/app/websocket/gateway.py`
- `backend/app/llm/context.py`
- `backend/app/llm/tool_loop.py`
- `backend/app/llm/types.py`
- `backend/pyproject.toml`
- `.env.example`
- `docker-compose.yml`
- `README.md` and `backend/README.md`

New files are expected under `backend/app/memory/`, `backend/migrations/versions/`, `backend/tests/`, and `scripts/` for evaluation/backfill operations. BGE embedding/reranker model-server files are outside this repository; this project adds only provider clients and contracts for that server.

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Legacy memory loss during rename/backfill | Additive migration, fixture with real legacy rows, pre/post counts, production feature rollback rather than destructive downgrade |
| Cross-user vector leakage | Mandatory ownership arguments, composite FKs, joined active/owner filter, adversarial two-user tests, provider-payload inspection |
| Extraction stores hallucinations | User-final-only evidence, source spans, strict typed validation, high-precision thresholds, rejected-reason metrics, shadow write evaluation |
| Preference conflicts return both values | Predicate-specific conflict identity, transactional supersession, active-only query |
| HNSW filtering harms recall | Measure against exact search, tune candidate count/index settings, retain exact fallback for small per-user corpora |
| Model outage increases voice latency | Parallel bounded calls, short deadlines, structured/FTS fallback, async writes, explicit direct-memory degradation |
| Memory prompt injection | Separate data channel/section, no policy concatenation, adversarial corpus, tool authorization outside model |
| Delete races with worker/backfill | Row locks/version checks, cancelled jobs, physical cascade, user memory-version invalidation, post-delete provider-payload test |
| Model upgrade silently changes ranking | Pin the model/server release on the inference server, record returned models plus the operator's deployment identifier, use versioned re-embedding, and run the same corpus before rollout |
| Context growth regresses LLM latency | Hard per-section budgets, top-N bounds, deterministic truncation, P95/P99 instrumentation |

## 16. Final Phase 6 Gate

Phase 6 is accepted only when all of the following are true:

```text
legacy Phase 2 memories preserved by migration
first-class final messages and provenance available
structured latest/oldest/date/fact/preference/relationship queries PASS
BGE-M3 VECTOR(1024) retrieval and PostgreSQL FTS PASS
RRF + bge-reranker-v2-m3 evaluation thresholds PASS
automatic extraction precision/recall threshold PASS
exact dedupe and conflict/supersession PASS
memory CRUD/correction/delete-all/settings/session exclusion PASS
memory_search/save/forget authorization and idempotency PASS
deleted/superseded/disabled/excluded retrievals = 0
cross-user retrievals/provider payload leaks = 0
direct no-result/unavailable hallucinations = 0
retrieved content cannot alter system/tool policy
voice cancellation and stale-result suppression PASS
target-hardware latency/resource evidence recorded
shadow and canary rollout PASS
retention/deletion/runbook/acceptance documentation complete
```

Until this predicate passes, keep retrieval injection and automatic memory writes disabled in production.
