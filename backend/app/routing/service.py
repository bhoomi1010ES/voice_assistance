from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Protocol

from app.core.config import Settings
from app.routing.models import (
    RouteDecision,
    RouterCancelledError,
    RouterMode,
    RouterOutcome,
    RouterRunResult,
    RouterRunStatus,
    RouterRuntimeContext,
)


class CompiledRouter(Protocol):
    async def ainvoke(self, input: dict[str, object], *, context: RouterRuntimeContext) -> dict:
        """Run the pure routing graph for one authenticated turn."""


class DecisionRouterService:
    """Feature-gated wrapper; no gateway integration is performed in Phase 1."""

    def __init__(self, settings: Settings, graph: CompiledRouter | None = None) -> None:
        self.settings = settings
        self._graph = graph

    async def decide(
        self,
        decision: RouteDecision,
        *,
        context: RouterRuntimeContext,
    ) -> RouterRunResult:
        mode = RouterMode(self.settings.router_mode)
        if mode == RouterMode.OFF:
            return RouterRunResult(
                status=RouterRunStatus.DISABLED,
                use_legacy_orchestrator=True,
            )

        if context.cancellation_check():
            return RouterRunResult(
                status=RouterRunStatus.CANCELLED,
                use_legacy_orchestrator=True,
            )

        if not self._in_cohort(mode, context.user_id):
            return RouterRunResult(
                status=RouterRunStatus.OUT_OF_COHORT,
                use_legacy_orchestrator=True,
            )

        graph = self._graph
        if graph is None:
            from app.routing.graph import build_router_graph

            graph = build_router_graph()
            self._graph = graph

        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {"decision": decision.model_dump(mode="json")},
                    context=context,
                ),
                timeout=self.settings.router_timeout_ms / 1000,
            )
        except RouterCancelledError:
            return RouterRunResult(
                status=RouterRunStatus.CANCELLED,
                use_legacy_orchestrator=True,
            )
        except TimeoutError:
            return RouterRunResult(
                status=RouterRunStatus.TIMED_OUT,
                use_legacy_orchestrator=True,
            )
        except Exception:  # noqa: BLE001 - graph is pure; pre-effect fallback is safe
            return RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=True,
            )

        outcome = RouterOutcome.model_validate(result["outcome"])
        # Shadow mode always observes a candidate while preserving the legacy
        # orchestrator as the authoritative response path.
        return RouterRunResult(
            status=RouterRunStatus.DECIDED,
            decision=decision,
            outcome=outcome,
            use_legacy_orchestrator=mode == RouterMode.SHADOW,
        )

    def _in_cohort(self, mode: RouterMode, user_id: uuid.UUID) -> bool:
        if mode == RouterMode.ON:
            return True
        percent = self.settings.router_cohort_percent
        if percent <= 0:
            return False
        digest = hashlib.sha256(user_id.bytes).digest()
        bucket = int.from_bytes(digest[:8], "big") % 100
        return bucket < percent
