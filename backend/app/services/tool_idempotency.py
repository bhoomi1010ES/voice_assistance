from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.tool_loop import (
    IdempotencyKey,
    ToolIdempotencyClaim,
    ToolIdempotencyStore,
)
from app.models import ToolExecutionRecord


class PostgresToolIdempotencyStore(ToolIdempotencyStore):
    """Use the existing PostgreSQL transaction as the tool idempotency boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, key: IdempotencyKey) -> ToolIdempotencyClaim:
        user_id, turn_id, tool_name, tool_call_id = key
        statement = (
            postgres_insert(ToolExecutionRecord)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                turn_id=turn_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="started",
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "turn_id", "tool_name", "tool_call_id"]
            )
            .returning(ToolExecutionRecord.id)
        )
        inserted_id = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            return ToolIdempotencyClaim(acquired=True)

        record = await self._record(key)
        if record is not None and record.status == "completed":
            return ToolIdempotencyClaim(
                acquired=False,
                cached_content=record.result_content,
            )
        return ToolIdempotencyClaim(acquired=False)

    async def get(self, key: IdempotencyKey) -> str | None:
        record = await self._record(key)
        if record is None or record.status != "completed":
            return None
        return record.result_content

    async def put(self, key: IdempotencyKey, content: str) -> None:
        record = await self._record(key)
        if record is None:
            raise RuntimeError("Tool idempotency record was not claimed")
        record.status = "completed"
        record.result_content = content
        await self.session.flush()

    async def release(self, key: IdempotencyKey) -> None:
        user_id, turn_id, tool_name, tool_call_id = key
        await self.session.execute(
            delete(ToolExecutionRecord).where(
                ToolExecutionRecord.user_id == user_id,
                ToolExecutionRecord.turn_id == turn_id,
                ToolExecutionRecord.tool_name == tool_name,
                ToolExecutionRecord.tool_call_id == tool_call_id,
                ToolExecutionRecord.status == "started",
            )
        )
        await self.session.flush()

    async def _record(self, key: IdempotencyKey) -> ToolExecutionRecord | None:
        user_id, turn_id, tool_name, tool_call_id = key
        return await self.session.scalar(
            select(ToolExecutionRecord).where(
                ToolExecutionRecord.user_id == user_id,
                ToolExecutionRecord.turn_id == turn_id,
                ToolExecutionRecord.tool_name == tool_name,
                ToolExecutionRecord.tool_call_id == tool_call_id,
            )
        )
