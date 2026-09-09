import asyncio

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.models import ConversationTurn, MemoryItem, Message, Task


async def main() -> None:
    database = Database(Settings())
    assert database.session_factory is not None
    async with database.session_factory() as session:
        tasks = (
            await session.execute(
                select(Task.title, Task.due_at, Task.status, Task.created_at)
                .where(Task.title.ilike("%Rahul%"))
                .order_by(Task.created_at.desc())
                .limit(5)
            )
        ).all()
        memories = (
            await session.execute(
                select(
                    MemoryItem.content,
                    MemoryItem.memory_type,
                    MemoryItem.status,
                    MemoryItem.created_at,
                )
                .where(MemoryItem.content.ilike("%online meeting%"))
                .order_by(MemoryItem.created_at.desc())
                .limit(5)
            )
        ).all()
        turns = (
            await session.execute(
                select(
                    ConversationTurn.id,
                    ConversationTurn.status,
                    ConversationTurn.metadata_json,
                    ConversationTurn.created_at,
                )
                .order_by(ConversationTurn.created_at.desc())
                .limit(4)
            )
        ).all()
        messages = (
            await session.execute(
                select(Message.turn_id, Message.role, Message.content_json, Message.created_at)
                .order_by(Message.created_at.desc())
                .limit(8)
            )
        ).all()
        print(f"matching_tasks={len(tasks)}")
        for task in tasks:
            print(tuple(task))
        print(f"matching_memories={len(memories)}")
        for memory in memories:
            print(tuple(memory))
        print("recent_turns=")
        for turn in turns:
            print(tuple(turn))
        print("recent_messages=")
        for message in messages:
            print(tuple(message))
    await database.dispose()


asyncio.run(main())
