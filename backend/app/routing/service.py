from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.config import Settings
from app.routing.models import (
    RouteDecision,
    RouteName,
    RouterCancelledError,
    RouterMode,
    RouterOutcome,
    RouterRunResult,
    RouterRunStatus,
    RouterRuntimeContext,
)

LOGGER = logging.getLogger("voice-assistance-backend")


class CompiledRouter(Protocol):
    async def ainvoke(self, input: dict[str, object], *, context: RouterRuntimeContext) -> dict:
        """Run the pure routing graph for one authenticated turn."""


class DecisionRouterService:
    """Feature-gated router; shadow observations never own the response path."""

    def __init__(
        self,
        settings: Settings,
        graph: CompiledRouter | None = None,
        *,
        shadow_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self.settings = settings
        self._graph = graph
        self._shadow_semaphore = shadow_semaphore

    async def observe_shadow_transcript(
        self,
        transcript: str,
        *,
        context: RouterRuntimeContext,
        legacy_route: RouteName | None,
        memory_route_allowed: bool,
    ) -> RouterRunResult:
        """Observe one sampled turn without affecting its legacy response.

        The caller must first run the existing confirmation resolver and legacy
        response path. This method classifies locally and invokes only the pure
        graph; it never calls an LLM, retrieval service, tool, or TTS path.
        """

        if RouterMode(self.settings.router_mode) != RouterMode.SHADOW:
            return RouterRunResult(
                status=RouterRunStatus.DISABLED,
                use_legacy_orchestrator=True,
            )
        if not self._in_cohort(RouterMode.SHADOW, context.user_id):
            return RouterRunResult(
                status=RouterRunStatus.OUT_OF_COHORT,
                use_legacy_orchestrator=True,
            )

        started_ns = time.perf_counter_ns()
        decision = None
        try:
            from app.routing.rules import classify_transcript

            decision = classify_transcript(transcript)
        except (TypeError, ValueError):
            result = RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=True,
            )
            self._log_shadow_observation(
                context=context,
                result=result,
                candidate_decision=decision,
                legacy_route=legacy_route,
                latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                fallback_reason="decision_validation_failed",
            )
            return result

        if decision.route == RouteName.MEMORY_QUERY and not memory_route_allowed:
            result = RouterRunResult(
                status=RouterRunStatus.SKIPPED_MEMORY_POLICY,
                decision=decision,
                use_legacy_orchestrator=True,
            )
            self._log_shadow_observation(
                context=context,
                result=result,
                candidate_decision=decision,
                legacy_route=legacy_route,
                latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                fallback_reason="memory_retrieval_disabled_or_opted_out",
            )
            return result

        semaphore = self._shadow_semaphore
        if semaphore is not None and semaphore.locked():
            result = RouterRunResult(
                status=RouterRunStatus.SKIPPED_CAPACITY,
                decision=decision,
                use_legacy_orchestrator=True,
            )
            self._log_shadow_observation(
                context=context,
                result=result,
                candidate_decision=decision,
                legacy_route=legacy_route,
                latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                fallback_reason="shadow_concurrency_limit_reached",
            )
            return result

        if semaphore is not None:
            await semaphore.acquire()
        try:
            result = await self.decide(decision, context=context)
        finally:
            if semaphore is not None:
                semaphore.release()
        self._log_shadow_observation(
            context=context,
            result=result,
            candidate_decision=decision,
            legacy_route=legacy_route,
            latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
            fallback_reason=(
                result.status.value if result.status != RouterRunStatus.DECIDED else None
            ),
        )
        return result

    @staticmethod
    def _log_shadow_observation(
        *,
        context: RouterRuntimeContext,
        result: RouterRunResult,
        candidate_decision: RouteDecision | None,
        legacy_route: RouteName | None,
        latency_ms: float,
        fallback_reason: str | None,
    ) -> None:
        decision = result.decision or candidate_decision
        outcome = result.outcome
        shadow_route = (
            outcome.route
            if outcome is not None
            else (decision.route if decision is not None else None)
        )
        disagreement = (
            shadow_route != legacy_route
            if shadow_route is not None and legacy_route is not None and outcome is not None
            else None
        )
        LOGGER.info(
            "Router shadow observation",
            extra={
                "event": "router.shadow.observation",
                "session_id": str(context.session_id),
                "turn_id": str(context.turn_id),
                "response_id": str(context.response_id),
                "router_status": result.status.value,
                "shadow_route": shadow_route.value if shadow_route is not None else None,
                "confidence": decision.confidence if decision is not None else None,
                "target_tool": decision.target_tool if decision is not None else None,
                "action_domain": (
                    decision.action_domain.value
                    if decision is not None and decision.action_domain is not None
                    else None
                ),
                "decision_source": (
                    decision.decision_source.value if decision is not None else None
                ),
                "legacy_route": legacy_route.value if legacy_route is not None else None,
                "needs_clarification": (
                    outcome.needs_clarification if outcome is not None else None
                ),
                "disagreement": disagreement,
                "disagreement_category": (
                    f"{legacy_route.value}->{shadow_route.value}"
                    if disagreement and legacy_route is not None and shadow_route is not None
                    else None
                ),
                "fallback_reason": fallback_reason,
                "latency_ms": round(max(0.0, latency_ms), 3),
            },
        )

    async def route_transcript(
        self,
        transcript: str,
        *,
        context: RouterRuntimeContext,
        resolve_pending_confirmation: Callable[[], Awaitable[object | None]],
    ) -> RouterRunResult:
        """Run cancellation and the caller's existing scoped resolver before rules.

        The callback must invoke the authoritative confirmation store/resolver
        (for example, the gateway's `_resolve_pending_confirmation` with its
        authenticated user/device/session scope). This method owns no approval
        state and does not reinterpret yes/no responses.
        """

        mode = RouterMode(self.settings.router_mode)
        if mode == RouterMode.OFF:
            return RouterRunResult(
                status=RouterRunStatus.DISABLED,
                use_legacy_orchestrator=True,
            )
        if context.cancellation_check():
            return RouterRunResult(
                status=RouterRunStatus.CANCELLED,
                use_legacy_orchestrator=False,
            )
        try:
            confirmation_result = await resolve_pending_confirmation()
        except Exception:  # noqa: BLE001 - fail closed if confirmation state is unavailable
            return RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=False,
            )
        if confirmation_result is not None:
            return RouterRunResult(
                status=RouterRunStatus.CONFIRMATION_HANDLED,
                use_legacy_orchestrator=False,
            )
        if context.cancellation_check():
            return RouterRunResult(
                status=RouterRunStatus.CANCELLED,
                use_legacy_orchestrator=False,
            )
        try:
            from app.routing.rules import classify_transcript

            decision = classify_transcript(transcript)
        except (TypeError, ValueError):
            return RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=True,
            )
        return await self.decide(decision, context=context)

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
            try:
                from app.routing.graph import build_router_graph

                graph = build_router_graph()
                self._graph = graph
            except Exception:  # noqa: BLE001 - graph setup failure is a safe fallback
                return RouterRunResult(
                    status=RouterRunStatus.FAILED,
                    use_legacy_orchestrator=True,
                )

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

        try:
            outcome = RouterOutcome.model_validate(result["outcome"])
        except Exception:  # noqa: BLE001 - malformed graph output is a safe fallback
            return RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=True,
            )
        if outcome.route != decision.route or outcome.needs_clarification != (
            decision.route == RouteName.MIXED_AMBIGUOUS
        ):
            return RouterRunResult(
                status=RouterRunStatus.FAILED,
                use_legacy_orchestrator=True,
            )
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
