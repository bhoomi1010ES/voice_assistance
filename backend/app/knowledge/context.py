from __future__ import annotations

import html
import uuid
from dataclasses import dataclass

from .types import KnowledgeSelection


@dataclass(frozen=True)
class KnowledgeContext:
    """Bounded prompt envelope with distinct, untrusted engine sections."""

    text: str
    evidence_ids: tuple[uuid.UUID, ...]
    rag_characters: int
    okf_characters: int


def build_knowledge_context(
    selection: KnowledgeSelection,
    *,
    max_chars: int,
) -> KnowledgeContext:
    if max_chars < 1:
        raise ValueError("knowledge_context_limit_invalid")
    seen: set[tuple[tuple[uuid.UUID, ...], str]] = set()
    sections: list[tuple[str, list[str]]] = []
    remaining = max_chars
    selected_ids: set[uuid.UUID] = set()
    rag_characters = 0
    okf_characters = 0
    for engine in ("rag", "okf"):
        lines = []
        facts = (
            fact
            for result in selection.results
            if result.engine == engine
            for fact in result.facts
        )
        for fact in facts:
            normalized_text = " ".join(fact.text.split())
            if not normalized_text:
                continue
            dedupe_key = (
                tuple(sorted(set(fact.source_memory_ids), key=str)),
                normalized_text.casefold(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            source_ids = ",".join(str(value) for value in dedupe_key[0]) or "unknown"
            line = (
                f'<fact type="{html.escape(fact.kind, quote=True)}" '
                f'key="{html.escape(fact.key, quote=True)}" sources="{source_ids}">'
                f"{html.escape(normalized_text)}</fact>"
            )
            if len(line) + 32 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 32
            selected_ids.update(fact.source_memory_ids)
        if lines:
            label = f"{engine.upper()}_EVIDENCE"
            body = f"<{label}>\n" + "\n".join(lines) + f"\n</{label}>"
            sections.append((engine, [body]))
            if engine == "rag":
                rag_characters = len(body)
            else:
                okf_characters = len(body)
    rendered = "\n".join(section[1][0] for section in sections)
    # Account for inter-section newline while preserving the configured hard ceiling.
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars]
        rag_characters = min(rag_characters, max_chars)
        okf_characters = max(0, max_chars - rag_characters)
    return KnowledgeContext(
        text=rendered,
        evidence_ids=tuple(sorted(selected_ids, key=str)),
        rag_characters=rag_characters,
        okf_characters=okf_characters,
    )
