from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.okf.types import KnowledgeDisposition

from .engines import KnowledgeEngine
from .types import KnowledgeEngineResult, KnowledgeFact, KnowledgeMode, KnowledgeSelection


class KnowledgeSelector:
    """Run the configured engine set and combine only grounded evidence."""

    def __init__(self, mode: KnowledgeMode, engines: Sequence[KnowledgeEngine]) -> None:
        if mode not in {"rag", "okf", "combined"}:
            raise ValueError("knowledge_mode_invalid")
        expected = ("rag",) if mode == "rag" else ("okf",) if mode == "okf" else ("rag", "okf")
        by_name = {engine.name: engine for engine in engines}
        if any(name not in by_name for name in expected):
            raise ValueError("knowledge_engine_missing")
        self.mode = mode
        self.engines = tuple(by_name[name] for name in expected)

    async def retrieve(self, session: AsyncSession, request) -> KnowledgeSelection:
        if request.cancellation_check:
            return KnowledgeSelection(
                mode=self.mode,
                disposition=KnowledgeDisposition.CANCELLED,
                results=(),
                facts=(),
                evidence_ids=(),
                reason="cancelled_before_retrieval",
            )
        results: list[KnowledgeEngineResult] = []
        for engine in self.engines:
            try:
                results.append(await engine.retrieve(session, request))
            except TimeoutError:
                results.append(
                    KnowledgeEngineResult(
                        engine=engine.name,
                        disposition=KnowledgeDisposition.UNAVAILABLE,
                        reason="engine_timeout",
                    )
                )
            except SQLAlchemyError:
                results.append(
                    KnowledgeEngineResult(
                        engine=engine.name,
                        disposition=KnowledgeDisposition.UNAVAILABLE,
                        reason="engine_unavailable",
                    )
                )
            except Exception:  # noqa: BLE001 - an engine failure must not expose its details
                results.append(
                    KnowledgeEngineResult(
                        engine=engine.name,
                        disposition=KnowledgeDisposition.UNAVAILABLE,
                        reason="engine_unavailable",
                    )
                )
        return self.combine(self.mode, tuple(results))

    @staticmethod
    def combine(
        mode: KnowledgeMode, results: tuple[KnowledgeEngineResult, ...]
    ) -> KnowledgeSelection:
        if mode != "combined":
            result = results[0]
            return KnowledgeSelection(
                mode=mode,
                disposition=result.disposition,
                results=results,
                facts=result.facts,
                evidence_ids=result.evidence_ids,
                reason=result.reason,
            )
        if any(result.disposition == KnowledgeDisposition.CONFLICT for result in results):
            disposition = KnowledgeDisposition.CONFLICT
            reason = "conflicting_engine_evidence"
        else:
            available = tuple(
                result
                for result in results
                if result.disposition
                in {
                    KnowledgeDisposition.DIRECT_ANSWER,
                    KnowledgeDisposition.CONTINUE_WITH_EVIDENCE,
                }
            )
            if available:
                disposition = KnowledgeDisposition.CONTINUE_WITH_EVIDENCE
                reason = (
                    "partial_engine_failure"
                    if any(
                        result.disposition == KnowledgeDisposition.UNAVAILABLE
                        for result in results
                    )
                    else "combined_evidence"
                )
            elif any(result.disposition == KnowledgeDisposition.UNAVAILABLE for result in results):
                disposition = KnowledgeDisposition.UNAVAILABLE
                reason = "engines_unavailable"
            else:
                disposition = KnowledgeDisposition.NO_RESULT
                reason = "no_current_matching_evidence"
        facts = _dedupe_facts(fact for result in results for fact in result.facts)
        evidence_ids = tuple(
            sorted({source_id for fact in facts for source_id in fact.source_memory_ids}, key=str)
        )
        return KnowledgeSelection(
            mode=mode,
            disposition=disposition,
            results=results,
            facts=facts,
            evidence_ids=evidence_ids,
            reason=reason,
        )


def _dedupe_facts(facts) -> tuple[KnowledgeFact, ...]:
    unique: dict[tuple[tuple[str, ...], str], KnowledgeFact] = {}
    for fact in facts:
        normalized = " ".join(fact.text.split()).casefold()
        key = (tuple(sorted(str(item) for item in set(fact.source_memory_ids))), normalized)
        if normalized and key not in unique:
            unique[key] = fact
    return tuple(unique[key] for key in sorted(unique))
