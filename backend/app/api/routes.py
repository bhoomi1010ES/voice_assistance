from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api import auth, devices, memories, sessions, tasks, voice, websocket

router = APIRouter()

router.include_router(auth.router)
router.include_router(devices.router)
router.include_router(memories.router)
router.include_router(tasks.router)
router.include_router(sessions.router)
router.include_router(websocket.router)
router.include_router(voice.router)


@router.get("/health")
async def health() -> dict[str, str]:
    """Return process health without requiring external dependencies."""

    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Return readiness based on PostgreSQL and Redis connectivity."""

    dependency_status = await request.app.state.infrastructure.check_readiness()
    status_code = 200 if dependency_status["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=dependency_status)
