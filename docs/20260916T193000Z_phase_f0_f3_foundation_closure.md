# F0-F3 foundation closure

Status at capture: pending a fresh valid physical-device latency run. GraphRAG implementation and graph retrieval remain disabled.

## F0 baseline and runtime

- Repository commit: `8033c81204958e179ac2ba5a606a88ab5d08d8cb`.
- Repository and target database head after migration: `0014_memory_supersession_owner`.
- PostgreSQL: 18.4; pgvector: 0.8.6.
- Requested backend command: `..\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 8000`.
- Python: 3.12.10. The Uvicorn listener is running on port 8000; Metro is listening on 8081.
- Effective runtime flags: `MEMORY_RETRIEVAL_MODE=inject`, `MEMORY_WRITE_ENABLED=true`, `GRAPH_RAG_MODE=off`, `GRAPH_WRITE_ENABLED=false`.
- `/health`: `ok`. `/ready`: `ready`; PostgreSQL, Redis, embedding, reranker, and NVIDIA provider contracts reported ready. The readiness contract reports `live_verified=false`; the retained NVIDIA live validation is a separate PASS artifact.
- Runtime evidence: `docs/evidence/phase6/phase6_f3_runtime_20260916.json`.

## F1/F2 validation

- Focused Phase 6 and PostgreSQL integration tests after applying 0014: `60 passed, 289 deselected, 1 warning`.
- Provider unit/contract tests: `35 passed, 1 warning`.
- Latency tests: `17 passed` before the analyzer clock-safety addition; analyzer/live tests after that addition: `11 passed`.
- Full backend suite: `296 passed, 53 skipped, 1 warning`.
- Ruff check passed for the backend and modified scripts. Modified files are formatted. Two unrelated pre-existing files remain outside this change's format scope: `backend/app/api/tasks.py` and `backend/app/tts/remote.py`.
- NVIDIA live validation: 8 cases, zero errors, zero cross-user leakage, zero unauthorized tool executions; worker validation: 10/10 concurrent jobs completed.

## Migration 0014

Migration: `0014_memory_supersession_owner`

Reason: enforce owner-scoped supersession references with a composite `(supersedes_id, user_id)` foreign key.

Pre-migration evidence: `docs/evidence/phase6/phase6_f3_migration_pre_0014_20260916.json`.

Post-migration evidence: `docs/evidence/phase6/phase6_f3_migration_post_0014_20260916.json`.

Backup and disposable restore evidence: `docs/evidence/phase6/phase6_f3_backup_restore_20260916.json`.

| Check | Result |
|---|---|
| Existing rows preserved | PASS: 26 rows before and after; backup restore matched 26 rows |
| Existing status counts preserved | PASS: active 20, superseded 6 |
| Existing supersession references preserved | PASS: 6 |
| Cross-owner supersession rejected | PASS |
| Same-owner supersession accepted | PASS |
| Delete-parent `ON DELETE SET NULL` behavior | PASS; child owner preserved |
| Alembic repository/live head | PASS: 0014 |

## F3 hybrid-only baseline

The retained graph-off baseline remains `docs/phase6_f3_hybrid_only_baseline_20260916.json`, updated after migration. Retrieval evaluation passed 25 cases with Hybrid Recall@5 1.000, MRR 0.875, nDCG@5 0.895, top-1 accuracy 0.800, p50 378.082 ms, and p95 431.595 ms. Cross-user, deleted, superseded, excluded, disabled, and no-result leakage checks were zero.

Frozen rollout limits are in `docs/20260916T192900Z_phase_f3_graph_rollout_thresholds.md`. They are gates for later graph-on work; they do not authorize graph enablement.

## Physical-device latency gate

A fresh five-turn run was captured on OPPO CPH2527 (`9b0ea196`) after restoring the ADB reverse tunnels. All five responses completed, but only two turns were valid for acceptance. Three turns were rejected because the trace contained monotonic values from a second origin labelled as `backend`; six derived metrics therefore crossed incompatible clock domains. The analyzer correctly reported those metrics as `n/a â€” incompatible clock domains` and did not clamp them to zero. Per the smoke-test gate, collection stopped after these failures and the 20-turn run was not started.

Evidence:

- Raw trace: `docs/evidence/phase6/phase6_f3_physical_latency_20260916.jsonl`
- Analysis: `docs/evidence/phase6/phase6_f3_physical_latency_analysis_20260916.json`
- Report: `docs/evidence/phase6/phase6_f3_physical_latency_analysis_20260916.txt`

| Acceptance item | Result |
|---|---|
| Fresh physical-device trace | PASS: 5 attempted turns captured |
| STT-final â†’ orchestration breakdown | PASS for 2 valid turns; 3 rejected for clock integrity |
| STT-final â†’ LLM request breakdown | FAIL: affected by cross-origin gateway/backend records |
| Provider/queue/unaccounted breakdown | FAIL for acceptance: cross-clock metrics rejected |
| Valid physical STT-final â†’ first-token baseline | FAIL: smoke clock-integrity gate failed |

The run recorded zero negative durations, zero artificial zero durations, and zero impossible-ordering violations. The remaining failure is trace clock-domain integrity, not a provider-latency result.

## Verdict

`FOUNDATION F0-F3: BLOCKED ON CLOCK-SAFE PHYSICAL LATENCY EVIDENCE`

Do not begin Stage 5 or enable GraphRAG until the collector emits one consistent monotonic clock domain per process and a new smoke run passes the clock-integrity gate. The current evidence does establish that the target database migration, F1/F2 behavior, hybrid-only quality baseline, provider contracts, and graph-off runtime are ready for that final measurement.
