# LangGraph router foundation — Phase 1

**Date:** 2026-09-23 18:19 America/Los_Angeles (2026-09-24 01:19 UTC)  
**Status:** Implemented; feature mode defaults to `off`; no gateway integration or route cutover.  
**Known pre-existing failure:** `test_llm_context.py::test_informational_and_ordinary_voice_intent_remains_auto[How do I create a task?]` was reproduced before changes. The existing intent regex forces `create_task` for an informational question. This Phase 1 work does not change that behavior.

## Todo

- [x] Pin LangGraph and resolve its Python 3.12 dependency set.
- [x] Add a side-effect-free compiled routing graph and Pydantic route contract.
- [x] Add off/shadow/canary/on service modes, deterministic user cohort selection, timeout and pre-effect legacy fallback.
- [x] Add tests for safe defaults, validation, graph topology, route outcomes, cancellation, timeout, cohort selection, and fallback.
- [x] Confirm the gateway does not invoke the router and its existing orchestrator remains unchanged.
- [x] Run focused and full backend verification; record unrelated known failures.

## Implementation

- Added `langgraph==1.2.12` to `backend/pyproject.toml` and frozen `requirements.txt`. The project virtual environment is Python 3.12.10. The official LangGraph package metadata requires Python `>=3.10` and includes Python 3.12 support; installation and graph tests also succeeded under the project interpreter.
- Locked the resolved transitive dependencies in `requirements.txt`. LangGraph SDK requires `websockets<17`, so the frozen environment pin is now `websockets==16.1.1` (the previous pin was 17.0.1). `pip check` reports no broken requirements.
- Added `backend/app/routing/models.py` with the allowed route enum, strict frozen `RouteDecision`, action-domain labels that cannot carry executable write arguments, non-executable mixed-intent outcome, runtime turn context, modes, and service result contracts.
- Added `backend/app/routing/graph.py` with a compiled `StateGraph`, one validation entry node, explicit conditional route edges, terminal side-effect-free outcome nodes, and cancellation checks. It has no checkpointer, model, tool, database, or TTS integration.
- Added `backend/app/routing/service.py`. `off` returns before constructing or invoking the graph. `shadow` returns a candidate while keeping the legacy orchestrator authoritative. Canary/shadow sampling uses a stable SHA-256 bucket of authenticated `user_id`; `on` selects all users. Timeout, cancellation, and graph errors fall back to the legacy path before effects.
- Added `ROUTER_MODE=off`, `ROUTER_COHORT_PERCENT=0`, and `ROUTER_TIMEOUT_MS=250` to Settings and `.env.example`. The gateway was not edited, so no user turn can reach this service yet.
- Added `backend/tests/test_router_foundation.py` for settings, model validation, allowed tool targets, forbidden write payloads, graph compilation/edges/outcomes, stable cohort assignment, cancellation, timeout, off-mode no-call behavior, shadow behavior, and pre-effect fallback.

## Verification

| Check | Result |
|---|---|
| Python interpreter | Python 3.12.10 |
| `pytest -q tests/test_router_foundation.py tests/test_config.py` | **51 passed** |
| Full backend `pytest -q --tb=short` | **394 passed, 1 failed, 56 skipped**. Only failure is the known informational `How do I create a task?` false positive, reproduced before implementation. |
| Focused Ruff check (`app/routing`, `tests/test_router_foundation.py`, `app/core/config.py`) | PASS |
| Focused Ruff format check | PASS |
| `pip check` | PASS; no broken requirements |
| Frozen requirements vs installed environment | 78 package pins match the project virtual environment |
| `git diff --check` | PASS; Git emitted only existing LF-to-CRLF notices |

No full frontend, API deployment, mobile build, live provider call, or physical-device test was run. No router model call, tool call, memory retrieval, write, or user-visible event was added to the gateway path.

## Files changed for Phase 1

- `.env.example`
- `backend/app/core/config.py`
- `backend/app/routing/__init__.py`
- `backend/app/routing/models.py`
- `backend/app/routing/graph.py`
- `backend/app/routing/service.py`
- `backend/pyproject.toml`
- `backend/tests/test_router_foundation.py`
- `requirements.txt`
- `plan.md`
- `.gitignore` (allowlisted this required work note under the ignored `docs/` directory)

The earlier Phase 0 baseline/corpus documentation changes were already present and were not changed by this Phase 1 implementation.

## Compatibility references

- [LangGraph package metadata](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml) declares Python `>=3.10` and the dependencies resolved into the frozen project environment.
- [LangGraph Graph API documentation](https://docs.langchain.com/oss/python/langgraph/graph-api) documents `StateGraph`, conditional edges, `Runtime`, and ephemeral `context_schema`.
