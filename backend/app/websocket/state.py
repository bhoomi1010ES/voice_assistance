from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.websocket.binary import BinaryPcmFrame


class VoiceState(StrEnum):
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    SESSION_READY = "SESSION_READY"
    RECEIVING_AUDIO = "RECEIVING_AUDIO"
    ENDING = "ENDING"
    CLOSED = "CLOSED"


class StateTransitionError(ValueError):
    """Raised when a voice event is invalid for the current state."""


class SequenceError(ValueError):
    """Raised when an audio frame is duplicate, missing, or out of order."""

    def __init__(self, kind: str, *, expected: int, received: int):
        super().__init__(kind)
        self.kind = kind
        self.expected = expected
        self.received = received


@dataclass(frozen=True)
class TurnCounters:
    turn_id: uuid.UUID
    response_id: uuid.UUID
    turn_number: int
    frame_count: int
    byte_count: int
    last_sequence_no: int


class VoiceConnectionState:
    """Small deterministic state machine for one authenticated connection."""

    def __init__(self) -> None:
        self.state = VoiceState.CONNECTING
        self.session_id: uuid.UUID | None = None
        self.current_turn_id: uuid.UUID | None = None
        self.current_response_id: uuid.UUID | None = None
        self.turn_number = 0
        self.expected_sequence = 0
        self.frame_count = 0
        self.byte_count = 0

    def authenticate(self) -> None:
        self._require(VoiceState.CONNECTING)
        self.state = VoiceState.AUTHENTICATED

    def session_ready(self, session_id: uuid.UUID, *, completed_turns: int = 0) -> None:
        self._require(VoiceState.AUTHENTICATED)
        self.session_id = session_id
        self.turn_number = completed_turns
        self.state = VoiceState.SESSION_READY

    def start_turn(self, turn_id: uuid.UUID, response_id: uuid.UUID) -> int:
        self._require(VoiceState.SESSION_READY)
        self.turn_number += 1
        self.current_turn_id = turn_id
        self.current_response_id = response_id
        self.expected_sequence = 0
        self.frame_count = 0
        self.byte_count = 0
        self.state = VoiceState.RECEIVING_AUDIO
        return self.turn_number

    def accept_frame(self, frame: BinaryPcmFrame) -> None:
        self._require(VoiceState.RECEIVING_AUDIO)
        if frame.sequence_no != self.expected_sequence:
            kind = "duplicate" if frame.sequence_no < self.expected_sequence else "gap"
            raise SequenceError(
                kind,
                expected=self.expected_sequence,
                received=frame.sequence_no,
            )
        self.expected_sequence += 1
        self.frame_count += 1
        self.byte_count += frame.payload_length

    def commit(self, *, last_sequence_no: int, frame_count: int, byte_count: int) -> TurnCounters:
        self._require(VoiceState.RECEIVING_AUDIO)
        if self.current_turn_id is None or self.current_response_id is None:
            raise StateTransitionError("active turn is missing identifiers")
        if frame_count != self.frame_count or byte_count != self.byte_count:
            raise StateTransitionError("audio commit counters do not match received frames")
        expected_last = self.expected_sequence - 1
        if last_sequence_no != expected_last:
            raise StateTransitionError("audio commit sequence does not match received frames")

        counters = TurnCounters(
            turn_id=self.current_turn_id,
            response_id=self.current_response_id,
            turn_number=self.turn_number,
            frame_count=self.frame_count,
            byte_count=self.byte_count,
            last_sequence_no=last_sequence_no,
        )
        self.current_turn_id = None
        self.current_response_id = None
        self.state = VoiceState.SESSION_READY
        return counters

    def abort_turn(self) -> TurnCounters:
        self._require(VoiceState.RECEIVING_AUDIO)
        if self.current_turn_id is None or self.current_response_id is None:
            raise StateTransitionError("active turn is missing identifiers")
        counters = TurnCounters(
            turn_id=self.current_turn_id,
            response_id=self.current_response_id,
            turn_number=self.turn_number,
            frame_count=self.frame_count,
            byte_count=self.byte_count,
            last_sequence_no=self.expected_sequence - 1,
        )
        self.current_turn_id = None
        self.current_response_id = None
        self.state = VoiceState.SESSION_READY
        return counters

    def begin_ending(self) -> None:
        if self.state not in {VoiceState.CLOSED, VoiceState.ENDING}:
            self.state = VoiceState.ENDING

    def close(self) -> None:
        self.state = VoiceState.CLOSED

    def _require(self, expected: VoiceState) -> None:
        if self.state != expected:
            raise StateTransitionError(
                f"event is invalid in state {self.state}; expected {expected}"
            )
