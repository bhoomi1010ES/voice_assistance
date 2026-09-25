from __future__ import annotations

from dataclasses import dataclass

from .types import FusedMemory


@dataclass(frozen=True)
class MemoryContext:
    """Untrusted, bounded memory evidence kept separate from system policy."""

    entries: tuple[FusedMemory, ...]
    text: str


def assemble_context(
    memories: tuple[FusedMemory, ...] | list[FusedMemory],
    *,
    max_chars: int,
) -> MemoryContext:
    parts: list[str] = []
    used = 0
    for index, memory in enumerate(memories, start=1):
        line = f"[{index}] {memory.content.strip()}"
        if not line or used + len(line) + 1 > max_chars:
            break
        parts.append(line)
        used += len(line) + 1
    text = "\n".join(parts)
    return MemoryContext(entries=tuple(memories[: len(parts)]), text=text)


def assemble_evidence_context(
    memories: tuple[FusedMemory, ...] | list[FusedMemory],
    *,
    max_chars: int,
    conflicts_detected: bool = False,
) -> MemoryContext:
    """Format selected records with bounded provenance and temporal status."""

    prefix = (
        "Evidence assessment: multiple current records conflict; state the conflict and do not "
        "guess which value is current.\n"
        if conflicts_detected
        else ""
    )
    parts: list[str] = []
    used = len(prefix)
    selected: list[FusedMemory] = []
    for index, memory in enumerate(memories, start=1):
        content = " ".join(memory.content.split())
        provenance = ",".join(memory.sources) or "unknown"
        recorded = memory.created_at.isoformat()
        validity = (
            f"valid_from={memory.valid_from.isoformat() if memory.valid_from else 'open'},"
            f"valid_to={memory.valid_to.isoformat() if memory.valid_to else 'open'}"
        )
        line = (
            f"[{index}] memory_id={memory.memory_id} source_message_id="
            f"{memory.source_message_id or 'unknown'} type={memory.memory_type.value} "
            f"status={memory.status.value} rank={memory.rank} source={provenance} "
            f"recorded_at={recorded} "
            f"{validity} content={content}"
        )
        if used + len(line) + 1 > max_chars:
            break
        parts.append(line)
        selected.append(memory)
        used += len(line) + 1
    text = prefix + "\n".join(parts)
    return MemoryContext(entries=tuple(selected), text=text)
