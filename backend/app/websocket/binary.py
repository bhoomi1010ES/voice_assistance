from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"VAI1"
PROTOCOL_VERSION = 1
CLIENT_PCM_FRAME_TYPE = 1
HEADER_STRUCT = struct.Struct("!4sBBHIQI")
HEADER_BYTES = HEADER_STRUCT.size


class BinaryProtocolError(ValueError):
    """Raised when a binary voice frame is malformed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BinaryPcmFrame:
    sequence_no: int
    client_timestamp_ms: int
    payload: bytes

    @property
    def payload_length(self) -> int:
        return len(self.payload)


def encode_pcm_frame(
    *,
    sequence_no: int,
    client_timestamp_ms: int,
    payload: bytes,
) -> bytes:
    if sequence_no < 0 or client_timestamp_ms < 0:
        raise ValueError("sequence and timestamp must be non-negative")
    return (
        HEADER_STRUCT.pack(
            MAGIC,
            PROTOCOL_VERSION,
            CLIENT_PCM_FRAME_TYPE,
            0,
            sequence_no,
            client_timestamp_ms,
            len(payload),
        )
        + payload
    )


def decode_pcm_frame(
    data: bytes,
    *,
    max_frame_bytes: int,
    expected_payload_bytes: int,
) -> BinaryPcmFrame:
    if len(data) > max_frame_bytes:
        raise BinaryProtocolError("frame_too_large")
    if len(data) < HEADER_BYTES:
        raise BinaryProtocolError("frame_header_truncated")

    magic, version, frame_type, flags, sequence_no, timestamp_ms, payload_length = (
        HEADER_STRUCT.unpack_from(data)
    )
    if magic != MAGIC:
        raise BinaryProtocolError("invalid_frame_magic")
    if version != PROTOCOL_VERSION:
        raise BinaryProtocolError("unsupported_frame_version")
    if frame_type != CLIENT_PCM_FRAME_TYPE:
        raise BinaryProtocolError("unsupported_frame_type")
    if flags != 0:
        raise BinaryProtocolError("unsupported_frame_flags")
    if payload_length != expected_payload_bytes:
        raise BinaryProtocolError("invalid_pcm_payload_length")
    if len(data) != HEADER_BYTES + payload_length:
        raise BinaryProtocolError("frame_length_mismatch")
    return BinaryPcmFrame(
        sequence_no=sequence_no,
        client_timestamp_ms=timestamp_ms,
        payload=data[HEADER_BYTES:],
    )
