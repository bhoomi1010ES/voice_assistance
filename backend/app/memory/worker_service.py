from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.config import Settings
from app.db.session import Database
from app.memory.providers import RemoteEmbeddingProvider

from .jobs import MemoryJobWorker

LOGGER = logging.getLogger("voice-assistance-backend")


class MemoryWorkerService:
    """Run the database-backed memory job worker within the app lifecycle."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        embedding_provider: RemoteEmbeddingProvider,
    ) -> None:
        self.settings = settings
        self.database = database
        self.worker = MemoryJobWorker(settings, embedding_provider=embedding_provider)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.database.session_factory is None:
            raise RuntimeError("memory_worker_database_unavailable")
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="memory-job-worker")
        LOGGER.info(
            "memory worker started",
            extra={
                "event": "memory.worker.started",
                "poll_interval_seconds": self.settings.memory_worker_poll_interval_seconds,
                "lease_seconds": self.settings.memory_job_lease_seconds,
                "max_attempts": self.settings.memory_job_max_attempts,
            },
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.settings.memory_worker_shutdown_timeout_seconds,
            )
        except TimeoutError:
            LOGGER.warning(
                "memory worker shutdown timed out; cancelling loop",
                extra={"event": "memory.worker.shutdown_timeout"},
            )
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None
            LOGGER.info("memory worker stopped", extra={"event": "memory.worker.stopped"})

    async def _run_loop(self) -> None:
        assert self.database.session_factory is not None
        while not self._stop_event.is_set():
            try:
                async with self.database.session_factory() as session:
                    did_work = await self.worker.run_once(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "memory worker poll failed",
                    extra={"event": "memory.worker.poll_failed"},
                )
                did_work = False
            if did_work:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.memory_worker_poll_interval_seconds,
                )
            except TimeoutError:
                pass
