import struct

import pytest

from app.tts.base import TTSProviderError
from app.tts.wav import WavPcmStreamParser


def _wav(payload: bytes, *, sample_rate_hz: int = 24_000, channels: int = 1) -> bytes:
    fmt = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate_hz,
        sample_rate_hz * channels * 2,
        channels * 2,
        16,
    )
    junk = b"JUNK" + struct.pack("<I", 3) + b"abc" + b"\0"
    data = b"data" + struct.pack("<I", len(payload)) + payload
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt + junk + data
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_wav_parser_extracts_data_with_non_44_byte_header_and_split_input() -> None:
    parser = WavPcmStreamParser(expected_sample_rate_hz=24_000)
    source = _wav(b"\x00\x01\x02\x03\x04\x05")
    output: list[bytes] = []
    for index in range(0, len(source), 5):
        output.extend(parser.feed(source[index : index + 5]))
    parser.finish()

    assert b"".join(output) == b"\x00\x01\x02\x03\x04\x05"
    assert parser.format is not None
    assert parser.format.sample_rate_hz == 24_000
    assert parser.format.channels == 1
    assert parser.format.bits_per_sample == 16


def test_wav_parser_rejects_wrong_sample_rate_or_channels() -> None:
    with pytest.raises(TTSProviderError, match="mono PCM16"):
        parser = WavPcmStreamParser(expected_sample_rate_hz=24_000)
        parser.feed(_wav(b"\x00\x01", sample_rate_hz=16_000))

    with pytest.raises(TTSProviderError, match="mono PCM16"):
        parser = WavPcmStreamParser(expected_sample_rate_hz=24_000)
        parser.feed(_wav(b"\x00\x01", channels=2))


def test_wav_parser_rejects_truncated_data() -> None:
    parser = WavPcmStreamParser(expected_sample_rate_hz=24_000)
    source = _wav(b"\x00\x01\x02\x03")
    parser.feed(source[:-1])
    with pytest.raises(TTSProviderError):
        parser.finish()
