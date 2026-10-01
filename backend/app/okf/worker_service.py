from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.models import MemoryItem, OkfSyncJob

from .jobs import OkfSyncWorker

LOGGER = logging.getLogger("voice-assistance-backend")


class OkfWorkerService:
    """Run OKF sync work out-of-band from HTTP and voice response handlers."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.worker = OkfSyncWorker(settings)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.database.session_factory is None:
            raise RuntimeError("okf_worker_database_unavailable")
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="okf-sync-worker")
        LOGGER.info("OKF sync worker started", extra={"event": "okf.worker.started"})

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(
                asyncio.shield(self._task),
                timeout=self.settings.okf_worker_shutdown_timeout_seconds,
            )
        except TimeoutError:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def run_once(self) -> bool:
        factory = self.database.session_factory
        if factory is None:
            raise RuntimeError("okf_worker_database_unavailable")
        async with factory() as session:
            job_id = await self.worker.claim(session)
            await session.commit()
        if job_id is None:
            return False
        sync_metric: dict[str, object] | None = None
        try:
            async with factory() as session:
                async with session.begin():
                    job = await session.get(OkfSyncJob, job_id)
                    if job is None:
                        return True
                    source_created_at = None
                    if job.event_type == "upsert_memory" and job.memory_id is not None:
                        source_created_at = await session.scalar(
                            select(MemoryItem.created_at).where(
                                MemoryItem.id == job.memory_id,
                                MemoryItem.user_id == job.user_id,
                            )
                        )
                    await self.worker.process(session, job_id=job_id)
                    if (
                        job.event_type == "upsert_memory"
                        and job.status == "completed"
                        and source_created_at is not None
                        and job.completed_at is not None
                    ):
                        sync_metric = {
                            "event_type": job.event_type,
                            "status": job.status,
                            "attempt": job.attempts,
                            "sync_lag_ms": round(
                                max(
                                    0.0,
                                    (job.completed_at - source_created_at).total_seconds()
                                    * 1_000,
                                ),
                                3,
                            ),
                        }
            if sync_metric is not None:
                LOGGER.info(
                    "OKF source became available",
                    extra={"event": "okf.sync.metric", **sync_metric},
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Store only a stable code/class, never source content or exception text.
            code = f"worker_{type(error).__name__.lower()}"
            async with factory() as session, session.begin():
                job = await session.scalar(
                    select(OkfSyncJob).where(OkfSyncJob.id == job_id).with_for_update()
                )
                if job is not None and job.status == "running":
                    self.worker.fail(job, code)
            LOGGER.exception("OKF sync job failed", extra={"event": "okf.worker.job_failed"})
        return True

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "OKF worker poll failed", extra={"event": "okf.worker.poll_failed"}
                )
                did_work = False
            if did_work:
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.okf_worker_poll_interval_seconds
                )
            except TimeoutError:
                pass
