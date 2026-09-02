from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

import app.stt.windows_engine as windows_engine_module
from app.core.config import Settings
from app.stt.base import (
    EnginePartialCallback,
    STTCancelledError,
    STTEngine,
    STTEngineFinal,
    STTEngineInfo,
    STTEnginePartial,
    STTEngineTurn,
)
from app.stt.protocol import WorkerProtocolError, WorkerRequest, WorkerResponse, parse_response
from app.stt.remote_engine import RemoteTranscriptionEngine
from app.stt.service import STTConfigurationError, STTService
from app.stt.windows_engine import WindowsSpeechEngine, _WindowsTurnState


class FakeStreamingEngine(STTEngine):
    name = "fake"

    def __init__(self) -> None:
        self.turns: dict[uuid.UUID, STTEngineTurn] = {}
        self.cancelled: set[uuid.UUID] = set()
        self.closed = False

    async def initialize(self) -> STTEngineInfo:
        return STTEngineInfo(engine=self.name, runtime="test", available=True, language="en-US")

    async def start_turn(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        generation: int,
        language: str | None,
        on_partial: EnginePartialCallback,
    ) -> STTEngineTurn:
        turn = STTEngineTurn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            generation=generation,
            language=language,
            on_partial=on_partial,
        )
        self.turns[turn_id] = turn
        return turn

    async def push_audio(self, turn: STTEngineTurn, pcm_bytes: bytes, *, generation: int) -> None:
        if turn.turn_id in self.cancelled:
            raise STTCancelledError("cancelled")
        await turn.on_partial(
            STTEnginePartial(
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                response_id=turn.response_id,
                generation=generation,
                text="hello",
                language="en-US",
                confidence=0.91,
                audio_duration_ms=20,
                timestamp_ms=int(time.time() * 1000),
                monotonic_timestamp=time.monotonic(),
            )
        )

    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        if turn.turn_id in self.cancelled:
            raise STTCancelledError("cancelled")
        now = time.monotonic()
        return STTEngineFinal(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            response_id=turn.response_id,
            generation=generation,
            text="hello world",
            language="en-US",
            confidence=0.95,
            timestamp_ms=int(time.time() * 1000),
            monotonic_timestamp=now,
            inference_duration_ms=4.0,
        )

    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        self.cancelled.add(turn.turn_id)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_remote_is_the_default_engine_without_initializing_it() -> None:
    service = STTService(Settings(_env_file=None))
    assert isinstance(service.engine, RemoteTranscriptionEngine)
    await service.close()


def test_windows_engine_is_rejected_by_production_selector() -> None:
    with pytest.raises(STTConfigurationError, match="STT_ENGINE=windows is retired"):
        STTService(Settings(_env_file=None, stt_engine="windows"))


def test_language_normalization_can_preserve_windows_culture() -> None:
    assert STTService.normalize_language("en-US", preserve_culture=True) == "en-US"
    assert STTService.normalize_language("en_US", preserve_culture=True) == "en-US"
    assert STTService.normalize_language("en-US") == "en"


@pytest.mark.asyncio
async def test_engine_boundary_maps_partial_final_and_metrics() -> None:
    engine = FakeStreamingEngine()
    service = STTService(Settings(_env_file=None), engine=engine)
    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    turn = await service.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        language="en-US",
    )
    await turn.accept_audio(b"\x00\x10" * 320)
    partial = await turn.events.get()
    assert partial is not None
    assert partial.event_type == "transcript.partial"
    assert partial.metrics["confidence"] == 0.91

    final = await turn.finalize()
    assert final.event.text == "hello world"
    assert final.event.language == "en-US"
    assert final.metrics["confidence"] == 0.95
    await turn.close()
    await service.close()


@pytest.mark.asyncio
async def test_stale_partial_after_commit_is_rejected() -> None:
    engine = FakeStreamingEngine()
    service = STTService(Settings(_env_file=None), engine=engine)
    turn = await service.start_turn(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
    )
    await turn.accept_audio(b"\x00\x10" * 320)
    assert (await turn.events.get()) is not None
    await turn.finalize()
    await turn._on_engine_partial(
        STTEnginePartial(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            response_id=turn.response_id,
            generation=0,
            text="late result",
            language="en-US",
            confidence=None,
            audio_duration_ms=20,
            timestamp_ms=int(time.time() * 1000),
            monotonic_timestamp=time.monotonic(),
        )
    )
    assert turn.events.empty()
    await turn.close()
    await service.close()


@pytest.mark.asyncio
async def test_windows_adapter_maps_worker_events_and_rejects_stale_results() -> None:
    engine = WindowsSpeechEngine(Settings(_env_file=None))
    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    partials: list[STTEnginePartial] = []

    async def on_partial(partial: STTEnginePartial) -> None:
        partials.append(partial)

    loop = asyncio.get_running_loop()
    handle = STTEngineTurn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=on_partial,
    )
    state = _WindowsTurnState(
        handle=handle,
        start_future=loop.create_future(),
        final_future=loop.create_future(),
        generation=0,
    )
    engine._turns[(session_id, turn_id)] = state

    await engine._route_response(
        WorkerResponse(
            type="PARTIAL",
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            generation=0,
            text="hello",
            confidence=0.8,
        )
    )
    assert [partial.text for partial in partials] == ["hello"]

    await engine._route_response(
        WorkerResponse(
            type="PARTIAL",
            session_id=session_id,
            turn_id=turn_id,
            response_id=uuid.uuid4(),
            generation=0,
            text="stale",
        )
    )
    assert [partial.text for partial in partials] == ["hello"]

    await engine._route_response(
        WorkerResponse(
            type="FINAL",
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            generation=0,
            text="hello world",
            confidence=0.9,
        )
    )
    final = await state.final_future
    assert final.text == "hello world"
    assert final.confidence == 0.9

    cancel_session, cancel_turn_id, cancel_response = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cancel_handle = STTEngineTurn(
        session_id=cancel_session,
        turn_id=cancel_turn_id,
        response_id=cancel_response,
        generation=0,
        language="en",
        on_partial=on_partial,
    )
    cancel_state = _WindowsTurnState(
        handle=cancel_handle,
        start_future=loop.create_future(),
        final_future=loop.create_future(),
        generation=0,
    )
    engine._turns[(cancel_session, cancel_turn_id)] = cancel_state
    await engine.cancel_turn(cancel_handle, generation=1)
    assert cancel_state.cancelled
    assert cancel_state.final_future.cancelled()


@pytest.mark.asyncio
async def test_windows_adapter_streams_to_a_mocked_worker_process(monkeypatch, tmp_path) -> None:
    class FakeLineStream:
        def __init__(self, *messages: dict[str, object]) -> None:
            self.lines: asyncio.Queue[bytes] = asyncio.Queue()
            for message in messages:
                self.emit(message)

        def emit(self, message: dict[str, object]) -> None:
            self.lines.put_nowait(json.dumps(message).encode() + b"\n")

        async def readline(self) -> bytes:
            return await self.lines.get()

    class FakeWriter:
        def __init__(self, process: FakeProcess) -> None:
            self.process = process
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)
            self.process.handle_write(data)

        async def drain(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.stdout = FakeLineStream(
                {
                    "type": "READY",
                    "engine": "windows",
                    "runtime": "System.Speech.Recognition",
                    "available": True,
                    "language": "en-US",
                    "recognizer_name": "Fake Windows English",
                    "languages": ["en-US"],
                }
            )
            self.stderr = FakeLineStream()
            self.stdin = FakeWriter(self)

        def handle_write(self, data: bytes) -> None:
            request = json.loads(data)
            context = {
                "session_id": request.get("session_id"),
                "turn_id": request.get("turn_id"),
                "response_id": request.get("response_id"),
                "generation": request.get("generation"),
            }
            if request["type"] == "START_TURN":
                self.stdout.emit({"type": "TURN_READY", **context})
            elif request["type"] == "AUDIO":
                self.stdout.emit({"type": "PARTIAL", **context, "text": "hello"})
            elif request["type"] == "COMMIT":
                self.stdout.emit({"type": "FINAL", **context, "text": "hello world"})
            elif request["type"] == "SHUTDOWN":
                self.returncode = 0
                self.stdout.emit({"type": "SHUTDOWN_ACK"})

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    process = FakeProcess()

    async def create_process(*args, **kwargs):
        return process

    monkeypatch.setattr(windows_engine_module.os, "name", "nt")
    monkeypatch.setattr(
        windows_engine_module.asyncio,
        "create_subprocess_exec",
        create_process,
    )
    settings = Settings(
        _env_file=None,
        stt_windows_worker_path=str(tmp_path / "fake-worker.exe"),
    )
    (tmp_path / "fake-worker.exe").touch()
    engine = WindowsSpeechEngine(settings)
    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    partials: list[str] = []

    async def on_partial(partial: STTEnginePartial) -> None:
        partials.append(partial.text)

    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=on_partial,
    )
    await engine.push_audio(handle, b"\x00\x01" * 320, generation=0)
    await asyncio.sleep(0)
    final = await engine.finish_turn(handle, generation=1)

    assert partials == ["hello"]
    assert final.text == "hello world"
    sent_types = [json.loads(line)["type"] for line in process.stdin.writes]
    assert sent_types == ["START_TURN", "AUDIO", "COMMIT"]
    assert all(
        json.loads(line).get("session_id") == str(session_id)
        for line in process.stdin.writes
        if json.loads(line)["type"] != "SHUTDOWN"
    )
    await engine.close()


def test_worker_protocol_is_correlated_and_framed() -> None:
    session_id, turn_id, response_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    request = WorkerRequest(
        type="AUDIO",
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=2,
        audio=b"\x00\x01",
    )
    assert request.to_line().endswith(b"\n")
    response = parse_response(
        f'{{"type":"PARTIAL","session_id":"{session_id}",'
        f'"turn_id":"{turn_id}","response_id":"{response_id}",'
        '"generation":2,"text":"hello"}'
    )
    assert response.turn_id == turn_id
    assert response.generation == 2

    with pytest.raises(WorkerProtocolError):
        parse_response(b'{"type":"PARTIAL","turn_id":"not-a-uuid"}')
