from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api import auth, devices, memories, reminders, sessions, tasks, voice, websocket

router = APIRouter()

router.include_router(auth.router)
router.include_router(devices.router)
router.include_router(memories.router)
router.include_router(tasks.router)
router.include_router(reminders.router)
router.include_router(sessions.router)
router.include_router(websocket.router)
router.include_router(voice.router)


@router.get("/health")
async def health() -> dict[str, str]:
    """Return process health without requiring external dependencies."""

    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Return dependency and configured-provider readiness."""

    dependency_status = await request.app.state.infrastructure.check_readiness()
    result = {
        **dependency_status,
        "dependencies": dict(dependency_status.get("dependencies", {})),
    }
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and settings.okf_enabled and settings.okf_sync_enabled:
        if settings.okf_worker_mode == "in_process":
            worker = getattr(request.app.state, "okf_worker", None)
            worker_running = bool(worker is not None and worker.running)
            result["dependencies"]["okf_sync_worker"] = {
                "enabled": True,
                "mode": "in_process",
                "status": "ready" if worker_running else "not_ready",
            }
            if not worker_running:
                result["status"] = "not_ready"
        else:
            result["dependencies"]["okf_sync_worker"] = {
                "enabled": True,
                "mode": "standalone",
                "status": "external",
            }
    llm_status = request.app.state.llm_service.readiness()
    if llm_status["enabled"]:
        result["dependencies"]["llm"] = llm_status
        if llm_status["status"] != "ready":
            result["status"] = "not_ready"
    tts_service = getattr(request.app.state, "tts_service", None)
    if tts_service is not None and getattr(request.app.state, "settings", None) is not None:
        info = getattr(tts_service, "info", None)
        enabled = bool(info is not None and info.enabled)
        if enabled or request.app.state.settings.tts_api_url:
            result["dependencies"]["tts"] = {
                "enabled": enabled,
                "status": "ready" if enabled else "not_ready",
                "provider": info.provider if info is not None else None,
                "model": info.model if info is not None else None,
            }
            if not enabled:
                result["status"] = "not_ready"
    memory_service = getattr(request.app.state, "memory_service", None)
    if memory_service is not None:
        memory_status = await memory_service.readiness()
        if memory_status["enabled"]:
            result["dependencies"]["memory"] = memory_status
            if memory_status["status"] != "ready":
                result["status"] = "not_ready"
    router_service = getattr(request.app.state, "router_decision_service", None)
    if router_service is not None:
        router_status = await router_service.readiness()
        if router_status["enabled"]:
            result["dependencies"]["router"] = router_status
            if router_status["status"] != "ready":
                result["status"] = "not_ready"
    status_code = 200 if result["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=result)
