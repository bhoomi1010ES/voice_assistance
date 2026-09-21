from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.websocket.gateway import VoiceGateway
from app.websocket.state import VoiceConnectionState


def _shutdown_gateway(*, finalize_error: Exception | None = None) -> VoiceGateway:
    session_id = uuid.uuid4()
    gateway = object.__new__(VoiceGateway)
    gateway.settings = Settings(_env_file=None, app_env="test")
    gateway._finalized = False
    gateway._registry_released = False
    gateway._connection_close_logged = False
    gateway._closing = asyncio.Event()
    gateway._processor_task = None
    gateway._watchdog_task = None
    gateway._receive_task = None
    gateway._retry_response_task = None
    gateway._tts_tasks = {}
    gateway._stt_finalize_task = None
    gateway.stt_turn = None
    gateway.active_turn = None
    gateway.voice_session = SimpleNamespace(id=session_id)
    gateway._session_id = session_id
    gateway._session_status = "completed"
    gateway._close_code = 1000
    gateway._close_reason = "user_stopped"
    gateway._session_total_frames = 0
    gateway._session_total_bytes = 0
    gateway.stats = SimpleNamespace(
        error_count=0,
        frames_accepted=0,
        queue_high_water_mark=0,
        queue_overflow_count=0,
    )
    gateway.owner = object()
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4(), device_id=uuid.uuid4())
    gateway.websocket = object()
    gateway.confirmation_store = None
    gateway.state = VoiceConnectionState()
    gateway.state.authenticate()
    gateway.state.session_ready(session_id)
    gateway.registry = SimpleNamespace(release=AsyncMock())
    gateway.db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    finalize = AsyncMock()
    if finalize_error is not None:
        finalize.side_effect = finalize_error
    gateway.persistence = SimpleNamespace(finalize_session=finalize)
    gateway._send = AsyncMock()
    gateway._close_stt_turn = AsyncMock()
    gateway._finalize_active_turn = AsyncMock()
    gateway._cancel_stt_finalize_task = AsyncMock()
    gateway._confirmation_scope = lambda session_id: session_id
    return gateway


@pytest.mark.asyncio
async def test_shutdown_releases_registry_before_session_finalize() -> None:
    gateway = _shutdown_gateway()
    order: list[str] = []

    async def release(*_args):
        order.append("release")

    async def finalize(*_args, **_kwargs):
        order.append("finalize")

    gateway.registry.release = release
    gateway.persistence.finalize_session = finalize

    await gateway.shutdown()

    assert order[0] == "release"
    assert "finalize" in order
    assert gateway._registry_released is True


@pytest.mark.asyncio
async def test_shutdown_releases_registry_when_session_finalize_fails() -> None:
    gateway = _shutdown_gateway(finalize_error=RuntimeError("session rolled back"))

    await gateway.shutdown()

    gateway.registry.release.assert_awaited()
    gateway.db.rollback.assert_awaited()
    assert gateway._registry_released is True
    assert gateway.state.state.value == "CLOSED"


@pytest.mark.asyncio
async def test_session_end_releases_registry_immediately() -> None:
    gateway = _shutdown_gateway()
    gateway._require_session = lambda: None
    from app.websocket.protocol import SessionEndMessage

    await gateway._handle_session_end(
        SessionEndMessage(type="client.session.end", reason="user_stopped")
    )

    gateway.registry.release.assert_awaited()
    assert gateway._closing.is_set()
    assert gateway._registry_released is True
