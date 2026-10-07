"""Administrative / migration script to requeue reminders that failed due to push readiness.

Usage:
    # Inspect what would be requeued without modifying the database:
    python scripts/requeue_failed_push_reminders.py --dry-run

    # Requeue all eligible future reminders across all users:
    python scripts/requeue_failed_push_reminders.py

    # Requeue for a specific user:
    python scripts/requeue_failed_push_reminders.py --user-id <UUID>

SQL Equivalent for direct PostgreSQL admin execution:
------------------------------------------------------
UPDATE reminders
SET
    status = 'scheduled',
    next_attempt_at = trigger_at,
    failure_code = NULL,
    failure_reason = NULL,
    locked_at = NULL,
    locked_by = NULL,
    lease_expires_at = NULL,
    dead_lettered_at = NULL,
    attempt_count = 0,
    updated_at = NOW()
WHERE
    status = 'failed'
    AND failure_code IN ('push_provider_unconfigured', 'no_active_push_device')
    AND trigger_at > NOW();
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


async def run(args: argparse.Namespace) -> None:
    from app.core.config import get_settings
    from app.db.session import Database
    from app.reminders.requeue import requeue_failed_push_reminders

    settings = get_settings()
    database = Database(settings)
    if database.session_factory is None:
        raise SystemExit("DATABASE_URL is required to run the requeue script.")

    try:
        async with database.session_factory() as session:
            result = await requeue_failed_push_reminders(
                session,
                user_id=args.user_id,
                dry_run=args.dry_run,
            )

        mode_str = "DRY-RUN (no changes committed)" if args.dry_run else "EXECUTED"
        print(f"[{mode_str}] Requeue completed:")
        print(f"  Scanned candidates:     {result.scanned_count}")
        print(f"  Requeued to scheduled:  {result.requeued_count}")
        print(f"  Skipped (past-due):     {result.skipped_past_due_count}")
        if result.requeued_reminder_ids:
            print("  Requeued reminder IDs:")
            for reminder_id in result.requeued_reminder_ids:
                print(f"    - {reminder_id}")
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect eligible reminders without committing changes.",
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="Optional UUID filter to restrict requeue to a single user.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
