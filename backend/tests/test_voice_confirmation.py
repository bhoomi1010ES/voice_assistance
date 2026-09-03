from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, StrictStr

from app.core.config import Settings
from app.llm.task_tools import CreateTaskArguments
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from app.services.voice_confirmation import (
    InMemoryVoiceConfirmationStore,
    PendingConfirmation,
    resolve_confirmation,
)
from app.websocket.cancellation import CancellationGuard
from app.websocket.gateway import VoiceGateway
from app.websocket.protocol import ResponseCancelMessage


class _FakePersistence:
    def __init__(self) -> None:
        self.metadata: list[dict] = []

    async def merge_turn_metadata(self, db, principal, *, turn_id, metadata):
        self.metadata.append({"turn_id": turn_id, **metadata})
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _principal() -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


def _pending(principal: SimpleNamespace, session_id: uuid.UUID) -> PendingConfirmation:
    original_turn_id = uuid.uuid4()
    original_response_id = uuid.uuid4()
    call_id = f"call-{uuid.uuid4()}"
    return PendingConfirmation.new(
        authenticated_user_id=principal.user_id,
        device_id=principal.device_id,
        session_id=session_id,
        original_turn_id=original_turn_id,
        original_response_id=original_response_id,
        tool_call_id=call_id,
        tool_name="create_task",
        validated_tool_arguments={"title": "Call Rahul"},
        idempotency_key=(principal.user_id, original_turn_id, "create_task", call_id),
        ttl_seconds=120,
    )


async def _gateway(*, pending: PendingConfirmation | None = None):
    principal = (
        SimpleNamespace(
            user_id=pending.authenticated_user_id,
            device_id=pending.device_id,
            session_id=uuid.uuid4(),
        )
        if pending is not None
        else _principal()
    )
    session_id = pending.session_id if pending is not None else uuid.uuid4()
    current_response_id = uuid.uuid4()
    store = InMemoryVoiceConfirmationStore()
    registry = ToolRegistry()
    execution_count = 0

    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        title: StrictStr

    async def handler(_context, arguments):
        nonlocal execution_count
        execution_count += 1
        return {"task_id": "task-1", "title": arguments.title}

    registry.register(
        name="create_task",
        description="Create a task after confirmation.",
        arguments_model=Arguments,
        handler=handler,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    db = _FakeDatabase()
    outbound: list[dict] = []
    gateway = object.__new__(VoiceGateway)
    gateway.settings = Settings(
        _env_file=None,
        app_env="test",
        voice_confirmation_ttl_seconds=120,
    )
    gateway.principal = principal
    gateway.voice_session = SimpleNamespace(id=session_id)
    gateway.confirmation_store = store
    gateway.persistence = _FakePersistence()
    gateway.db = db
    gateway.cancel_guard = CancellationGuard()
    gateway.cancel_guard.activate(current_response_id)
    gateway.tool_loop = SimpleNamespace(
        executor=ToolExecutor(
            registry,
            idempotency_store=InMemoryToolIdempotencyStore(),
        )
    )

    async def send(event: dict) -> None:
        outbound.append(event)

    gateway._send = send
    if pending is not None:
        await store.create_or_get(pending)
    return gateway, store, outbound, current_response_id, lambda: execution_count


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("yes", "APPROVED"),
        ("YES, please!", "APPROVED"),
        ("go ahead", "APPROVED"),
        ("no", "REJECTED"),
        ("No thanks.", "REJECTED"),
        ("nevermind", "REJECTED"),
        ("maybe", "AMBIGUOUS"),
        ("I don't know", "AMBIGUOUS"),
    ],
)
def test_confirmation_resolver_is_deterministic(spoken: str, expected: str) -> None:
    assert resolve_confirmation(spoken) == expected


@pytest.mark.asyncio
async def test_approval_executes_once_and_replay_cannot_mutate_again() -> None:
    principal = _principal()
    pending = _pending(principal, uuid.uuid4())
    gateway, store, outbound, response_id, count = await _gateway(pending=pending)

    first = await gateway._resolve_pending_confirmation(
        session_id=pending.session_id,
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Yes",
    )
    second = await gateway._resolve_pending_confirmation(
        session_id=pending.session_id,
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Yes",
    )

    assert first == {
        "status": "completed",
        "confirmation": "approved",
        "tool_execution_count": 1,
        "database_mutation": True,
    }
    assert second == {"status": "completed", "confirmation": "already_handled"}
    assert count() == 1
    stored = await store.get((pending.authenticated_user_id, pending.device_id, pending.session_id))
    assert stored is not None and stored.status == "CONSUMED"
    assert [event["type"] for event in outbound] == [
        "confirmation.resolved",
        "assistant.text.final",
        "confirmation.resolved",
        "assistant.text.final",
    ]
    assert outbound[1]["text"].startswith("Done.")
    assert outbound[3]["text"] == "That confirmation has already been handled."


@pytest.mark.asyncio
async def test_ambiguous_and_rejected_confirmation_never_execute() -> None:
    for spoken, expected_status in (("maybe", "PENDING"), ("No", "REJECTED")):
        principal = _principal()
        pending = _pending(principal, uuid.uuid4())
        gateway, store, _outbound, response_id, count = await _gateway(pending=pending)
        await gateway._resolve_pending_confirmation(
            session_id=pending.session_id,
            turn_id=uuid.uuid4(),
            response_id=response_id,
            transcript=spoken,
        )
        stored = await store.get(
            (pending.authenticated_user_id, pending.device_id, pending.session_id)
        )
        assert stored is not None and stored.status == expected_status
        assert count() == 0


@pytest.mark.asyncio
async def test_expired_confirmation_never_executes() -> None:
    principal = _principal()
    pending = _pending(principal, uuid.uuid4())
    pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    gateway, store, outbound, response_id, count = await _gateway(pending=pending)

    result = await gateway._resolve_pending_confirmation(
        session_id=pending.session_id,
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Yes",
    )

    stored = await store.get((pending.authenticated_user_id, pending.device_id, pending.session_id))
    assert result == {"status": "completed", "confirmation": "expired"}
    assert stored is not None and stored.status == "EXPIRED"
    assert count() == 0
    assert "expired" in outbound[1]["text"]


@pytest.mark.asyncio
async def test_pending_confirmation_is_cancelled_on_disconnect_scope_cleanup() -> None:
    principal = _principal()
    pending = _pending(principal, uuid.uuid4())
    gateway, store, outbound, _response_id, count = await _gateway(pending=pending)

    cancelled = await store.cancel_scope(
        (pending.authenticated_user_id, pending.device_id, pending.session_id)
    )
    result = await gateway._resolve_pending_confirmation(
        session_id=pending.session_id,
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        transcript="Yes",
    )

    assert cancelled is True
    assert result == {"status": "completed", "confirmation": "already_handled"}
    assert count() == 0
    assert outbound[-1]["text"] == "That confirmation has already been handled."


@pytest.mark.asyncio
async def test_cancel_message_invalidates_completed_turn_pending_action() -> None:
    principal = _principal()
    pending = _pending(principal, uuid.uuid4())
    gateway, store, outbound, _response_id, count = await _gateway(pending=pending)
    gateway.cancel_guard.clear()
    gateway._last_response_id = pending.original_response_id
    gateway.state = SimpleNamespace(session_id=pending.session_id)

    await gateway._handle_response_cancel(
        ResponseCancelMessage(
            type="client.response.cancel",
            response_id=pending.original_response_id,
            reason="user_cancelled_confirmation",
        )
    )

    stored = await store.get((pending.authenticated_user_id, pending.device_id, pending.session_id))
    assert stored is not None and stored.status == "CANCELLED"
    assert count() == 0
    assert [event["type"] for event in outbound[-2:]] == [
        "confirmation.resolved",
        "response.cancelled",
    ]


@pytest.mark.asyncio
async def test_tool_executor_persists_confirmation_request_after_validation() -> None:
    principal = _principal()
    session_id = uuid.uuid4()
    store = InMemoryVoiceConfirmationStore()
    registry = ToolRegistry()
    execution_count = 0

    async def handler(_context, _arguments):
        nonlocal execution_count
        execution_count += 1
        return {"ok": True}

    registry.register(
        name="create_task",
        description="Create a task after confirmation.",
        arguments_model=CreateTaskArguments,
        handler=handler,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    requested: list[PendingConfirmation] = []

    async def save_confirmation(call, arguments, tool):
        original_turn_id = uuid.uuid4()
        pending = PendingConfirmation.new(
            authenticated_user_id=principal.user_id,
            device_id=principal.device_id,
            session_id=session_id,
            original_turn_id=original_turn_id,
            original_response_id=uuid.uuid4(),
            tool_call_id=call.tool_call_id,
            tool_name=tool.name,
            validated_tool_arguments=arguments.model_dump(mode="json"),
            idempotency_key=(principal.user_id, original_turn_id, tool.name, call.tool_call_id),
            ttl_seconds=120,
        )
        requested.append(await store.create_or_get(pending))
        return True

    from app.llm.types import LLMToolCall

    executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
    result = await executor.execute(
        LLMToolCall(
            tool_call_id="confirmation-call",
            name="create_task",
            arguments={"title": "Call Rahul", "due_at": None, "notes": None},
        ),
        context=ToolExecutionContext(
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            scopes=frozenset({"tasks:write"}),
            confirmation_requested=save_confirmation,
        ),
    )

    assert result.error_code == "llm_tool_confirmation_required"
    assert result.executed is False
    assert execution_count == 0
    assert len(requested) == 1
    assert requested[0].status == "PENDING"
