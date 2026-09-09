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
