"""Standalone entry point for the PostgreSQL-backed reminder worker."""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.services.infrastructure import Infrastructure
from app.services.push_delivery import UnavailablePushDeliveryProvider

from .worker_service import ReminderWorkerService


async def run() -> None:
    settings = get_settings()
    infrastructure = Infrastructure(settings)
    worker = ReminderWorkerService(
        settings,
        infrastructure.database,
        UnavailablePushDeliveryProvider(),
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await worker.start()
        await stop_event.wait()
    finally:
        await worker.stop()
        await infrastructure.close()


if __name__ == "__main__":
    asyncio.run(run())
