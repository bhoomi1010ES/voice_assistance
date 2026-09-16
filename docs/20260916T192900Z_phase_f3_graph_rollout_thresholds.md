# Phase F3 GraphRAG rollout thresholds

Status: frozen before any GraphRAG implementation or graph retrieval enablement.

The comparison baseline is `phase6-f3-hybrid-only-v1`, using the retained Phase 6 corpus and the effective runtime contract `MEMORY_RETRIEVAL_MODE=inject`, `MEMORY_WRITE_ENABLED=true`, `GRAPH_RAG_MODE=off`, and `GRAPH_WRITE_ENABLED=false`.

## Required safety gates

- Cross-user leakage: `0`.
- Deleted, superseded, excluded-session, and disabled-user memory leakage: `0`.
- No-result relevant-memory injection: `0`.
- PostgreSQL ownership constraints and lifecycle tests: all green.
- Unsupported graph job types: dead-lettered or rejected without retry loops.
- Graph failures: baseline hybrid retrieval and response behavior remain available; graph failure must not inject context.

## Graph-off equality gate

With graph mode off and graph writes disabled, retrieval IDs, injected context, and response routing must be exactly equal to the frozen hybrid-only baseline for the same corpus, user, session, and query cohort. Any graph query, graph context, or graph write in this mode is a failure.

## Quality and latency gates for later graph-on evaluation

Graph-on results are compared with the same corpus and cohort. They must meet or exceed the frozen baseline:

| Metric | Frozen baseline | Required threshold |
|---|---:|---:|
| Hybrid Recall@5 | 1.000 | `>= 1.000` |
| Final MRR | 0.875 | `>= 0.875` |
| Final nDCG@5 | 0.895 | `>= 0.895` |
| Final top-1 accuracy | 0.800 | `>= 0.800` |
| Retrieval p50 | 378.082 ms | `<= 416 ms` |
| Retrieval p95 | 431.595 ms | `<= 475 ms` |

The rounded latency limits allow at most a 10% regression over the retained hybrid-only run. A graph-specific stage must also stay within `50 ms` p50 and `100 ms` p95, measured separately. These are acceptance limits, not optimization targets.

## Rollback gate

Rollback is the tested configuration with `GRAPH_RAG_MODE=off` and `GRAPH_WRITE_ENABLED=false`. It must remove graph context and graph writes while preserving hybrid retrieval. Do not downgrade the live database as part of a runtime rollback; migration rollback requires a separately approved maintenance procedure after confirming no graph job rows exist.

No GraphRAG implementation, graph retrieval, or production graph write may begin until the F0-F3 closure report records a valid physical-device latency baseline and all gates above are approved.
