"""Bounded owner-scoped candidate lookup for explicit memory-forget actions.

This is a target-resolution primitive only. It does not authorize deletion,
create a proposal, or mutate a memory. Callers must still use the existing
confirmed memory_forget tool and revalidate ownership and active status there.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryItem

_LEADING_ACTION = re.compile(
    r"^(?:please\s+)?(?:forget|delete|remove)\s+(?:that\s+)?(?:my\s+)?(?:saved\s+)?(?:memory\s+about\s+)?",
    re.IGNORECASE,
)
_LEADING_FACT = re.compile(r"^(?:that\s+)?(?:i\s+)?(?:prefer|like|love|hate)\s+", re.I)


@dataclass(frozen=True)
class ForgetResolution:
    status: str
    memory_ids: tuple[uuid.UUID, ...] = ()


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


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
) -> ForgetResolution:
    """Return a UUID only for one exact normalized phrase in active owned rows.

    A broad fuzzy match would turn relevance into deletion authority, so this
    resolver deliberately abstains unless the complete described phrase
    matches a stored content string in either direction.
    """
    if not 1 <= limit <= 100:
        raise ValueError("forget candidate limit must be between 1 and 100")
    phrase = _normalize(forget_description(transcript))
    if not phrase:
        return ForgetResolution("no_match")
    rows = await session.scalars(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id, MemoryItem.status == "active")
        .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        .limit(limit)
    )
    matches = tuple(
        row.id
        for row in rows
        if row.user_id == user_id
        and row.status == "active"
        and (content := _normalize(row.content))
        and (content == phrase or content.endswith(f" {phrase}") or phrase.endswith(f" {content}"))
    )
    if len(matches) == 1:
        return ForgetResolution("unique", matches)
    if len(matches) > 1:
        return ForgetResolution("ambiguous", matches[:5])
    return ForgetResolution("no_match")
