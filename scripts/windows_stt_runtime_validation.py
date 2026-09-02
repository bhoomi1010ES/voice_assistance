#!/usr/bin/env python3
"""Exercise real Windows STT lifecycle, concurrency, and recovery paths."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import uuid
import wave
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = WORKSPACE_ROOT / "backend"
if str(BACKEND_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.stt.base import STTCancelledError  # noqa: E402
from app.stt.service import STTService  # noqa: E402


def read_pcm_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (
            source.getframerate() != 16_000
            or source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getcomptype() != "NONE"
        ):
            raise ValueError("input WAV must be 16 kHz mono signed PCM16")
        pcm = source.readframes(source.getnframes())
    if not pcm or len(pcm) % 2:
        raise ValueError("input WAV contains no valid PCM16 frames")
    return pcm


def process_snapshot(pid: int | None) -> dict[str, object] | None:
    if pid is None:
        return None
    command = (
        f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "if ($null -ne $p) { [pscustomobject]@{ cpu_seconds=$p.CPU; "
        "working_set_bytes=$p.WorkingSet64; private_bytes=$p.PrivateMemorySize64 } "
        "| ConvertTo-Json -Compress }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return json.loads(result.stdout)


async def collect_events(turn, events: list[dict[str, object]]) -> None:
    while True:
        event = await turn.events.get()
        if event is None:
            return
        events.append(
            {
                "type": event.event_type,
                "text": event.text,
                "session_id": str(event.session_id),
                "turn_id": str(event.turn_id),
                "response_id": str(event.response_id),
                "generation": event.generation,
                "timestamp_ms": event.timestamp_ms,
            }
        )


async def feed(turn, pcm: bytes, *, realtime: bool) -> None:
    for offset in range(0, len(pcm), 640):
        chunk = pcm[offset : offset + 640]
        await turn.accept_audio(chunk)
        if realtime:
            await asyncio.sleep(len(chunk) / (16_000 * 2))


async def successful_turn(service: STTService, pcm: bytes, *, realtime: bool) -> dict[str, object]:
    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    turn = await service.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        language="en-US",
    )
    events: list[dict[str, object]] = []
    collector = asyncio.create_task(collect_events(turn, events))
    try:
        await feed(turn, pcm, realtime=realtime)
        result = await turn.finalize()
        await asyncio.sleep(0.1)
        partials = [event for event in events if event["type"] == "transcript.partial"]
        finals = [event for event in events if event["type"] == "transcript.final"]
        final = result.event
        return {
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "response_id": str(response_id),
            "final": final.text,
            "partial_count": len(partials),
            "final_count": len(finals),
            "correlation_ok": all(
                event["session_id"] == str(session_id)
                and event["turn_id"] == str(turn_id)
                and event["response_id"] == str(response_id)
                for event in events
            ),
            "metrics": result.metrics,
        }
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector
        await turn.close()


async def cancelled_turn(service: STTService, pcm: bytes) -> dict[str, object]:
    turn = await service.start_turn(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        language="en-US",
    )
    events: list[dict[str, object]] = []
    collector = asyncio.create_task(collect_events(turn, events))
    try:
        await turn.accept_audio(pcm[:640])
        await turn.cancel()
        await asyncio.sleep(0.2)
        try:
            await turn.finalize()
        except STTCancelledError:
            finalize_cancelled = True
        else:
            finalize_cancelled = False
        return {
            "cancelled": True,
            "finalize_rejected": finalize_cancelled,
            "final_events": len([event for event in events if event["type"] == "transcript.final"]),
        }
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector
        await turn.close()


async def run(path: Path, output: Path | None) -> int:
    pcm = read_pcm_wav(path)
    settings = Settings()
    service = STTService(settings)
    payload: dict[str, object] = {"status": "PASS"}
    try:
        info = await service.initialize()
        engine = service.engine
        process = getattr(engine, "_process", None)
        worker_pid = process.pid if process is not None else None
        payload["engine"] = info.__dict__
        payload["worker_pid"] = worker_pid
        payload["python_pid"] = os.getpid()
        payload["resources_idle"] = {
            "python": process_snapshot(os.getpid()),
            "worker": process_snapshot(worker_pid),
        }

        payload["cancellation"] = await cancelled_turn(service, pcm)
        payload["post_cancel_reuse"] = await successful_turn(service, pcm, realtime=False)

        first = await service.start_turn(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            language="en-US",
        )
        second = await service.start_turn(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            language="en-US",
        )
        first_task = asyncio.create_task(feed(first, pcm, realtime=True))
        second_task = asyncio.create_task(feed(second, pcm, realtime=True))
        await asyncio.sleep(1)
        payload["resources_concurrent"] = {
            "python": process_snapshot(os.getpid()),
            "worker": process_snapshot(worker_pid),
        }
        await asyncio.gather(first_task, second_task)
        first_result, second_result = await asyncio.gather(first.finalize(), second.finalize())
        payload["concurrency"] = {
            "stream_count": 2,
            "first": {
                "session_id": str(first.session_id),
                "turn_id": str(first.turn_id),
                "response_id": str(first.response_id),
                "final": first_result.event.text,
            },
            "second": {
                "session_id": str(second.session_id),
                "turn_id": str(second.turn_id),
                "response_id": str(second.response_id),
                "final": second_result.event.text,
            },
            "isolated": (
                first_result.event.turn_id == first.turn_id
                and second_result.event.turn_id == second.turn_id
                and first_result.event.turn_id != second_result.event.turn_id
            ),
        }
        await first.close()
        await second.close()

        recovery_turn = await service.start_turn(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            language="en-US",
        )
        process = getattr(engine, "_process", None)
        if process is None:
            raise RuntimeError("Windows worker process is missing during recovery test")
        process.kill()
        await asyncio.sleep(0.5)
        try:
            await recovery_turn.finalize()
        except Exception as error:  # noqa: BLE001 - capture controlled crash behavior
            payload["worker_failure"] = {
                "controlled_error": type(error).__name__,
                "message": str(error),
            }
        else:
            payload["worker_failure"] = {"controlled_error": None}
        await recovery_turn.close()
        restarted = await engine.initialize()
        payload["recovery"] = {
            "restarted": restarted.available,
            "language": restarted.language,
            "post_restart_turn": await successful_turn(service, pcm, realtime=False),
        }
    except Exception as error:  # noqa: BLE001 - this is an evidence harness
        payload["status"] = "FAIL"
        payload["error"] = f"{type(error).__name__}: {error}"
    finally:
        await service.close()

    encoded = json.dumps(payload, indent=2, default=str)
    print(encoded)
    if output is not None:
        output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if payload["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="16 kHz mono PCM16 speech WAV")
    parser.add_argument("--output", type=Path, help="optional JSON evidence path")
    args = parser.parse_args()
    return asyncio.run(run(args.wav, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
