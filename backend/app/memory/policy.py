from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .types import MemoryType, normalize_memory_text, stable_dedupe_key

_SECRET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def validate_safe_json(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("memory_metadata_too_large")
    for key, item in _walk_json(value):
        if _SECRET.search(key) or (isinstance(item, str) and _SECRET.search(item)):
            raise ValueError("memory_candidate_sensitive")
    return value


def _walk_json(value: Any, *, depth: int = 0):
    if depth > 4:
        raise ValueError("memory_metadata_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_json(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield "", item
            yield from _walk_json(item, depth=depth + 1)


@dataclass(frozen=True)
class ExtractionCandidate:
    content: str
    memory_type: MemoryType
    subject: str | None = None
    predicate: str | None = None
    object_json: dict[str, Any] | None = None
    confidence: float = 1.0
    salience: float = 0.5
    source_offset: int | None = None
    source_length: int | None = None

    @property
    def dedupe_key(self) -> str:
        return stable_dedupe_key(
            memory_type=self.memory_type,
            subject=self.subject,
            predicate=self.predicate,
            object_json=self.object_json,
            content=self.content,
        )


def validate_candidate(candidate: ExtractionCandidate, source_text: str) -> ExtractionCandidate:
    content = " ".join(candidate.content.split())
    source = " ".join(source_text.split())
    if not content or len(content) > 2_000:
        raise ValueError("memory_candidate_invalid")
    if _SECRET.search(content):
        raise ValueError("memory_candidate_sensitive")
    validate_safe_json(candidate.object_json)
    if not 0 <= candidate.confidence <= 1 or not 0 <= candidate.salience <= 1:
        raise ValueError("memory_candidate_invalid")
    if normalize_memory_text(content) not in normalize_memory_text(source):
        raise ValueError("memory_candidate_not_grounded")
    return ExtractionCandidate(
        content=content,
        memory_type=candidate.memory_type,
        subject=candidate.subject.strip() if candidate.subject else None,
        predicate=candidate.predicate.strip() if candidate.predicate else None,
        object_json=candidate.object_json,
        confidence=candidate.confidence,
        salience=candidate.salience,
        source_offset=candidate.source_offset,
        source_length=candidate.source_length,
    )
