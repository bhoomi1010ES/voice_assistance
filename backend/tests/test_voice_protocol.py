from __future__ import annotations

import uuid

import pytest

from app.websocket.binary import (
    HEADER_BYTES,
    BinaryPcmFrame,
    BinaryProtocolError,
    decode_pcm_frame,
    encode_pcm_frame,
)
from app.websocket.cancellation import CancellationGuard
from app.websocket.protocol import ProtocolError, parse_control_message
from app.websocket.state import (
    SequenceError,
    StateTransitionError,
    VoiceConnectionState,
    VoiceState,
)


def test_binary_pcm_frame_round_trips_with_fixed_header() -> None:
    payload = bytes(range(64)) * 10
    encoded = encode_pcm_frame(
        sequence_no=7,
        client_timestamp_ms=1234,
        payload=payload,
    )

    assert len(encoded) == HEADER_BYTES + len(payload)
    decoded = decode_pcm_frame(
        encoded,
        max_frame_bytes=4096,
        expected_payload_bytes=len(payload),
    )
    assert decoded.sequence_no == 7
    assert decoded.client_timestamp_ms == 1234
    assert decoded.payload == payload


def test_binary_pcm_frame_rejects_bad_header_and_length() -> None:
    payload = b"\x00" * 640
    encoded = encode_pcm_frame(sequence_no=0, client_timestamp_ms=1, payload=payload)

    with pytest.raises(BinaryProtocolError, match="invalid_frame_magic"):
        decode_pcm_frame(
            b"BAD!" + encoded[4:],
            max_frame_bytes=4096,
            expected_payload_bytes=640,
        )
    with pytest.raises(BinaryProtocolError, match="frame_length_mismatch"):
        decode_pcm_frame(
            encoded + b"extra",
            max_frame_bytes=4096,
            expected_payload_bytes=640,
        )


def test_control_messages_forbid_client_identity_override() -> None:
    raw = (
        '{"type":"client.session.start","protocol_version":1,"audio":'
        '{"sample_rate_hz":16000,"channels":1,"frame_samples":320,"frame_bytes":640},'
        '"user_id":"other"}'
    )

    with pytest.raises(ProtocolError, match="invalid_control_message"):
        parse_control_message(raw, max_bytes=16 * 1024)


def test_voice_state_accepts_one_turn_and_rejects_invalid_sequence() -> None:
    state = VoiceConnectionState()
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    response_id = uuid.uuid4()
    state.authenticate()
    state.session_ready(session_id)
    state.start_turn(turn_id, response_id)

    state.accept_frame(BinaryPcmFrame(0, 1, b"x" * 640))
    with pytest.raises(SequenceError, match="gap"):
        state.accept_frame(BinaryPcmFrame(2, 2, b"x" * 640))

    with pytest.raises(StateTransitionError, match="counters"):
        state.commit(last_sequence_no=0, frame_count=2, byte_count=1280)


def test_state_transitions_and_commit_reset_active_turn() -> None:
    state = VoiceConnectionState()
    state.authenticate()
    state.session_ready(uuid.uuid4())
    turn_id = uuid.uuid4()
    response_id = uuid.uuid4()
    assert state.start_turn(turn_id, response_id) == 1

    state.accept_frame(BinaryPcmFrame(0, 1, b"x" * 640))
    counters = state.commit(last_sequence_no=0, frame_count=1, byte_count=640)

    assert counters.turn_id == turn_id
    assert state.state == VoiceState.SESSION_READY
    assert state.current_turn_id is None


def test_cancellation_guard_blocks_stale_response() -> None:
    guard = CancellationGuard()
    response_a = uuid.uuid4()
    response_b = uuid.uuid4()
    guard.activate(response_a)
    assert guard.can_emit(response_a)
    assert guard.cancel(response_a)
    assert not guard.can_emit(response_a)

    guard.activate(response_b)
    assert not guard.can_emit(response_a)
    assert guard.can_emit(response_b)
    assert not guard.cancel(response_a)
