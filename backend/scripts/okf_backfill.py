"""Explicitly enqueue a resumable, owner-scoped OKF memory backfill.

This command never runs at application startup. Resume with the last printed
``next_after_memory_id`` value if the process stops between batches.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

async def run(args: argparse.Namespace) -> None:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.session import Database
    from app.models import MemoryItem, User
    from app.okf.jobs import enqueue_memory_sync

    settings = get_settings()
    if not settings.okf_enabled or not settings.okf_sync_enabled:
        raise SystemExit("Set OKF_ENABLED=true and OKF_SYNC_ENABLED=true before backfill.")
    database = Database(settings)
    if database.session_factory is None:
        raise SystemExit("DATABASE_URL is required for OKF backfill.")
    cursor = args.after_memory_id
    try:
        while True:
            async with database.session_factory() as session, session.begin():
                owner = await session.scalar(
                    select(User).where(User.id == args.user_id, User.memory_enabled.is_(True))
                )
                if owner is None:
                    raise SystemExit("User does not exist or memory is disabled.")
                query = select(MemoryItem.id).where(
                    MemoryItem.user_id == args.user_id,
                    MemoryItem.status == "active",
                    MemoryItem.source_session_id.is_not(None),
                )
                if cursor is not None:
                    query = query.where(MemoryItem.id > cursor)
                memory_ids = tuple(
                    (
                        await session.scalars(
                            query.order_by(MemoryItem.id).limit(args.batch_size)
                        )
                    ).all()
                )
                for memory_id in memory_ids:
                    await enqueue_memory_sync(
                        session,
                        user_id=args.user_id,
                        memory_id=memory_id,
                        policy_version=settings.okf_policy_version,
                    )
            if not memory_ids:
                print(f"backfill complete; next_after_memory_id={cursor or 'none'}")
                return
            cursor = memory_ids[-1]
            print(f"enqueued={len(memory_ids)} next_after_memory_id={cursor}")
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, type=uuid.UUID)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--after-memory-id", type=uuid.UUID)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 2_000:
        parser.error("--batch-size must be between 1 and 2000")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
