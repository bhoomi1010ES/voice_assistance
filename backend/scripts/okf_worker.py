"""Run the dedicated OKF synchronization worker as a standalone process."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


async def run() -> None:
    from app.core.config import get_settings
    from app.db.session import Database
    from app.okf.worker_service import OkfWorkerService

    settings = get_settings()
    if not settings.okf_enabled or not settings.okf_sync_enabled:
        raise SystemExit("Set OKF_ENABLED=true and OKF_SYNC_ENABLED=true to run the worker.")
    database = Database(settings)
    service = OkfWorkerService(settings, database)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:  # Windows Proactor event loops
            signal.signal(sig, lambda *_args: loop.call_soon_threadsafe(stopped.set))
    try:
        await service.start()
        await stopped.wait()
    finally:
        await service.stop()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(run())
