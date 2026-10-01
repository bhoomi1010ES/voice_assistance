from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from app.memory.evaluation import MemoryEvaluation
from app.memory.types import MemoryRetrievalResult
from app.okf.types import KnowledgeDisposition, KnowledgeResult

KnowledgeMode = Literal["rag", "okf", "combined"]


@dataclass(frozen=True)
class KnowledgeFact:
    """Engine-neutral, source-grounded fact for safe context composition."""

    key: str
    text: str
    source_memory_ids: tuple[uuid.UUID, ...]
    kind: str


@dataclass(frozen=True)
class KnowledgeEngineResult:
    engine: Literal["rag", "okf"]
    disposition: KnowledgeDisposition
    reason: str
    facts: tuple[KnowledgeFact, ...] = ()
    direct_text: str | None = None
    evidence_ids: tuple[uuid.UUID, ...] = ()
    rag_result: MemoryRetrievalResult | None = field(default=None, repr=False, compare=False)
    rag_evaluation: MemoryEvaluation | None = field(default=None, repr=False, compare=False)
    okf_result: KnowledgeResult | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class KnowledgeSelection:
    mode: KnowledgeMode
    disposition: KnowledgeDisposition
    results: tuple[KnowledgeEngineResult, ...]
    facts: tuple[KnowledgeFact, ...]
    evidence_ids: tuple[uuid.UUID, ...]
    reason: str

