from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.websocket.cancellation import CancellationGuard
from app.websocket.gateway import VoiceGateway, VoiceGatewayStats
from app.websocket.protocol import ResponseCancelMessage, TurnStartMessage
from app.websocket.state import VoiceConnectionState


class _Persistence:
    def __init__(self, turn):
        self.turn = turn

    async def create_turn(self, *args, **kwargs):
        return self.turn

    async def merge_turn_metadata(self, *args, **kwargs):
        return None


class _Registry:
    def __init__(self):
        self.set_turn_calls = []
        self.cancel_response_calls = []
        self.clear_response = AsyncMock()

    async def set_turn(self, *args):
        self.set_turn_calls.append(args)

    async def cancel_response(self, *args):
        self.cancel_response_calls.append(args)
        return True

    async def refresh(self, *args):
        return None


def _gateway(*, old_turn_id: uuid.UUID, old_response_id: uuid.UUID, new_turn_id: uuid.UUID):
    session_id = uuid.uuid4()
    turn = SimpleNamespace(id=new_turn_id, turn_number=2, response_id=uuid.uuid4())
    gateway = object.__new__(VoiceGateway)
    gateway.settings = Settings(_env_file=None, app_env="test")
    gateway.voice_session = SimpleNamespace(client_metadata={}, total_frames=0, total_bytes=0)
    gateway._session_id = session_id
    gateway.state = VoiceConnectionState()
    gateway.state.authenticate()
    gateway.state.session_ready(session_id, completed_turns=1)
    gateway.active_turn = None
    gateway.stt_turn = None
    gateway._stt_event_task = None
    gateway._stt_finalize_task = None
    gateway._stt_enabled = False
    gateway._stt_language = None
    gateway._last_response_id = old_response_id
    gateway._response_turn_id = old_turn_id
    gateway._turn_started = None
    gateway._pending_turn_start = None
    gateway._turn_start_event_ids = set()
    gateway._cancelled_response_ids = set()
    gateway._turn_timings = {}
    gateway._tts_tasks = {}
    gateway._stt_finalize_cancel_requested = False
    gateway.cancel_guard = CancellationGuard()
    gateway.cancel_guard.activate(old_response_id)
    gateway.stats = VoiceGatewayStats()
    gateway.queue = SimpleNamespace(qsize=lambda: 0)
    gateway.owner = object()
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.persistence = _Persistence(turn)
    gateway.registry = _Registry()
    gateway.db = SimpleNamespace(commit=AsyncMock())
    gateway.llm_service = SimpleNamespace(
        provider_info=None,
        cancel=AsyncMock(return_value=True),
    )
    gateway._cancel_tts_response = AsyncMock()
    gateway._persist_conversation_log = AsyncMock()
    gateway._send = AsyncMock()
    return gateway, turn


@pytest.mark.asyncio
async def test_barge_in_cancel_then_turn_start_creates_one_fresh_turn() -> None:
    old_turn_id = uuid.uuid4()
    old_response_id = uuid.uuid4()
    gateway, new_turn = _gateway(
        old_turn_id=old_turn_id,
        old_response_id=old_response_id,
        new_turn_id=uuid.uuid4(),
    )
    pending = TurnStartMessage(type="client.turn.start")
    await gateway._handle_turn_start(pending)
    assert gateway._pending_turn_start is pending

    await gateway._handle_response_cancel(
        ResponseCancelMessage(
            type="client.response.cancel",
            response_id=old_response_id,
            reason="barge_in",
        )
    )

    assert gateway.stats.cancellation_count == 1
    assert gateway.active_turn is new_turn
    assert new_turn.id != old_turn_id
    assert new_turn.response_id != old_response_id
    assert gateway._pending_turn_start is None
    assert len(gateway.registry.set_turn_calls) == 1
    assert [event.args[0]["type"] for event in gateway._send.await_args_list] == [
        "response.cancelled",
        "server.turn.ready",
    ]


@pytest.mark.asyncio
async def test_turn_start_before_cancel_is_pending_and_duplicate_is_ignored() -> None:
    old_turn_id = uuid.uuid4()
    old_response_id = uuid.uuid4()
    gateway, _ = _gateway(
        old_turn_id=old_turn_id,
        old_response_id=old_response_id,
        new_turn_id=uuid.uuid4(),
    )
    message = TurnStartMessage(type="client.turn.start")

    await gateway._handle_turn_start(message)
    await gateway._handle_turn_start(message)

    assert gateway._pending_turn_start is message
    assert gateway.persistence.turn.id != old_turn_id
    assert gateway.registry.set_turn_calls == []
    gateway._response_turn_id = None
    await gateway._create_pending_turn_if_ready()
    assert gateway.active_turn is gateway.persistence.turn
    assert len(gateway.registry.set_turn_calls) == 1


@pytest.mark.asyncio
async def test_cancel_then_turn_start_creates_replacement_without_resend() -> None:
    old_turn_id = uuid.uuid4()
    old_response_id = uuid.uuid4()
    gateway, new_turn = _gateway(
        old_turn_id=old_turn_id,
        old_response_id=old_response_id,
        new_turn_id=uuid.uuid4(),
    )

    await gateway._handle_response_cancel(
        ResponseCancelMessage(
            type="client.response.cancel",
            response_id=old_response_id,
            reason="barge_in",
        )
    )
    await gateway._handle_turn_start(TurnStartMessage(type="client.turn.start"))

    assert gateway.active_turn is new_turn
    assert gateway.stats.cancellation_count == 1
    assert len(gateway.registry.set_turn_calls) == 1
    assert [event.args[0]["type"] for event in gateway._send.await_args_list] == [
        "response.cancelled",
        "server.turn.ready",
    ]


@pytest.mark.asyncio
async def test_late_old_cleanup_does_not_clear_replacement_response() -> None:
    old_turn_id = uuid.uuid4()
    old_response_id = uuid.uuid4()
    gateway, _ = _gateway(
        old_turn_id=old_turn_id,
        old_response_id=old_response_id,
        new_turn_id=uuid.uuid4(),
    )
    new_response_id = uuid.uuid4()
    gateway._last_response_id = new_response_id
    gateway._response_turn_id = uuid.uuid4()
    gateway.cancel_guard.activate(new_response_id)

    await gateway._complete_response_state(old_response_id)

    assert gateway._response_turn_id is not None
    assert gateway.cancel_guard.can_emit(new_response_id)
    assert gateway.registry.clear_response.await_count == 1
