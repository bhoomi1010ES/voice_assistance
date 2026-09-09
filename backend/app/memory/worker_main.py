"""Production entry point for the database-backed Phase 6 memory worker."""

from __future__ import annotations

import asyncio
import logging
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.memory.providers import RemoteEmbeddingProvider
from app.memory.worker_service import MemoryWorkerService
from app.services.infrastructure import Infrastructure


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.memory_write_enabled:
        raise RuntimeError("MEMORY_WRITE_ENABLED must be true for the memory worker")
    infrastructure = Infrastructure(settings)
    provider = RemoteEmbeddingProvider(settings)
    await provider.initialize()
    worker = MemoryWorkerService(settings, infrastructure.database, provider)
    try:
        postgres = await infrastructure.check_postgres()
        if postgres["status"] != "ok":
            raise RuntimeError("memory_worker_postgres_unavailable")
        await worker.start()
        logging.getLogger("voice-assistance-backend").info(
            "memory worker process ready",
            extra={"event": "memory.worker.process_ready"},
        )
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(stop_signal, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
    finally:
        await worker.stop()
        await provider.close()
        await infrastructure.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
