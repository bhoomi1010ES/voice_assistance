# Phase F3 — Hybrid-only baseline freeze

Date: 2026-09-16

## Scope

Phase F3 reverified the F1/F2 remediation with GraphRAG disabled. The frozen manifest is [`phase6_f3_hybrid_only_baseline_20260916.json`](C:/Users/lenovo/Desktop/voice_assistance/docs/phase6_f3_hybrid_only_baseline_20260916.json). It records the corpus, model versions, runtime flags, test results, provider results, latency evidence, and SHA-256 hashes of the retained result files.

The effective configuration was:

- `MEMORY_RETRIEVAL_MODE=inject`
- `MEMORY_WRITE_ENABLED=true`
- `GRAPH_RAG_MODE=off`
- `GRAPH_WRITE_ENABLED=false`

No graph reads or graph writes participated in the baseline.
The checks ran in isolated repository processes; no long-lived backend listener was observed. The flags were read by those processes through `Settings` with the graph-off values recorded above.

## Reverification

| Check | Result |
|---|---|
| Focused Phase 6 and integration tests | 60 passed, 1 existing Starlette/httpx warning |
| Full backend suite | 296 passed, 53 skipped, 1 existing warning |
| Provider unit/contract tests | 35 passed, 1 warning |
| Latency and analyzer tests | 17 passed |
| Live hybrid retrieval evaluation | PASS; 25 cases |
| Extraction evaluation | PASS |
| Live NVIDIA memory validation | PASS; 8 cases, zero provider errors |
| Production worker validation | PASS; 18 jobs, 10/10 concurrency jobs completed |

The live retrieval evaluation recorded Hybrid Recall@5 `1.000`, MRR `0.875`, nDCG@5 `0.895`, top-1 accuracy `0.800`, p50 latency `378.082 ms`, p95 latency `431.595 ms`, zero cross-user leakage, zero deleted-memory retrieval, and zero no-result injection.

The NVIDIA validation returned grounded answers, passed no-result and prompt-injection checks, and recorded zero unauthorized tool executions and zero cross-user leakage. Its provider metadata still reports `live_verified=false`; the eight configured NVIDIA requests nevertheless completed without provider errors. This metadata limitation is retained in the manifest rather than hidden.
The worker validation was retained from a rerun after an initial probe produced an incomplete concurrent sample; the retained run completed all 10/10 concurrent jobs.

## Latency evidence

The retrieval-evaluation latency is valid for the hybrid-only baseline and is retained in the manifest. The available physical-device trace was re-analyzed, but it is not accepted as an end-to-end voice baseline: it mixes monotonic clock domains, producing zero or missing first-token values and negative unaccounted intervals. The analyzer output is retained at `docs/evidence/phase6/phase6_f3_latency_analysis_20260916.txt` with that limitation recorded.

## Migration state

Repository Alembic head is `0014_memory_supersession_owner`. The shared database remains at `0013_graph_index_job_type`; the pending F2 migration `0014_memory_supersession_owner` was not applied during F3. The existing graph foundation/job migrations through `0013` remain present, while graph reads and writes stayed disabled.

## Harness correction

The NVIDIA validation harness was updated to pass the current gateway helper's required session, turn, and response identifiers and to avoid flagging a conditional “tell me what your favorite restaurant is” sentence as a fabricated personal fact. This changes only validation behavior; production memory and voice behavior were not changed.

The hybrid-only baseline is ready for reviewer approval and later graph-off equality/shadow comparison. Stage 5 was not started.
