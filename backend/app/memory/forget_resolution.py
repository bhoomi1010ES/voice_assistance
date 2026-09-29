"""Bounded owner-scoped candidate lookup for explicit memory-forget actions.

This is a target-resolution primitive only. It does not authorize deletion,
create a proposal, or mutate a memory. Callers must still use the existing
confirmed memory_forget tool and revalidate ownership and active status there.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryItem

_LEADING_ACTION = re.compile(
    r"^(?:please\s+)?(?:forget|delete|remove)\s+(?:that\s+|about\s+)?"
    r"(?:my\s+)?(?:saved\s+)?(?:memory\s+about\s+)?",
    re.IGNORECASE,
)
_LEADING_FACT = re.compile(r"^(?:that\s+)?(?:i\s+)?(?:prefer|like|love|hate)\s+", re.I)
_MATCH_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "i",
        "is",
        "memory",
        "my",
        "of",
        "saved",
        "that",
        "the",
        "to",
    }
)


@dataclass(frozen=True)
class ForgetResolution:
    status: str
    memory_ids: tuple[uuid.UUID, ...] = ()


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def _meaningful_tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[\w]+", value.casefold())
        if len(token) >= 2 and token not in _MATCH_STOPWORDS
    )


def _complete_phrase_match(content: str, phrase: str) -> bool:
    return bool(
        content
        and (content == phrase or content.endswith(f" {phrase}") or phrase.endswith(f" {content}"))
    )


def _grounded_lexical_match(content: str, phrase: str) -> bool:
    if _complete_phrase_match(content, phrase):
        return True
    phrase_tokens = _meaningful_tokens(phrase)
    return len(phrase_tokens) >= 2 and phrase_tokens.issubset(_meaningful_tokens(content))


def _semantic_result_is_safe(memory: Any, *, memory_service: Any) -> bool:
    sources = {str(source) for source in getattr(memory, "sources", ())}
    if "fts" in sources:
        return True
    if "dense" not in sources:
        return False
    settings = getattr(memory_service, "settings", None)
    threshold = float(getattr(settings, "memory_min_rerank_score", 0.5))
    # Raw reciprocal-rank-fusion scores are tiny. Requiring the configured
    # reranker floor prevents an arbitrary nearest neighbour from becoming
    # deletion authority when semantic reranking is unavailable.
    return float(getattr(memory, "score", 0.0)) >= threshold


def forget_description(transcript: str) -> str:
    """Strip only explicit action framing; leave the described fact grounded."""
    text = " ".join(transcript.split()).strip(" .?!")
    text = _LEADING_ACTION.sub("", text).strip(" .?!")
    return _LEADING_FACT.sub("", text).strip(" .?!")


async def resolve_forget_target(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    transcript: str,
    limit: int = 50,
    memory_service: Any | None = None,
    now: datetime | None = None,
    trace: Callable[[str, float, dict[str, Any]], None] | None = None,
) -> ForgetResolution:
    """Resolve one active owned target using grounded lexical/Hybrid RAG evidence."""
    if not 1 <= limit <= 100:
        raise ValueError("forget candidate limit must be between 1 and 100")
    phrase = _normalize(forget_description(transcript))
    if not phrase:
        return ForgetResolution("no_match")
    result_rows = await session.scalars(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id, MemoryItem.status == "active")
        .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        .limit(limit)
    )
    rows = tuple(result_rows)
    active_owned_by_id = {
        row.id: row
        for row in rows
        if row.user_id == user_id and str(row.status) == "active" and _normalize(row.content)
    }
    exact_matches = tuple(
        row.id
        for row in active_owned_by_id.values()
        if _complete_phrase_match(_normalize(row.content), phrase)
    )
    if len(exact_matches) > 1:
        return ForgetResolution("ambiguous", exact_matches[:5])

    candidate_ids = set(exact_matches) | {
        row.id
        for row in active_owned_by_id.values()
        if _grounded_lexical_match(_normalize(row.content), phrase)
    }
    if memory_service is not None:
        retrieval = await memory_service.retrieve(
            session,
            user_id=user_id,
            query=forget_description(transcript),
            now=now,
            limit=min(limit, 8),
            trace=trace,
        )
        if retrieval.status == "ready":
            retrieved_ids = {
                memory.memory_id
                for memory in retrieval.memories
                if memory.user_id == user_id
                and str(memory.status) == "active"
                and _semantic_result_is_safe(memory, memory_service=memory_service)
            }
            if retrieved_ids:
                verified_rows = await session.scalars(
                    select(MemoryItem).where(
                        MemoryItem.id.in_(retrieved_ids),
                        MemoryItem.user_id == user_id,
                        MemoryItem.status == "active",
                    )
                )
                for row in verified_rows:
                    if (
                        row.id in retrieved_ids
                        and row.user_id == user_id
                        and str(row.status) == "active"
                    ):
                        active_owned_by_id[row.id] = row
                        candidate_ids.add(row.id)
    matches = tuple(
        memory_id for memory_id in active_owned_by_id if memory_id in candidate_ids
    )
    if len(matches) == 1:
        return ForgetResolution("unique", matches)
    if len(matches) > 1:
        return ForgetResolution("ambiguous", matches[:5])
    return ForgetResolution("no_match")
