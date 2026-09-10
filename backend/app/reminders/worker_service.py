from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.config import Settings
from app.db.session import Database
from app.services.push_delivery import PushDeliveryProvider

from .worker import ReminderWorker

LOGGER = logging.getLogger("voice-assistance-backend")


class ReminderWorkerService:
    """Application-lifecycle wrapper around the durable reminder worker."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        provider: PushDeliveryProvider,
    ) -> None:
        self.settings = settings
        self.database = database
        self.worker = ReminderWorker(settings, provider=provider)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.database.session_factory is None:
            raise RuntimeError("reminder_worker_database_unavailable")
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="reminder-worker")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.settings.reminder_worker_shutdown_timeout_seconds,
            )
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None

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
                    "reminder worker poll failed", extra={"event": "reminder.worker.poll_failed"}
                )
                did_work = False
            if did_work:
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.reminder_worker_poll_interval_seconds,
                )
            except TimeoutError:
                pass
