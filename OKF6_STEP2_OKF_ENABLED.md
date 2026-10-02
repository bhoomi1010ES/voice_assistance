# OKF-6 Step 2 — OKF Enabled While Retaining RAG

**Overall result: PASS**

The physical Android voice test completed on the same account and device as Step 1. With **OKF_ENABLED=true** and **KNOWLEDGE_MODE=rag**, the RAG pipeline retrieved the expected active Orion memory as its top final candidate, passed non-empty memory context into the completed LLM turn, and produced an answer supported by that memory. The user confirmed hearing “postresql with pgvector.”

## Completion checklist

- [x] Verified the requested effective settings and backend readiness without exposing secrets.
- [x] Completed the physical Android voice test with the same account, device, and question as Step 1; no text was injected into the backend.
- [x] Inspected the target-turn trace and persisted records read-only.
- [x] Compared retrieval and response evidence with OKF6_STEP1_RAG_BASELINE.md.
- [x] Recorded the result and separated later unrelated voice-session errors from the target test.

## Configuration and readiness

The .env values and Settings() snapshot agreed. The running backend process started after .env was last written. Runtime trace for the tested turn shows the RAG pipeline executing.

| Setting | Required | Observed |
|---|---:|---:|
| KNOWLEDGE_MODE | rag | rag |
| OKF_ENABLED | true | true |
| OKF_SYNC_ENABLED | false | false |
| OKF_SHADOW_READS | false | false |
| ROUTER_MODE | off | off |
| ROUTER_COHORT_PERCENT | 0 | 0 |

No secret values are included. .env was last written at **2026-10-01 13:46:36 PDT**; backend process **18656** started at **13:47:44 PDT**. The pre-test readiness check returned HTTP 200 for /health and /ready; /ready reported ready, with PostgreSQL and Redis ok, and memory, TTS, and LLM ready. The readiness payload reported llm.live_verified=false, matching the Step 1 caveat. A post-test readiness check at approximately **14:01 PDT** again returned HTTP 200 and ready.

## Test details

- **Test date/time:** 2026-10-01, approximately **13:53 PDT** (**20:53 UTC**). Target turn began at 20:53:07.905Z and completed at 20:53:25.482Z.
- **Test method:** Physical Android voice input and spoken response. No text was injected into the backend.
- **Account/device continuity:** Database session records show the same user and device IDs as Step 1; client metadata identifies the platform as Android. The device ID is omitted here.
- **Exact question requested:** “What does Project Orion use for long-term memory storage?”
- **Persisted STT transcript:** “What does the project Orion use for long term memory storage?” The intended question was preserved in one transcript.
- **Expected active memory:** a8441991-cd10-473f-a975-2383e7e6f98c — “Project Orion uses PostgreSQL with PG vector for long-term memory storage.”
- **Persisted assistant response:** “Project Orion uses PostgreSQL with PGVector for long-term memory storage.”
- **User-reported audible response:** “postresql with pgvector.”
- **Session ID:** 14134e7c-97de-4b0c-8046-00ec097e5698
- **Turn ID:** 8d4d3965-3c4c-4bb9-b349-eb379e46706c
- **Response ID:** 8327e73d-7b60-4b46-9c72-16240c5c78c3

## Observed results

| Check | Result | Evidence |
|---|---|---|
| Voice request received | PASS | Target trace records turn_started, first_pcm_received, turn_commit_received, and turn_complete; persisted session and turn records match the same account and device as Step 1. |
| STT completed | PASS | Remote STT returned HTTP 200; the target turn persisted the intended question. Audio duration was 7,440 ms and STT inference was about 1,392 ms. |
| Effective knowledge mode | PASS | Effective KNOWLEDGE_MODE=rag; target trace contains the complete rag/memory_context_pipeline path. No OKF component event appears for this target turn. |
| Router disabled | PASS | Effective settings were ROUTER_MODE=off and ROUTER_COHORT_PERCENT=0; no router execution event appears on the target turn. |
| RAG retrieval stages | PASS | Full-text search returned 1 candidate; vector search returned 6; reciprocal-rank fusion and reranking processed 6. The relevance boundary accepted 5 final candidates. |
| Expected memory rank and score | PASS | Memory a8441991-cd10-473f-a975-2383e7e6f98c was reranked rank 1, score 0.999279, from dense and fts sources. It was marked trusted exact evidence and accepted; the final candidate list also placed it at rank 1 with score 0.999279. |
| Evidence passed toward LLM | PASS | Prompt build completed with memory_context_characters=501 and no prior history messages. LLM completion and the assistant response persisted for the same turn. The raw serialized prompt is not logged, so this establishes non-empty memory context construction and its use in the completed turn, not the exact prompt wording. |
| Assistant response supported by memory | PASS | The persisted answer says PostgreSQL with PGVector, matching the saved fact’s PostgreSQL with PG vector. The user confirmed hearing the same core answer. |
| LLM and TTS completed | PASS | LLM trace reports completion; TTS generated two segments and tts_response_completed; the gateway recorded turn_complete. |
| Target-turn errors/timeouts | PASS | Persisted target status is committed, error_count=0; RAG provider_error=null; no target-turn error or timeout event was found. |

## Comparison with Step 1 baseline

| Measure | Step 1 (OKF_ENABLED=false) | Step 2 (OKF_ENABLED=true) | Observation |
|---|---:|---:|---|
| Selected knowledge mode | rag | rag | Same mode. |
| FTS candidate count | 1 | 1 | Same. |
| Vector candidate count | 6 | 6 | Same. |
| RRF candidates | 6 | 6 | Same. |
| Final accepted candidates | 6 | 5 | Step 2 had one fewer candidate after the relevance boundary. |
| Orion memory final rank | 1 | 1 | Same top-ranked expected memory. |
| Orion reranker/final score | 0.999423 | 0.999279 | Step 2 was lower by 0.000144; both scores strongly ranked the expected fact first. |
| Prompt memory-context characters | 579 | 501 | Step 2 was 78 characters shorter; raw prompt content was not logged in either report. |
| Persisted assistant answer | PostgreSQL with pgvector | PostgreSQL with PGVector | Same supported answer, with capitalization difference. |
| Physical response | User confirmed hearing the answer | User confirmed hearing “postresql with pgvector” | Both tests completed audibly per user report and backend completion evidence. |

## Did enabling OKF change RAG behavior?

**Observed:** RAG remained selected and completed. The expected Orion memory remained the top final candidate with the same dense and fts sources, and the LLM returned an answer supported by it. The observed measurements were not identical: final accepted candidates decreased from 6 to 5, the Orion score changed from 0.999423 to 0.999279, and prompt memory context decreased from 579 to 501 characters. No OKF component event was recorded for the Step 2 target turn.

**Possible explanations, not proven causes:** Step 1’s persisted STT transcript repeated the question three times, while Step 2’s transcript contained it once and with slightly different wording (“the project Orion” and “long term”). That input difference may affect retrieval scores, boundary acceptance, or context assembly. Normal RAG scoring variability is another possibility. The logs do not establish that enabling OKF caused the numerical differences. For this tested turn, enabling the OKF master switch did not change the successful RAG outcome or select an OKF retrieval path.

## Separate later voice-session activity

After the successful target turn, additional non-target turns appeared in the same Android session. One later turn ended with stt_audio_too_long after 120,031 ms of observed audio and has status=failed, error_count=1. A subsequent separate session had an active turn without a committed transcript or assistant response at evidence collection time. These events occurred after the target response, do not share its turn ID, and are not counted as target-test failures. The target test itself committed with zero errors.

## Evidence and limitations

- **Primary trace:** logs/latency_trace.jsonl (UTC timestamps), filtered by target session, turn, and response IDs above. It records the RAG stages, candidate scores, prompt-context character count, LLM/TTS completion, and gateway turn completion.
- **Persisted records:** Read-only inspection of voice_sessions, conversation_turns, messages, and memory_items. The target user and assistant messages, committed turn, active saved memory, and same Step 1/Step 2 user/device identity were confirmed.
- **Readiness:** Live /health and /ready checks before and after the physical test.
- **Evidence limits:** The raw serialized LLM prompt and a separate Android client log were not available. Spoken delivery is supported by backend websocket/TTS completion and the user’s report. The later audio-too-long failure is documented separately because it belongs to a different turn.
- **Scope:** No code, database contents, or .env values were modified during this validation.

## Conclusion

**Step 2 PASS.** The physical voice test completed, RAG retrieved the expected Orion memory as its rank-1 final candidate, and the assistant delivered an answer supported by that memory while OKF_ENABLED=true. This single-turn test shows RAG remained functional with OKF enabled; the measured differences from Step 1 are documented above without assigning an unverified cause.
