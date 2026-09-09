import asyncio
import uuid

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.llm.tool_loop import ToolExecutionContext, ToolExecutor, ToolRegistry
from app.memory.tool_tools import build_explicit_memory_save_call, register_memory_tools
from app.models import ConversationTurn, User
from app.services.tool_idempotency import PostgresToolIdempotencyStore


async def main() -> None:
    settings = Settings()
    database = Database(settings)
    assert database.session_factory is not None
    async with database.session_factory() as session:
        user = await session.scalar(select(User).where(User.name == "Test1"))
        assert user is not None
        turn_id = await session.scalar(
            select(ConversationTurn.id)
            .where(ConversationTurn.user_id == user.id)
            .order_by(ConversationTurn.created_at.desc())
        )
        assert turn_id is not None
        call = build_explicit_memory_save_call(
            "Remember that diagnostic transaction rollback",
            turn_id=turn_id,
        )
        assert call is not None
        context = ToolExecutionContext(
            user_id=user.id,
            session_id=uuid.uuid4(),
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            scopes=frozenset({"memory:write"}),
            confirmed_tool_call_ids=frozenset({call.tool_call_id}),
            db=session,
            memory_settings=settings,
        )
        registry = ToolRegistry()
        register_memory_tools(registry, allow_write=True)
        result = await ToolExecutor(
            registry,
            idempotency_store=PostgresToolIdempotencyStore(session),
        ).execute(
            call,
            context=context,
        )
        print(f"executor_success={result.success}")
        print(f"executor_error={result.error_code}")
        print(f"executor_executed={result.executed}")
        await session.rollback()
    await database.dispose()


asyncio.run(main())
