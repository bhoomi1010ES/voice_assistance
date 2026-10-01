from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.okf.shadow import ShadowReadCapacity, perform_shadow_read, schedule_shadow_read
from app.okf.types import KnowledgeDisposition, KnowledgeResult, OkfEvidence


def _settings(**updates) -> Settings:
    user_id = updates.pop("user_id", uuid.uuid4())
    values = {
        "_env_file": None,
        "okf_enabled": True,
        "okf_sync_enabled": False,
        "okf_shadow_reads": True,
        "okf_shadow_user_ids": (user_id,),
        "knowledge_mode": "rag",
    }
    values.update(updates)
    return Settings(**values)


class FakeSession:
    def __init__(self, rows=()) -> None:
        self.rows = rows
        self.queries = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        self.queries += 1
        return SimpleNamespace(all=lambda: self.rows)

    async def connection(self):
        return self


@pytest.mark.asyncio
async def test_shadow_read_uses_factory_session_and_returns_content_free_metrics(
    monkeypatch,
) -> None:
    from app.okf import shadow

    user_id, session_id, source_id = (uuid.uuid4() for _ in range(3))
    independent_session = FakeSession()
    factory_calls = []

    def session_factory():
        factory_calls.append(True)
        return independent_session

    result = KnowledgeResult(
        engine="okf",
        status=KnowledgeDisposition.DIRECT_ANSWER,
        evidence=(
            OkfEvidence(
                concept_id=uuid.uuid4(),
                assertion_id=uuid.uuid4(),
                concept_type="preference",
                canonical_key="preferences/user/response-style",
                display_text="PRIVATE FACT MUST NOT BE LOGGED",
                status="active",
                source_memory_ids=(source_id,),
            ),
        ),
        duration_ms=1.0,
    )
    observed = {}

    class StubRetrieval:
        def __init__(self, _settings) -> None:
            pass

        async def retrieve(self, db, request, **kwargs):
            observed["session"] = db
            observed["request"] = request
            observed["trace"] = kwargs["trace"]
            return result

    monkeypatch.setattr(shadow, "OkfRetrievalService", StubRetrieval)
    observation = await perform_shadow_read(
        settings=_settings(user_id=user_id),
        session_factory=session_factory,
        user_id=user_id,
        session_id=session_id,
        query="PRIVATE QUERY MUST NOT BE LOGGED",
        now=datetime.now(UTC),
        rag_disposition="DIRECT_RAG",
        rag_evidence_ids=(source_id,),
        rag_latency_ms=4.0,
        scheduled_ns=time.perf_counter_ns(),
    )

    assert factory_calls == [True]
    assert observed["session"] is independent_session
    assert observed["request"].user_id == user_id
    assert observed["request"].session_id == session_id
    assert observation.fields["overlap_count"] == 1
    assert observation.fields["okf_provenance_coverage"] == 1.0
    assert independent_session.queries == 0
    assert observation.fields["session_setup_ms"] is not None
    assert observation.fields["connection_acquire_ms"] is not None
    assert observation.fields["rag_latency_ms"] == 4.0
    assert observation.fields["stage_timings_ms"] == {}
    assert "PRIVATE FACT" not in str(observation.fields)
    assert "PRIVATE QUERY" not in str(observation.fields)


@pytest.mark.asyncio
async def test_shadow_retrieval_succeeds_under_profiled_timeout(monkeypatch) -> None:
    from app.okf import shadow

    class SmallDelayRetrieval:
        def __init__(self, _settings) -> None:
            pass

        async def retrieve(self, *_args, **_kwargs):
            await asyncio.sleep(0.01)
            return KnowledgeResult(
                engine="okf",
                status=KnowledgeDisposition.NO_RESULT,
                duration_ms=10.0,
            )

    monkeypatch.setattr(shadow, "OkfRetrievalService", SmallDelayRetrieval)
    observation = await perform_shadow_read(
        settings=_settings(okf_retrieval_timeout_ms=300),
        session_factory=lambda: FakeSession(),
        user_id=uuid.uuid4(),
        session_id=None,
        query="A bounded synthetic diagnostic query",
        now=datetime.now(UTC),
        rag_disposition="NO_RESULT",
        rag_evidence_ids=(),
    )

    assert observation.fields["shadow_status"] == "completed"
    assert observation.fields["okf_disposition"] == "no_result"


@pytest.mark.asyncio
async def test_shadow_timeout_is_bounded(monkeypatch) -> None:
    from app.okf import shadow

    class SlowRetrieval:
        def __init__(self, _settings) -> None:
            pass

        async def retrieve(self, *_args, **_kwargs):
            await asyncio.sleep(0.05)

    monkeypatch.setattr(shadow, "OkfRetrievalService", SlowRetrieval)
    observation = await perform_shadow_read(
        settings=_settings(okf_retrieval_timeout_ms=1),
        session_factory=lambda: FakeSession(),
        user_id=uuid.uuid4(),
        session_id=None,
        query="query",
        now=datetime.now(UTC),
        rag_disposition="NO_RESULT",
        rag_evidence_ids=(),
    )
    assert observation.fields["okf_disposition"] == "unavailable"
    assert observation.fields["okf_reason"] == "retrieval_timeout"


def test_shadow_capacity_is_nonblocking_and_bounded() -> None:
    capacity = ShadowReadCapacity(1)

    assert capacity.try_acquire() is True
    assert capacity.active == 1
    assert capacity.try_acquire() is False
    capacity.release()
    assert capacity.active == 0
    assert capacity.try_acquire() is True
    capacity.release()


@pytest.mark.asyncio
async def test_shared_shadow_scheduler_accepts_case_correlation_without_logging_query(
    monkeypatch, caplog
) -> None:
    import logging

    from app.okf import shadow

    user_id = uuid.uuid4()
    settings = _settings(user_id=user_id, okf_shadow_max_concurrent=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = []

    async def blocked_read(**_kwargs):
        entered.set()
        await release.wait()
        return shadow.ShadowReadObservation(
            fields={"shadow_status": "completed", "latency_ms": 1.0}
        )

    monkeypatch.setattr(shadow, "perform_shadow_read", blocked_read)
    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    tasks = set()
    capacity = ShadowReadCapacity(1)
    result = schedule_shadow_read(
        settings=settings,
        session_factory=object(),
        user_id=user_id,
        session_id=None,
        query="PRIVATE LABELED QUERY",
        now=datetime.now(UTC),
        rag_disposition="direct_answer",
        rag_evidence_ids=(),
        rag_latency_ms=3.0,
        capacity=capacity,
        task_set=tasks,
        correlation={"run_id": "run-1", "case_id": "case-1"},
        on_complete=completed.append,
    )

    assert result == "scheduled"
    assert capacity.active == 1
    assert not entered.is_set()
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()
    await asyncio.gather(*tuple(tasks))
    assert capacity.active == 0
    assert completed[0].fields["shadow_status"] == "completed"
    event = next(
        record for record in caplog.records if getattr(record, "event", None) == "okf.shadow.read"
    )
    assert event.case_id == "case-1"
    assert event.run_id == "run-1"
    assert "PRIVATE LABELED QUERY" not in repr(event.__dict__)


@pytest.mark.asyncio
async def test_gateway_shadow_schedule_returns_without_awaiting_observation(
    monkeypatch, caplog
) -> None:
    from app.okf import shadow
    from app.websocket.gateway import VoiceGateway

    caplog.set_level(logging.INFO, logger="voice-assistance-backend")
    user_id, session_id, turn_id, response_id = (uuid.uuid4() for _ in range(4))
    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.settings = _settings(user_id=user_id, okf_shadow_max_concurrent=1)
    gateway.principal = SimpleNamespace(user_id=user_id)
    gateway.session_factory = object()
    gateway._okf_shadow_capacity = ShadowReadCapacity(1)
    gateway._okf_shadow_tasks = set()
    gateway._now_datetime = lambda: datetime.now(UTC)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_read(**_kwargs):
        entered.set()
        await release.wait()
        return shadow.ShadowReadObservation(
            fields={"shadow_status": "completed", "latency_ms": 1.0}
        )

    monkeypatch.setattr(shadow, "perform_shadow_read", blocked_read)
    scheduled = gateway._schedule_okf_shadow_read(
        transcript="PRIVATE SHADOW QUERY MUST NOT BE LOGGED",
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        route_prechecked=True,
        rag_disposition="DIRECT_RAG",
        rag_evidence_ids=(),
    )

    assert scheduled is None
    assert gateway._okf_shadow_capacity.active == 1
    assert not entered.is_set()
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()
    await asyncio.gather(*gateway._okf_shadow_tasks)
    assert gateway._okf_shadow_capacity.active == 0
    event = next(
        record for record in caplog.records if getattr(record, "event", None) == "okf.shadow.read"
    )
    assert event.shadow_status == "completed"
    assert "PRIVATE SHADOW QUERY" not in repr(event.__dict__)


def test_shadow_is_silent_for_users_outside_explicit_disposable_allowlist() -> None:
    from app.websocket.gateway import VoiceGateway

    allowed_id = uuid.uuid4()
    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.settings = _settings(user_id=allowed_id)
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.session_factory = object()
    gateway._okf_shadow_capacity = ShadowReadCapacity(1)
    gateway._okf_shadow_tasks = set()

    result = gateway._schedule_okf_shadow_read(
        transcript="Which response style do I prefer?",
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        route_prechecked=True,
        rag_disposition="DIRECT_RAG",
    )

    assert result is None
    assert gateway._okf_shadow_capacity.active == 0
    assert gateway._okf_shadow_tasks == set()


@pytest.mark.asyncio
async def test_shadow_capacity_and_memory_route_gates_skip_without_waiting() -> None:
    from app.websocket.gateway import VoiceGateway

    user_id = uuid.uuid4()
    gateway = VoiceGateway.__new__(VoiceGateway)
    gateway.settings = _settings(user_id=user_id, okf_shadow_max_concurrent=1)
    gateway.principal = SimpleNamespace(user_id=user_id)
    gateway.session_factory = object()
    gateway._okf_shadow_capacity = ShadowReadCapacity(1)
    gateway._okf_shadow_tasks = set()

    # A general route is not eligible even if the owner is in the disposable cohort.
    gateway._schedule_okf_shadow_read(
        transcript="What is the weather today?",
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        route_prechecked=False,
    )
    assert gateway._okf_shadow_capacity.active == 0

    # A full process-wide cap skips immediately rather than queuing a waiter.
    assert gateway._okf_shadow_capacity.try_acquire() is True
    gateway._schedule_okf_shadow_read(
        transcript="Which response style do I prefer?",
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        route_prechecked=True,
        rag_disposition="DIRECT_RAG",
    )
    assert gateway._okf_shadow_capacity.active == 1
    assert gateway._okf_shadow_tasks == set()
    gateway._okf_shadow_capacity.release()
