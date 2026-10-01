import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.llm.service import LLMService
from app.memory.providers import RemoteEmbeddingProvider, RemoteReranker
from app.memory.retrieval import MemoryRetrievalService
from app.memory.worker_service import MemoryWorkerService
from app.okf.shadow import ShadowReadCapacity
from app.okf.worker_service import OkfWorkerService
from app.reminders.worker_service import ReminderWorkerService
from app.routing.service import DecisionRouterService
from app.services.infrastructure import Infrastructure
from app.services.push_delivery import UnavailablePushDeliveryProvider
from app.stt.service import STTService
from app.tts.service import TTSService


def create_app(
    settings: Settings | None = None,
    infrastructure: Infrastructure | None = None,
    stt_service: STTService | None = None,
    llm_service: LLMService | None = None,
    tts_service: TTSService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(app_settings.log_level)
        router_service = DecisionRouterService(
            app_settings,
            shadow_semaphore=asyncio.Semaphore(app_settings.router_shadow_max_concurrent),
        )
        app.state.router_decision_service = router_service
        app.state.okf_shadow_capacity = ShadowReadCapacity(
            app_settings.okf_shadow_max_concurrent
        )
        if router_service.is_active():
            warm_started = time.perf_counter()
            warmed = router_service.warm_graph()
            logging.getLogger("voice-assistance-backend").info(
                "Router graph startup warmup completed",
                extra={
                    "event": "router.warmup",
                    "router_mode": app_settings.router_mode,
                    "ready": warmed,
                    "duration_ms": round((time.perf_counter() - warm_started) * 1000, 3),
                },
            )
        active_infrastructure = infrastructure or Infrastructure(app_settings)
        active_stt_service = stt_service or STTService(app_settings)
        active_llm_service = llm_service or LLMService(app_settings)
        active_tts_service = tts_service or TTSService(app_settings)
        embedding_provider = None
        reranker = None
        memory_worker = None
        reminder_worker = None
        okf_worker = None
        if app_settings.memory_retrieval_mode != "off" or app_settings.memory_write_enabled:
            embedding_provider = RemoteEmbeddingProvider(app_settings)
            await embedding_provider.initialize()
        if app_settings.memory_retrieval_mode != "off":
            reranker = RemoteReranker(app_settings)
            await reranker.initialize()
        memory_service = MemoryRetrievalService(
            app_settings,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
        try:
            await active_stt_service.initialize()
            await active_llm_service.initialize()
            await active_tts_service.initialize()
            app.state.settings = app_settings
            app.state.infrastructure = active_infrastructure
            app.state.stt_service = active_stt_service
            app.state.llm_service = active_llm_service
            app.state.tts_service = active_tts_service
            app.state.memory_service = memory_service
            if app_settings.memory_write_enabled:
                if embedding_provider is None:
                    raise RuntimeError("memory_worker_embedding_provider_unavailable")
                memory_worker = MemoryWorkerService(
                    app_settings,
                    active_infrastructure.database,
                    embedding_provider,
                )
                await memory_worker.start()
                app.state.memory_worker = memory_worker
            database = getattr(active_infrastructure, "database", None)
            if (
                app_settings.okf_enabled
                and app_settings.okf_sync_enabled
                and app_settings.okf_worker_mode == "in_process"
                and getattr(database, "session_factory", None)
            ):
                okf_worker = OkfWorkerService(app_settings, database)
                await okf_worker.start()
                app.state.okf_worker = okf_worker
            if app_settings.reminder_worker_enabled and getattr(database, "session_factory", None):
                reminder_worker = ReminderWorkerService(
                    app_settings,
                    database,
                    UnavailablePushDeliveryProvider(),
                )
                await reminder_worker.start()
                app.state.reminder_worker = reminder_worker
            logging.getLogger("voice-assistance-backend").info(
                "application started",
                extra={"event": "service.started"},
            )
            yield
        finally:
            try:
                if reminder_worker is not None:
                    await reminder_worker.stop()
                if okf_worker is not None:
                    await okf_worker.stop()
                if memory_worker is not None:
                    await memory_worker.stop()
            finally:
                try:
                    await active_llm_service.close()
                finally:
                    try:
                        await active_stt_service.close()
                    finally:
                        try:
                            await active_tts_service.close()
                        finally:
                            try:
                                if reranker is not None:
                                    await reranker.close()
                                if embedding_provider is not None:
                                    await embedding_provider.close()
                            finally:
                                await active_infrastructure.close()
            logging.getLogger("voice-assistance-backend").info(
                "application stopped",
                extra={"event": "service.stopped"},
            )

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(router)
    return app


app = create_app()
