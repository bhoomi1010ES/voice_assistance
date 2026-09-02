#!/usr/bin/env python3
"""Run a real local Windows Speech worker against a known PCM/WAV sample.

This is intentionally a harness, not a fake recognizer.  The input must be
16 kHz, mono, 16-bit PCM WAV so the same Python adapter and C# worker path used
by the WebSocket service is exercised.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
import uuid
import wave
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.stt.service import STTService  # noqa: E402


def read_pcm_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as source:
        if (
            source.getframerate() != 16_000
            or source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getcomptype() != "NONE"
        ):
            raise ValueError("input WAV must be 16 kHz mono signed PCM16")
        frames = source.readframes(source.getnframes())
    if not frames or len(frames) % 2:
        raise ValueError("input WAV contains no valid PCM16 frames")
    return frames, len(frames) // 2


async def run(
    path: Path,
    reference: str | None,
    output: Path | None,
    realtime: bool,
) -> int:
    pcm, samples = read_pcm_wav(path)
    settings = Settings()
    if settings.stt_engine != "windows":
        raise ValueError("synthetic harness requires STT_ENGINE=windows")

    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    service = STTService(settings)
    partials: list[dict[str, object]] = []
    collector: asyncio.Task[None] | None = None
    turn = None
    try:
        engine_info = await service.initialize()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            language="en-US",
        )

        async def collect_partials() -> None:
            while True:
                event = await turn.events.get()
                if event is None:
                    return
                if event.event_type == "transcript.partial":
                    partials.append(
                        {
                            "text": event.text,
                            "timestamp_ms": event.timestamp_ms,
                            "metrics": event.metrics,
                        }
                    )

        collector = asyncio.create_task(collect_partials())
        chunk_bytes = 640
        for offset in range(0, len(pcm), chunk_bytes):
            chunk = pcm[offset : offset + chunk_bytes]
            await turn.accept_audio(chunk)
            if realtime:
                await asyncio.sleep(len(chunk) / (16_000 * 2))
        commit_started = time.monotonic()
        commit_wall_ms = int(time.time() * 1000)
        result = await turn.finalize()
        final = result.event
        if not final.text.strip():
            raise ValueError("real Windows recognizer returned an empty final transcript")
        payload = {
            "status": "PASS",
            "reference": reference,
            "recognized": final.text,
            "confidence": result.metrics.get("confidence"),
            "partial_count": len(partials),
            "partials": partials,
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "response_id": str(response_id),
            "audio_duration_ms": round(samples / 16_000 * 1000),
            "commit_timestamp_ms": commit_wall_ms,
            "commit_monotonic_ms": round(commit_started * 1000, 1),
            "final_timestamp_ms": final.timestamp_ms,
            "speech_end_to_final_ms": result.metrics.get("speech_end_to_final_transcript_ms"),
            "first_partial_latency_ms": result.metrics.get("first_partial_latency_ms"),
            "engine": engine_info.__dict__,
        }
    except Exception as error:  # noqa: BLE001 - report harness failures as JSON
        payload = {
            "status": "FAIL",
            "error": f"{type(error).__name__}: {error}",
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "response_id": str(response_id),
        }
    finally:
        if collector is not None:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector
        if turn is not None:
            await turn.close()
        await service.close()

    encoded = json.dumps(payload, indent=2, default=str)
    print(encoded)
    if output is not None:
        output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="16 kHz mono PCM16 English speech WAV")
    parser.add_argument("--reference", help="optional immutable ground-truth transcript")
    parser.add_argument("--output", type=Path, help="optional JSON result path")
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="pace 20 ms PCM chunks at capture speed to exercise hypotheses",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.wav, args.reference, args.output, args.realtime))


if __name__ == "__main__":
    raise SystemExit(main())
