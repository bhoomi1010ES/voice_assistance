"""Connection-owned pause semantics; no providers, database, or user data required."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.websocket.gateway import VoiceGateway
from app.websocket.protocol import InteractionStateMessage, ProtocolError, parse_control_message
from app.websocket.state import StateTransitionError


def gateway() -> VoiceGateway:
    value = VoiceGateway.__new__(VoiceGateway)
    value.voice_session = SimpleNamespace(id=uuid.uuid4())
    value.state = SimpleNamespace(session_id=value.voice_session.id)
    value._turn_started = 100.0
    value._interaction_active = True
    value._turn_paused_at = None
    value._turn_paused_seconds = 0.0
    return value


@pytest.mark.parametrize("active", [True, False])
def test_vis_protocol_accepts_only_boolean_and_connection_owned_state(active: bool) -> None:
    parsed = parse_control_message(
        json.dumps({"type": "client.interaction.state", "active": active}), max_bytes=4096
    )
    assert isinstance(parsed, InteractionStateMessage)
    assert parsed.active is active


@pytest.mark.parametrize("active", ["true", "false", 1, 0, None, [], {}])
def test_vis_protocol_rejects_coerced_or_malformed_state(active) -> None:
    with pytest.raises(ProtocolError):
        parse_control_message(
            json.dumps({"type": "client.interaction.state", "active": active}), max_bytes=4096
        )


@pytest.mark.parametrize(
    "identity", ["session_id", "turn_id", "response_id", "user_id", "device_id"]
)
def test_vis_protocol_cannot_target_another_owner(identity: str) -> None:
    with pytest.raises(ProtocolError):
        parse_control_message(
            json.dumps(
                {"type": "client.interaction.state", "active": False, identity: str(uuid.uuid4())}
            ),
            max_bytes=4096,
        )


def test_vis_pause_freezes_recording_time_and_duplicate_callbacks_are_idempotent(
    monkeypatch,
) -> None:
    value = gateway()
    identity = value.state.session_id
    monkeypatch.setattr("app.websocket.gateway.time.monotonic", lambda: 102.0)
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=False)
    )
    assert value._elapsed_turn_ms(now=1000.0) == 2000
    monkeypatch.setattr("app.websocket.gateway.time.monotonic", lambda: 1000.0)
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=False)
    )
    assert value._elapsed_turn_ms(now=1000.0) == 2000
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=True)
    )
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=True)
    )
    assert value._elapsed_turn_ms(now=1001.0) == 3000
    assert value.state.session_id == identity


def test_vis_signal_requires_an_existing_session_and_idle_does_not_create_turn() -> None:
    value = gateway()
    value.voice_session = None
    with pytest.raises(StateTransitionError):
        value._handle_interaction_state(
            InteractionStateMessage(type="client.interaction.state", active=False)
        )
    value = gateway()
    value._turn_started = None
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=False)
    )
    value._handle_interaction_state(
        InteractionStateMessage(type="client.interaction.state", active=True)
    )
    assert value._elapsed_turn_ms() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["heartbeat_timeout", "session_timeout", "turn_timeout", "revoked"]
)
async def test_vis_watchdog_preserves_auth_and_session_limits_while_paused(
    monkeypatch, failure: str
) -> None:
    value = gateway()
    value.settings = SimpleNamespace(
        voice_heartbeat_interval_seconds=5,
        voice_idle_timeout_seconds=90,
        voice_heartbeat_timeout_seconds=20,
        voice_max_session_seconds=1800,
        voice_max_turn_seconds=120,
    )
    value._closing = asyncio.Event()
    value._last_activity = 1000.0
    value._last_ping = 900.0 if failure == "heartbeat_timeout" else 1000.0
    value._connection_started = -1000.0 if failure == "session_timeout" else 0.0
    value._last_auth_check = 900.0
    value._stt_finalize_task = None
    value._turn_paused_at = 102.0 if failure != "turn_timeout" else None
    value._timeout = AsyncMock()
    value._auth_still_valid = AsyncMock(return_value=False)
    monkeypatch.setattr("app.websocket.gateway.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("app.websocket.gateway.asyncio.sleep", AsyncMock())
    await value._watchdog_loop()
    if failure == "revoked":
        value._auth_still_valid.assert_awaited_once()
        value._timeout.assert_not_awaited()
    else:
        value._timeout.assert_awaited_once_with(failure)


@pytest.mark.asyncio
async def test_vis_dispatch_changes_only_pause_clock_without_response_reexecution() -> None:
    value = gateway()
    value._handle_audio_commit = AsyncMock()
    value._handle_turn_start = AsyncMock()
    value._handle_response_cancel = AsyncMock()
    for active in [False, False, True, True]:
        await value._handle_control(
            InteractionStateMessage(type="client.interaction.state", active=active)
        )
    value._handle_audio_commit.assert_not_awaited()
    value._handle_turn_start.assert_not_awaited()
    value._handle_response_cancel.assert_not_awaited()
