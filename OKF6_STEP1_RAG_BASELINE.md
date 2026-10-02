# OKF-6 Step 1: RAG Baseline

**Overall result: PASS**

The physical Android voice request reached the backend, RAG retrieved the expected saved memory as its top final candidate, and the assistant response matched that memory. The previous contents of this report described an earlier preflight; this version records the completed test after the user reported changing the configuration and restarting the backend and app.

## Test configuration

| Setting | Required | Observed |
|---|---|---|
| `KNOWLEDGE_MODE` | `rag` | `rag` |
| `OKF_ENABLED` | `false` | `false` |
| `OKF_SYNC_ENABLED` | `false` | `false` |
| `OKF_SHADOW_READS` | `false` | `false` |
| `ROUTER_MODE` | `off` | `off` |
| `ROUTER_COHORT_PERCENT` | `0` | `0` |

The selected `.env` values and `Settings()` values matched. `.env` was last written at **2026-10-01 12:44:53 PDT**. Backend worker processes **9628** and **16296** started at **12:54:57 PDT**, after that write. The test trace also shows the RAG pipeline executing on the test turn. No environment values other than the six requested, non-secret settings are included here.

Readiness was checked again after the physical turn:

- `GET /health`: **HTTP 200**, `{"status":"ok"}`.
- `GET /ready`: **HTTP 200**, `status=ready`; PostgreSQL and Redis were `ok`; LLM and TTS were `ready`; memory embedding and reranker dependencies were `ok`.
- The readiness payload reported `llm.live_verified=false` while still reporting the LLM dependency as `ready`. The tested turn subsequently has a completed LLM response in its persisted record and trace.

The existing report had recorded an earlier configuration-gate failure. That is historical and is superseded by the matching configuration snapshot, later backend worker start times, current readiness checks, and completed test evidence below.

## Test details

- **Test date/time:** 2026-10-01, approximately **13:32 PDT** (**20:32 UTC**). Backend turn start: `2026-10-01T20:32:05.920Z`; turn completion: approximately `20:32:45Z`.
- **Test account:** `c9aced07-?-448b36` (redacted).
- **Android device:** OPPO CPH2527.
- **Exact spoken question requested:** ?What does Project Orion use for long-term memory storage??
- **Persisted STT transcript:** ?What does Project Orion use for long-term memory storage?? repeated three times in the same turn.
- **Test memory:** active fact `a8441991-cd10-473f-a975-2383e7e6f98c`: ?Project Orion uses PostgreSQL with PG vector for long-term memory storage.?
- **User-reported spoken answer:** ?postrsql with pvector?. The persisted assistant response is ?Project Orion uses PostgreSQL with pgvector for long-term memory storage.?

## Observed results

| Check | Result | Evidence |
|---|---|---|
| Backend health | PASS | Post-test `GET /health` returned HTTP 200 and `{"status":"ok"}`. |
| Backend readiness | PASS | Post-test `GET /ready` returned HTTP 200 and `status=ready`; PostgreSQL, Redis, LLM, TTS, embedding, and reranker dependencies reported ready/ok. LLM `live_verified` was false in the readiness payload. |
| Voice request received | PASS | Trace has `gateway/turn_started`, `gateway/first_pcm_received`, and `gateway/turn_commit_received`; the matching turn and user message were persisted. |
| STT completed | PASS | STT trace completed with an HTTP 200 response and `stt_finalize_completed`; DB STT metadata records 29,280 ms audio and about 1,697 ms inference. Persisted transcript contains the requested question three times. |
| Router disabled | PASS | Effective `.env` and `Settings()` values were `ROUTER_MODE=off` and `ROUTER_COHORT_PERCENT=0`; backend workers started after the configuration file write. The target turn proceeds through the RAG pipeline and has no router execution event. |
| RAG mode selected | PASS | Effective `KNOWLEDGE_MODE=rag`; the target turn has `rag/memory_context_pipeline_started` and completed RAG stage events. |
| RAG retrieval executed | PASS | Target-turn trace records full-text search, embedding, vector search, reciprocal-rank fusion, reranking, relevance boundary, and final-candidate stages. |
| RAG evidence retrieved | PASS | The final candidate list contained 6 items. Full-text search returned 1 candidate; vector search returned 6. These are stage counts, not a claim that all six were included in the prompt. |
| Evidence matches memory | PASS | The saved active memory ID `a8441991-cd10-473f-a975-2383e7e6f98c` appears in the full-text candidate list and as final rank 1, score `0.999423`, from `dense` and `fts` sources. Its database content matches the test fact. |
| Evidence passed to LLM | PASS | `prompt/prompt_build_completed` for the same turn reports `memory_context_characters=579` and `status=completed`, followed by the LLM request and completion. The raw serialized prompt was not logged, so this confirms non-empty memory context construction and its use in the completed LLM turn, not the exact prompt wording. |
| Assistant response delivered | PASS | The backend trace records successful first assistant text send, completed TTS generation/response, and `gateway/turn_complete`. The database contains the assistant response shown above, and the user confirmed hearing it. |
| Answer supported by memory | PASS | The saved fact says PostgreSQL with PG vector; the assistant answered PostgreSQL with pgvector. The retrieved fact is the final rank-1 candidate. |
| Errors or timeouts | PASS | Target turn `error_count=0`; RAG final-candidate `provider_error=null`; STT, LLM, and TTS completed; no target-turn error, timeout, cancellation, or exception event was found in the trace. |

## Failure analysis

No confirmed failure occurred in the target turn. The only observed anomaly is that the persisted STT transcript repeats the requested question three times. The transcript still contains the intended question, and the RAG trace shows the expected memory at final rank 1. The available evidence does not identify why the transcript repeated, so no cause is assigned.

## Evidence and next action

- **Primary trace:** `logs/latency_trace.jsonl` (JSONL timestamps are UTC). Filter by session, turn, or response ID below. This is the active per-turn trace used for the RAG, prompt, STT, LLM, TTS, and gateway evidence.
- **Database records:** `voice_sessions`, `conversation_turns`, and `messages` for the test turn; `memory_items` for the saved fact. Inspection was read-only.
- **Health/readiness evidence:** live responses from `http://127.0.0.1:8000/health` and `/ready` during final evidence collection.
- **Voice session ID:** `87fce024-48f7-412c-86d1-bf3fa5efb864`.
- **Turn ID:** `8c178c5b-8319-4a42-96a9-ebe05e020544`.
- **Response ID:** `8b1a73b0-634e-4109-a8bf-1131dffa15cc`.
- **LLM provider request ID:** `req_918ec29b9f214f668881c7beb0c75666`.
- **Retrieved memory ID:** `a8441991-cd10-473f-a975-2383e7e6f98c`.
- **Evidence limits:** the raw prompt body is not in the trace; prompt context length is. No separate Android client log artifact was used; delivery is supported by backend websocket/TTS completion records and the user's report of the audible response. No secrets are included.

Step 1 passed. The project is ready to proceed to **Step 2: enable OKF while keeping `KNOWLEDGE_MODE=rag` and the router disabled**. No environment settings were changed during this evidence collection.
