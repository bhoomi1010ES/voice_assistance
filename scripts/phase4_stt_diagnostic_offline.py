#!/usr/bin/env python3
"""Recognize the exact captured physical PCM offline through the Windows worker."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import uuid
import wave
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.stt.evaluation import calculate_wer, normalize_transcript
from app.stt.service import STTService

REFERENCE = "The quick brown fox jumps over the lazy dog."


def read_pcm_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        properties = {
            "channels": source.getnchannels(),
            "sample_rate_hz": source.getframerate(),
            "sample_width_bytes": source.getsampwidth(),
            "compression": source.getcomptype(),
        }
        if properties != {
            "channels": 1,
            "sample_rate_hz": 16_000,
            "sample_width_bytes": 2,
            "compression": "NONE",
        }:
            raise ValueError(f"input WAV is not 16 kHz mono PCM16: {properties}")
        pcm = source.readframes(source.getnframes())
    if not pcm or len(pcm) % 2:
        raise ValueError("input WAV contains no complete PCM16 samples")
    return pcm


async def collect_events(turn, events: list[dict[str, object]]) -> None:
    while True:
        event = await turn.events.get()
        if event is None:
            return
        events.append(
            {
                "type": event.event_type,
                "text": event.text,
                "timestamp_ms": event.timestamp_ms,
                "monotonic_timestamp": event.metrics.get("monotonic_timestamp"),
            }
        )


async def run(wav_path: Path, output_path: Path | None) -> int:
    pcm = read_pcm_wav(wav_path)
    settings = Settings(_env_file=None, stt_engine="windows")
    service = STTService(settings)
    events: list[dict[str, object]] = []
    collector = None
    payload: dict[str, object] = {
        "route": "offline_same_audio_windows_worker",
        "reference_raw": REFERENCE,
        "reference_normalized": normalize_transcript(REFERENCE),
        "wav_path": str(wav_path),
        "pcm_bytes": len(pcm),
        "pcm_samples": len(pcm) // 2,
        "pcm_duration_ms": round(len(pcm) / (16_000 * 2) * 1000),
        "python_pid": os.getpid(),
    }
    try:
        info = await service.initialize()
        payload["engine"] = info.__dict__
        session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        turn = await service.start_turn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            language="en-US",
        )
        collector = asyncio.create_task(collect_events(turn, events))
        for offset in range(0, len(pcm), 640):
            await turn.accept_audio(pcm[offset : offset + 640])
        result = await turn.finalize()
        await asyncio.sleep(0.1)
        wer = calculate_wer(REFERENCE, result.event.text)
        payload.update(
            {
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "response_id": str(response_id),
                "hypothesis_raw": result.event.text,
                "hypothesis_normalized": normalize_transcript(result.event.text),
                "substitutions": wer.substitutions,
                "deletions": wer.deletions,
                "insertions": wer.insertions,
                "reference_word_count": wer.reference_words,
                "wer": wer.wer,
                "partials": [event for event in events if event["type"] == "transcript.partial"],
                "partial_count": len(
                    [event for event in events if event["type"] == "transcript.partial"]
                ),
                "final_metrics": result.metrics,
                "status": "PASS",
            }
        )
        await turn.close()
    except Exception as error:  # noqa: BLE001 - diagnostic result must preserve failure details
        payload["status"] = "FAIL"
        payload["error"] = f"{type(error).__name__}: {error}"
    finally:
        if collector is not None:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector
        await service.close()

    encoded = json.dumps(payload, indent=2, default=str)
    print(encoded)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded + "\n", encoding="utf-8")
    return 0 if payload.get("status") == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "wav",
        nargs="?",
        type=Path,
        default=WORKSPACE_ROOT / "scratch/phase4_stt_diagnostics/raw_input.wav",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=WORKSPACE_ROOT / "scratch/phase4_stt_diagnostics/offline_result.json",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.wav, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
