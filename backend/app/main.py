import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.llm.service import LLMService
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
        try:
            await active_stt_service.initialize()
            await active_llm_service.initialize()
            app.state.settings = app_settings
            app.state.infrastructure = active_infrastructure
            app.state.stt_service = active_stt_service
            app.state.llm_service = active_llm_service
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
