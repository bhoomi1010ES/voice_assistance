import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.llm.service import LLMService
from app.memory.providers import RemoteEmbeddingProvider, RemoteReranker
from app.memory.retrieval import MemoryRetrievalService
from app.services.infrastructure import Infrastructure
from app.stt.service import STTService


def create_app(
    settings: Settings | None = None,
    infrastructure: Infrastructure | None = None,
    stt_service: STTService | None = None,
    llm_service: LLMService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(app_settings.log_level)
        active_infrastructure = infrastructure or Infrastructure(app_settings)
        active_stt_service = stt_service or STTService(app_settings)
        active_llm_service = llm_service or LLMService(app_settings)
        embedding_provider = None
        reranker = None
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
            app.state.settings = app_settings
            app.state.infrastructure = active_infrastructure
            app.state.stt_service = active_stt_service
            app.state.llm_service = active_llm_service
            app.state.memory_service = memory_service
            logging.getLogger("voice-assistance-backend").info(
                "application started",
                extra={"event": "service.started"},
            )
            yield
        finally:
            try:
                await active_llm_service.close()
            finally:
                try:
                    await active_stt_service.close()
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
