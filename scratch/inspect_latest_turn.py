from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import ConversationTurn, Task


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            turn = await session.scalar(
                select(ConversationTurn).order_by(ConversationTurn.created_at.desc())
            )
            print("LATEST_TURN")
            if turn is None:
                print(None)
            else:
                print(
                    {
                        "id": str(turn.id),
                        "status": turn.status,
                        "turn_number": turn.turn_number,
                        "created_at": turn.created_at.isoformat(),
                        "metadata": turn.metadata_json,
                    }
                )
            tasks = list(
                (
                    await session.scalars(
                        select(Task).order_by(Task.created_at.desc()).limit(5)
                    )
                ).all()
            )
            print("RECENT_TASKS")
            for task in tasks:
                print(
                    {
                        "id": str(task.id),
                        "title": task.title,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "created_at": task.created_at.isoformat(),
                    }
                )
    finally:
        await engine.dispose()


asyncio.run(main())
