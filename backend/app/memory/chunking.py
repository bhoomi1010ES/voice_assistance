from __future__ import annotations


def chunk_text(text: str, *, max_chars: int = 1_600, overlap_chars: int = 160) -> tuple[str, ...]:
    """Deterministically chunk text on word boundaries with bounded overlap."""

    if max_chars < 128 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("invalid memory chunk bounds")
    normalized = " ".join(text.split())
    if not normalized:
        return ()
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        next_start = max(start + 1, end - overlap_chars)
        while next_start < len(normalized) and normalized[next_start] == " ":
            next_start += 1
        start = next_start
    return tuple(chunks)
