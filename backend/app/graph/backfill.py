from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import MemoryItem, User

from .indexing import GraphIndexingService
from .policy import GRAPH_INDEX_POLICY_VERSION
from .types import GraphBackfillResult

_DEFAULT_BATCH_SIZE = 100
_MAX_BATCH_SIZE = 1_000


class GraphBackfillService:
    """Restartable deterministic graph indexing over active owned memories.

    The service deliberately does not commit and never updates a memory row.
    Callers commit each bounded batch after the graph writes succeed and resume
    with ``next_cursor``. The cursor is the last scanned memory UUID, so rows
    are never re-read unless a caller intentionally restarts from ``None``;
    replaying a batch is safe because graph writes are idempotent.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        graph_indexer: GraphIndexingService | None = None,
    ) -> None:
        self.settings = settings
        self.graph_indexer = graph_indexer or GraphIndexingService(settings)

    async def run_batch(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        after_memory_id: uuid.UUID | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        policy_version: str = GRAPH_INDEX_POLICY_VERSION,
    ) -> GraphBackfillResult:
        if not isinstance(user_id, uuid.UUID):
            raise ValueError("graph backfill user_id must be a UUID")
        if after_memory_id is not None and not isinstance(after_memory_id, uuid.UUID):
            raise ValueError("graph backfill cursor must be a UUID")
        if not 1 <= batch_size <= _MAX_BATCH_SIZE:
            raise ValueError("graph backfill batch size must be between 1 and 1000")
        if policy_version != GRAPH_INDEX_POLICY_VERSION:
            raise ValueError("unsupported graph backfill policy version")

        if not self.settings.graph_write_enabled:
            return self._result(
                user_id=user_id,
                scanned=0,
                indexed=0,
                already_indexed=0,
                skipped=0,
                next_cursor=after_memory_id,
                complete=True,
            )

        memory_enabled = await session.scalar(
            select(User.memory_enabled).where(User.id == user_id)
        )
        if memory_enabled is not True:
            return self._result(
                user_id=user_id,
                scanned=0,
                indexed=0,
                already_indexed=0,
                skipped=0,
                next_cursor=after_memory_id,
                complete=True,
            )

        query = (
            select(MemoryItem.id)
            .where(MemoryItem.user_id == user_id, MemoryItem.status == "active")
            .order_by(MemoryItem.id.asc())
            .limit(batch_size)
        )
        if after_memory_id is not None:
            query = query.where(MemoryItem.id > after_memory_id)
        memory_ids = tuple((await session.scalars(query)).all())
        if not memory_ids:
            return self._result(
                user_id=user_id,
                scanned=0,
                indexed=0,
                already_indexed=0,
                skipped=0,
                next_cursor=after_memory_id,
                complete=True,
            )

        indexed = already_indexed = skipped = 0
        for memory_id in memory_ids:
            result = await self.graph_indexer.index_memory(
                session,
                user_id=user_id,
                memory_id=memory_id,
                policy_version=policy_version,
            )
            if result.status == "indexed":
                indexed += 1
            elif result.status == "already_indexed":
                already_indexed += 1
            else:
                skipped += 1

        return self._result(
            user_id=user_id,
            scanned=len(memory_ids),
            indexed=indexed,
            already_indexed=already_indexed,
            skipped=skipped,
            next_cursor=memory_ids[-1],
            complete=len(memory_ids) < batch_size,
        )

    @staticmethod
    def _result(
        *,
        user_id: uuid.UUID,
        scanned: int,
        indexed: int,
        already_indexed: int,
        skipped: int,
        next_cursor: uuid.UUID | None,
        complete: bool,
    ) -> GraphBackfillResult:
        return GraphBackfillResult(
            user_id=user_id,
            scanned=scanned,
            indexed=indexed,
            already_indexed=already_indexed,
            skipped=skipped,
            next_cursor=next_cursor,
            complete=complete,
        )

