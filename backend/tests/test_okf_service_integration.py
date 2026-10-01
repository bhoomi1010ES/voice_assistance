from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import (
    AuthSession,
    Device,
    MemoryItem,
    OkfConcept,
    OkfConceptAssertion,
    OkfConceptSource,
    OkfConceptVersion,
    OkfSyncJob,
    User,
    VoiceSession,
)
from app.okf.jobs import OkfSyncWorker, enqueue_memory_sync
from app.okf.lifecycle import OkfLifecycleService
from app.okf.policy import map_memory_to_proposal
from app.okf.repository import OkfSourceUnavailable
from app.okf.retrieval import OkfRetrievalService
from app.okf.scoped_sync import ScopedSyncValidationError, process_only
from app.okf.service import OkfKnowledgeService
from app.okf.shadow import perform_shadow_read
from app.okf.types import KnowledgeDisposition, KnowledgeRequest

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_okf_domain_transactions_are_versioned_isolated_and_idempotent() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run PostgreSQL integration checks.")
    if os.getenv("RUN_OKF_SERVICE_TESTS") != "1":
        pytest.skip("Set RUN_OKF_SERVICE_TESTS=1 to create a disposable domain-test DB.")

    source_url = make_url(Settings().database_dsn)
    database_name = f"okf_domain_{uuid.uuid4().hex[:12]}"
    test_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(source_url)

    async def create_database() -> bool:
        async with admin_engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            allowed = await connection.scalar(
                text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
            if not allowed:
                return False
            await connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        return True

    if not asyncio.run(create_database()):
        asyncio.run(admin_engine.dispose())
        pytest.skip("Connected PostgreSQL role cannot create a disposable database.")
    asyncio.run(admin_engine.dispose())

    environment = os.environ.copy()
    environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    try:
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=180,
        )
        assert upgrade.returncode == 0, upgrade.stderr
        asyncio.run(_exercise_domain(test_url))
        asyncio.run(_exercise_worker(test_url))
        asyncio.run(_exercise_scoped_worker(test_url))
    finally:
        asyncio.run(_drop_database(source_url, database_name))


async def _exercise_domain(url: URL) -> None:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, okf_enabled=True, okf_policy_version="okf-v1")
    service = OkfKnowledgeService(settings=settings)
    lifecycle = OkfLifecycleService(policy_version="okf-v1")
    now = datetime.now(UTC).replace(microsecond=0)
    owner_id, device_id, auth_session_id, session_id = [uuid.uuid4() for _ in range(4)]
    other_owner_id, other_device_id, other_auth_id, other_session_id = [
        uuid.uuid4() for _ in range(4)
    ]
    memory_ids = (uuid.uuid4(), uuid.uuid4())
    project_memory_id = uuid.uuid4()
    superseding_memory_id = uuid.uuid4()
    foreign_memory_id = uuid.uuid4()
    try:
        async with factory() as db:
            async with db.begin():
                await _add_owner_session(
                    db,
                    now=now,
                    user_id=owner_id,
                    device_id=device_id,
                    auth_session_id=auth_session_id,
                    session_id=session_id,
                )
                await _add_owner_session(
                    db,
                    now=now,
                    user_id=other_owner_id,
                    device_id=other_device_id,
                    auth_session_id=other_auth_id,
                    session_id=other_session_id,
                )
                await _add_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=memory_ids[0],
                    value="concise",
                )
                db.add(
                    OkfSyncJob(
                        user_id=owner_id,
                        memory_id=memory_ids[0],
                        event_type="upsert_memory",
                        idempotency_key=f"upsert_memory:{memory_ids[0]}:okf-v1",
                        status="pending",
                        attempts=0,
                        available_at=now,
                        policy_version="okf-v1",
                    )
                )
                await _add_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=memory_ids[1],
                    value="detailed",
                )
                await _add_project_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=project_memory_id,
                )
                await _add_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=superseding_memory_id,
                    value="succinct",
                )
                await _add_memory(
                    db,
                    now=now,
                    user_id=other_owner_id,
                    session_id=other_session_id,
                    memory_id=foreign_memory_id,
                    value="private",
                )

        async def apply(memory_id: uuid.UUID) -> str:
            async with factory() as db:
                async with db.begin():
                    memory = await db.scalar(select(MemoryItem).where(MemoryItem.id == memory_id))
                    proposal = map_memory_to_proposal(memory)
                    assert proposal is not None
                    outcome = await service.sync_source_memory(
                        db, user_id=owner_id, proposal=proposal
                    )
                    return outcome.status

        conflicting_outcomes = await asyncio.gather(*(apply(item) for item in memory_ids))
        assert sorted(conflicting_outcomes) == ["contested", "created"]

        # Replaying identical source evidence does not append a version or
        # duplicate a provenance link.
        assert await apply(memory_ids[0]) == "unchanged"
        async with factory() as db:
            concept = await db.scalar(
                select(OkfConcept).where(
                    OkfConcept.user_id == owner_id,
                    OkfConcept.canonical_key == "preferences/response-style",
                )
            )
            assert concept is not None and concept.status == "contested"
            assertions = list(
                (
                    await db.scalars(
                        select(OkfConceptAssertion).where(
                            OkfConceptAssertion.user_id == owner_id,
                            OkfConceptAssertion.concept_id == concept.id,
                        )
                    )
                ).all()
            )
            assert len(assertions) == 2
            assert (
                await db.scalar(
                    select(func.count(OkfConceptVersion.id)).where(
                        OkfConceptVersion.user_id == owner_id
                    )
                )
                == 2
            )
            assert (
                await db.scalar(
                    select(func.count(OkfConceptSource.memory_id)).where(
                        OkfConceptSource.user_id == owner_id,
                        OkfConceptSource.memory_id == memory_ids[0],
                    )
                )
                == 1
            )
            assert (
                await db.scalar(
                    select(func.count(OkfConcept.id)).where(OkfConcept.user_id == other_owner_id)
                )
                == 0
            )

        async with factory() as db:
            async with db.begin():
                project_outcomes = await service.sync_memory(
                    db, user_id=owner_id, memory_id=project_memory_id
                )
                assert len(project_outcomes) == 2
        async with factory() as db:
            project_keys = set(
                (
                    await db.scalars(
                        select(OkfConcept.canonical_key).where(OkfConcept.user_id == owner_id)
                    )
                ).all()
            )
            assert "projects/voice-assistant" in project_keys
            assert "projects/voice-assistant/framework" in project_keys
            project_child = await db.scalar(
                select(OkfConcept).where(
                    OkfConcept.user_id == owner_id,
                    OkfConcept.canonical_key == "projects/voice-assistant/framework",
                )
            )
            project_parent_id = await db.scalar(
                select(OkfConcept.id).where(
                    OkfConcept.user_id == owner_id,
                    OkfConcept.canonical_key == "projects/voice-assistant",
                )
            )
            assert project_child is not None
            assert project_child.parent_concept_id == project_parent_id

        async with factory() as db:
            async with db.begin():
                superseding_memory = await db.scalar(
                    select(MemoryItem).where(MemoryItem.id == superseding_memory_id)
                )
                superseding_proposal = map_memory_to_proposal(superseding_memory)
                assert superseding_proposal is not None
                previous_assertion = await db.scalar(
                    select(OkfConceptAssertion).where(
                        OkfConceptAssertion.user_id == owner_id,
                        OkfConceptAssertion.concept_id
                        == select(OkfConcept.id)
                        .where(
                            OkfConcept.user_id == owner_id,
                            OkfConcept.canonical_key == "preferences/response-style",
                        )
                        .scalar_subquery(),
                        OkfConceptAssertion.value_json == {"value": "concise"},
                    )
                )
                assert previous_assertion is not None
                superseding_proposal = superseding_proposal.model_copy(
                    update={"supersedes_assertion_id": previous_assertion.id}
                )
                update_outcome = await service.sync_source_memory(
                    db, user_id=owner_id, proposal=superseding_proposal
                )
                assert update_outcome.status == "updated"
        async with factory() as db:
            superseded = await db.scalar(
                select(OkfConceptAssertion).where(OkfConceptAssertion.id == previous_assertion.id)
            )
            assert superseded is not None and superseded.status == "superseded"
            assert superseded.current_version == 2
            assert (
                await db.scalar(
                    select(func.count(OkfConceptVersion.id)).where(
                        OkfConceptVersion.assertion_id == previous_assertion.id
                    )
                )
                == 2
            )
        assert await apply(memory_ids[0]) == "unchanged"
        async with factory() as db:
            assert (
                await db.scalar(
                    select(func.count(OkfConceptAssertion.id)).where(
                        OkfConceptAssertion.user_id == owner_id,
                        OkfConceptAssertion.concept_id
                        == select(OkfConcept.id)
                        .where(
                            OkfConcept.user_id == owner_id,
                            OkfConcept.canonical_key == "preferences/response-style",
                        )
                        .scalar_subquery(),
                    )
                )
                == 3
            )

        # A user cannot replay another user's source memory under their own ID.
        async with factory() as db:
            async with db.begin():
                foreign_memory = await db.scalar(
                    select(MemoryItem).where(MemoryItem.id == foreign_memory_id)
                )
                foreign_proposal = map_memory_to_proposal(foreign_memory)
                assert foreign_proposal is not None
                with pytest.raises(OkfSourceUnavailable, match="unavailable"):
                    await service.sync_source_memory(
                        db, user_id=owner_id, proposal=foreign_proposal
                    )

        # Synchronous source removal erases unsupported assertion content
        # before a caller deletes/excludes its memory.
        async with factory() as db:
            async with db.begin():
                removed_assertions = await lifecycle.remove_memory_source(
                    db, user_id=owner_id, memory_id=memory_ids[0]
                )
                assert len(removed_assertions) == 1
        async with factory() as db:
            concept = await db.scalar(
                select(OkfConcept).where(
                    OkfConcept.user_id == owner_id,
                    OkfConcept.canonical_key == "preferences/response-style",
                )
            )
            assert concept is not None and concept.status in {"active", "contested"}
            active_count = await db.scalar(
                select(func.count(OkfConceptAssertion.id)).where(
                    OkfConceptAssertion.user_id == owner_id,
                    OkfConceptAssertion.concept_id == concept.id,
                    OkfConceptAssertion.status == "active",
                )
            )
            assert active_count in {1, 2}
            assert concept.status == ("active" if active_count == 1 else "contested")
            assert (
                await db.scalar(
                    select(func.count(OkfConceptSource.memory_id)).where(
                        OkfConceptSource.user_id == owner_id,
                        OkfConceptSource.memory_id == memory_ids[0],
                    )
                )
                == 0
            )
            cancelled_upsert = await db.scalar(
                select(OkfSyncJob).where(
                    OkfSyncJob.user_id == owner_id,
                    OkfSyncJob.memory_id == memory_ids[0],
                    OkfSyncJob.event_type == "upsert_memory",
                )
            )
            assert cancelled_upsert is not None and cancelled_upsert.status == "cancelled"
        async with factory() as db:
            survivors = list(
                (
                    await db.scalars(
                        select(OkfConceptAssertion).where(
                            OkfConceptAssertion.user_id == owner_id,
                            OkfConceptAssertion.concept_id == concept.id,
                            OkfConceptAssertion.status == "active",
                        )
                    )
                ).all()
            )
            assert survivors
            survivor = survivors[0]
            retirement_source_id = await db.scalar(
                select(OkfConceptSource.memory_id)
                .join(
                    OkfConceptVersion,
                    OkfConceptVersion.id == OkfConceptSource.concept_version_id,
                )
                .where(
                    OkfConceptSource.user_id == owner_id,
                    OkfConceptSource.evidence_role == "supports",
                    OkfConceptVersion.assertion_id == survivor.id,
                    OkfConceptVersion.version == survivor.current_version,
                )
                .limit(1)
            )
            assert retirement_source_id is not None

        async with factory() as db:
            async with db.begin():
                retired = await service.retire_assertion(
                    db,
                    user_id=owner_id,
                    assertion_id=survivor.id,
                    source_memory_id=retirement_source_id,
                )
                assert retired.status == "retired"
        async with factory() as db:
            retired_assertion = await db.scalar(
                select(OkfConceptAssertion).where(OkfConceptAssertion.id == survivor.id)
            )
            assert retired_assertion is not None
            assert retired_assertion.current_version == 2
            assert retired_assertion.status == "retired"
            assert (
                await db.scalar(
                    select(func.count(OkfConceptVersion.id)).where(
                        OkfConceptVersion.assertion_id == survivor.id
                    )
                )
                == 2
            )

        async with factory() as db:
            async with db.begin():
                await lifecycle.purge_user(db, user_id=owner_id)
        async with factory() as db:
            jobs = list(
                (await db.scalars(select(OkfSyncJob).where(OkfSyncJob.user_id == owner_id))).all()
            )
            assert len(jobs) == 1
            assert jobs[0].event_type == "purge_user"
            assert jobs[0].memory_id is None
            assert (
                await db.scalar(
                    select(func.count(OkfConcept.id)).where(OkfConcept.user_id == owner_id)
                )
                == 0
            )
            assert (
                await db.scalar(
                    select(func.count(OkfConceptVersion.id)).where(
                        OkfConceptVersion.user_id == owner_id
                    )
                )
                == 0
            )

        async with factory() as db:
            async with db.begin():
                await lifecycle.exclude_session(db, user_id=owner_id, session_id=session_id)
        async with factory() as db:
            assert (
                await db.scalar(
                    select(func.count(OkfConcept.id)).where(OkfConcept.user_id == owner_id)
                )
                == 0
            )
    finally:
        await engine.dispose()


async def _exercise_worker(url: URL) -> None:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        okf_policy_version="okf-v1",
        okf_job_max_attempts=2,
        okf_retrieval_timeout_ms=2_000,
    )
    worker = OkfSyncWorker(settings)
    now = datetime.now(UTC).replace(microsecond=0)
    owner_id, device_id, auth_session_id, session_id, memory_id = [
        uuid.uuid4() for _ in range(5)
    ]
    try:
        # The domain exercise runs in this same disposable database and may
        # leave unrelated lifecycle jobs queued. Keep the worker assertions
        # deterministic by cancelling that prior fixture state first.
        async with factory() as db:
            async with db.begin():
                await db.execute(
                    update(OkfSyncJob)
                    .where(OkfSyncJob.status.in_(("pending", "retry_wait")))
                    .values(status="cancelled", locked_at=None)
                )

        async with factory() as db:
            async with db.begin():
                await _add_owner_session(
                    db,
                    now=now,
                    user_id=owner_id,
                    device_id=device_id,
                    auth_session_id=auth_session_id,
                    session_id=session_id,
                )
                await _add_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=memory_id,
                    value="concise",
                )
                assert await enqueue_memory_sync(
                    db,
                    user_id=owner_id,
                    memory_id=memory_id,
                    policy_version="okf-v1",
                )
                job = await db.scalar(
                    select(OkfSyncJob).where(
                        OkfSyncJob.user_id == owner_id,
                        OkfSyncJob.memory_id == memory_id,
                    )
                )
                assert job is not None and job.memory_generation == 0
                job_id = job.id

        # A second claimant skips a row held by the first claimant.
        async with factory() as first:
            async with first.begin():
                assert await worker.claim(first) == job_id
                async with factory() as second:
                    async with second.begin():
                        assert await worker.claim(second) is None

        async with factory() as db:
            async with db.begin():
                await worker.process(db, job_id=job_id)
        async with factory() as db:
            job = await db.scalar(select(OkfSyncJob).where(OkfSyncJob.id == job_id))
            assert job is not None and job.status == "completed" and job.attempts == 1
            concept_count = await db.scalar(
                select(func.count(OkfConcept.id)).where(OkfConcept.user_id == owner_id)
            )
            source_count = await db.scalar(
                select(func.count(OkfConceptSource.memory_id)).where(
                    OkfConceptSource.user_id == owner_id,
                    OkfConceptSource.memory_id == memory_id,
                )
            )
            assert concept_count == 1 and source_count == 1
            preference_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Which response style do I prefer?",
                    now=now,
                ),
            )
            assert preference_result.status == KnowledgeDisposition.DIRECT_ANSWER
            assert preference_result.evidence[0].source_memory_ids == (memory_id,)

        project_id = uuid.uuid4()
        async with factory() as db:
            async with db.begin():
                await _add_project_memory(
                    db,
                    now=now,
                    user_id=owner_id,
                    session_id=session_id,
                    memory_id=project_id,
                )
                await OkfKnowledgeService(settings=settings).sync_memory(
                    db, user_id=owner_id, memory_id=project_id
                )
        async with factory() as db:
            project_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Which framework does my Voice Assistant project use?",
                    now=now,
                ),
            )
            assert project_result.status == KnowledgeDisposition.CONTINUE_WITH_EVIDENCE
            assert {item.canonical_key for item in project_result.evidence} == {
                "projects/voice-assistant",
                "projects/voice-assistant/framework",
            }
            assert all(project_id in item.source_memory_ids for item in project_result.evidence)

        home_memory_id = uuid.uuid4()
        unrelated_profile_id = uuid.uuid4()
        async with factory() as db:
            async with db.begin():
                db.add(
                    MemoryItem(
                        id=home_memory_id,
                        user_id=owner_id,
                        content="My home location is Cedar Quay.",
                        memory_type="fact",
                        subject="user",
                        predicate="home_location",
                        object_json={"value": "Cedar Quay"},
                        confidence=0.9,
                        salience=0.8,
                        source_kind="explicit_tool",
                        source_session_id=session_id,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                db.add(
                    MemoryItem(
                        id=unrelated_profile_id,
                        user_id=owner_id,
                        content="My name is Test Subject.",
                        memory_type="fact",
                        subject="user",
                        predicate="name",
                        object_json={"value": "Test Subject"},
                        confidence=0.9,
                        salience=0.8,
                        source_kind="explicit_tool",
                        source_session_id=session_id,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.flush()
                await OkfKnowledgeService(settings=settings).sync_memory(
                    db, user_id=owner_id, memory_id=home_memory_id
                )
                await OkfKnowledgeService(settings=settings).sync_memory(
                    db, user_id=owner_id, memory_id=unrelated_profile_id
                )
        async with factory() as db:
            home_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Thank you. What is my home location? Thank you.",
                    now=now,
                    session_id=session_id,
                ),
            )
            assert home_result.status == KnowledgeDisposition.DIRECT_ANSWER
            assert len(home_result.evidence) == 1
            assert home_result.evidence[0].canonical_key == "profile/home-location"
            assert home_result.evidence[0].display_text == "Cedar Quay"
            assert home_result.evidence[0].source_memory_ids == (home_memory_id,)

            unrelated_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Thank you. What is my name? Thank you.",
                    now=now,
                    session_id=session_id,
                ),
            )
            assert unrelated_result.status == KnowledgeDisposition.DIRECT_ANSWER
            assert unrelated_result.evidence[0].canonical_key == "profile/name"
            assert unrelated_result.evidence[0].source_memory_ids == (unrelated_profile_id,)

        relationship_id = uuid.uuid4()
        async with factory() as db:
            async with db.begin():
                db.add(
                    MemoryItem(
                        id=relationship_id,
                        user_id=owner_id,
                        content="Rahul is my colleague.",
                        memory_type="relationship",
                        subject="Rahul",
                        predicate="relationship",
                        object_json={"target": "user", "kind": "colleague"},
                        confidence=0.9,
                        salience=0.8,
                        source_kind="explicit_tool",
                        source_session_id=session_id,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.flush()
                await OkfKnowledgeService(settings=settings).sync_memory(
                    db, user_id=owner_id, memory_id=relationship_id
                )
        async with factory() as db:
            relationship_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="What is my relationship with Rahul?",
                    now=now,
                ),
            )
            assert relationship_result.status == KnowledgeDisposition.DIRECT_ANSWER
            assert relationship_result.evidence[0].canonical_key == "relationships/rahul/user"
            assert relationship_result.evidence[0].source_memory_ids == (relationship_id,)

        # Simulate a duplicate delivery after a lost acknowledgement; the
        # deterministic source/provenance identity prevents a second mutation.
        async with factory() as db:
            async with db.begin():
                job = await db.scalar(
                    select(OkfSyncJob).where(OkfSyncJob.id == job_id).with_for_update()
                )
                assert job is not None
                job.status = "running"
                job.completed_at = None
                job.locked_at = datetime.now(UTC)
                job.attempts += 1
                await worker.process(db, job_id=job_id)
        async with factory() as db:
            assert (
                await db.scalar(
                    select(func.count(OkfConceptSource.memory_id)).where(
                        OkfConceptSource.user_id == owner_id,
                        OkfConceptSource.memory_id == memory_id,
                    )
                )
                == 1
            )

        # Shadow is exercised only against this randomly named disposable
        # owner and a separate session; its result is inspected, never injected.
        shadow_settings = Settings(
            _env_file=None,
            okf_enabled=True,
            okf_sync_enabled=True,
            okf_shadow_reads=True,
            okf_shadow_user_ids=(owner_id,),
            knowledge_mode="rag",
            okf_policy_version="okf-v1",
            okf_retrieval_timeout_ms=2_000,
        )
        shadow_observation = await perform_shadow_read(
            settings=shadow_settings,
            session_factory=factory,
            user_id=owner_id,
            session_id=session_id,
            query="Which response style do I prefer?",
            now=now,
            rag_disposition="DIRECT_RAG",
            rag_evidence_ids=(memory_id,),
        )
        assert shadow_observation.fields["shadow_status"] == "completed"
        assert shadow_observation.fields["okf_disposition"] == "direct_answer"
        assert shadow_observation.fields["overlap_count"] == 1
        assert shadow_observation.fields["okf_provenance_coverage"] == 1.0
        assert shadow_observation.fields["session_setup_ms"] is not None
        assert shadow_observation.fields["connection_acquire_ms"] is not None
        assert "total_retrieval" in shadow_observation.fields["stage_timings_ms"]

        async with factory() as db:
            foreign_result = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=uuid.uuid4(),
                    query="Which response style do I prefer?",
                    now=now,
                ),
            )
            assert foreign_result.status == KnowledgeDisposition.UNAVAILABLE

        # The lifecycle barrier must make a forgotten source unreadable before
        # any asynchronous removal job is acknowledged.
        async with factory() as db:
            async with db.begin():
                await OkfLifecycleService(policy_version="okf-v1").remove_memory_source(
                    db, user_id=owner_id, memory_id=memory_id
                )
        async with factory() as db:
            after_forget = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Which response style do I prefer?",
                    now=now,
                ),
            )
            assert after_forget.status == KnowledgeDisposition.NO_RESULT
        after_forget_shadow = await perform_shadow_read(
            settings=shadow_settings,
            session_factory=factory,
            user_id=owner_id,
            session_id=session_id,
            query="Which response style do I prefer?",
            now=now,
            rag_disposition="NO_RESULT",
            rag_evidence_ids=(),
        )
        assert after_forget_shadow.fields["okf_disposition"] == "no_result"
        assert after_forget_shadow.fields["no_result_agreement"] is True

        # Delete-all also purges all content-bearing concepts synchronously.
        async with factory() as db:
            async with db.begin():
                await OkfLifecycleService(policy_version="okf-v1").purge_user(
                    db, user_id=owner_id
                )
        async with factory() as db:
            after_delete = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_id,
                    query="Which response style do I prefer?",
                    now=now,
                ),
            )
            assert after_delete.status == KnowledgeDisposition.NO_RESULT
    finally:
        await engine.dispose()


async def _exercise_scoped_worker(url: URL) -> None:
    """Exact-ID execution must leave every non-scope job byte-for-byte unchanged."""

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = f"OKF6-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    other_run_id = f"OKF6-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    run_policy = f"okf-v1.{run_id}"
    other_policy = f"okf-v1.{other_run_id}"
    owner_a, owner_b, owner_c = [uuid.uuid4() for _ in range(3)]
    session_ids = [uuid.uuid4() for _ in range(3)]
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        okf_policy_version=run_policy,
        okf_job_max_attempts=3,
        okf_job_lease_seconds=60,
    )
    worker = OkfSyncWorker(settings)

    async def add_scoped_memory(
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID,
        run_tag: str,
        subject: str,
        predicate: str,
        value: str,
        status: str = "active",
        supersedes_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        memory_id = uuid.uuid4()
        async with factory() as db, db.begin():
            db.add(
                MemoryItem(
                    id=memory_id,
                    user_id=owner_id,
                    content=f"{subject} {predicate.replace('_', ' ')} is {value}.",
                    metadata_json={"okf_run_id": run_tag, "case_id": "scoped-worker-test"},
                    memory_type="project" if predicate == "framework" else "preference",
                    subject=subject,
                    predicate=predicate,
                    object_json={"value": value},
                    confidence=1.0,
                    salience=0.9,
                    source_kind="manual_api",
                    source_session_id=session_id,
                    supersedes_id=supersedes_id,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
        return memory_id

    async def add_job(
        *,
        owner_id: uuid.UUID,
        memory_id: uuid.UUID,
        policy: str,
        generation: int | None = None,
    ) -> uuid.UUID:
        async with factory() as db, db.begin():
            inserted = await enqueue_memory_sync(
                db, user_id=owner_id, memory_id=memory_id, policy_version=policy
            )
            assert inserted
            job = await db.scalar(
                select(OkfSyncJob).where(
                    OkfSyncJob.user_id == owner_id,
                    OkfSyncJob.memory_id == memory_id,
                    OkfSyncJob.policy_version == policy,
                )
            )
            assert job is not None
            if generation is not None and generation != job.memory_generation:
                job.memory_generation = generation
                job.idempotency_key = f"upsert_memory:{memory_id}:{generation}:{policy}"
            return job.id

    async def snapshot_jobs() -> dict[uuid.UUID, tuple[object, ...]]:
        async with factory() as db:
            rows = list((await db.scalars(select(OkfSyncJob).order_by(OkfSyncJob.id))).all())
            return {
                row.id: (
                    row.user_id,
                    row.memory_id,
                    row.event_type,
                    row.idempotency_key,
                    row.status,
                    row.attempts,
                    row.available_at,
                    row.locked_at,
                    row.completed_at,
                    row.updated_at,
                    row.last_error_code,
                    row.policy_version,
                    row.memory_generation,
                )
                for row in rows
            }

    try:
        async with factory() as db, db.begin():
            owners = zip((owner_a, owner_b, owner_c), session_ids, strict=True)
            for index, (owner_id, session_id) in enumerate(owners):
                await _add_owner_session(
                    db,
                    now=now,
                    user_id=owner_id,
                    device_id=uuid.uuid4(),
                    auth_session_id=uuid.uuid4(),
                    session_id=session_id,
                )
                owner = await db.get(User, owner_id)
                assert owner is not None
                owner.name = "OKF" if index == 0 else f"Synthetic owner {index}"

        # Run A contains a superseded source plus the active replacement and a
        # separate source that will later exercise lifecycle removal.
        old_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="Scoped Run A Project",
            predicate="framework",
            value="Legacy Framework",
            status="superseded",
        )
        active_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="Scoped Run A Project",
            predicate="framework",
            value="Rust",
            supersedes_id=old_memory,
        )
        removable_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="user",
            predicate="response_style",
            value="concise",
        )
        run_a_job_ids = {
            await add_job(owner_id=owner_a, memory_id=old_memory, policy=run_policy),
            await add_job(owner_id=owner_a, memory_id=active_memory, policy=run_policy),
            await add_job(owner_id=owner_a, memory_id=removable_memory, policy=run_policy),
        }

        run_b_memory = await add_scoped_memory(
            owner_id=owner_b,
            session_id=session_ids[1],
            run_tag=other_run_id,
            subject="Run B Project",
            predicate="framework",
            value="Java",
        )
        run_b_job = await add_job(owner_id=owner_b, memory_id=run_b_memory, policy=other_policy)
        owner_mismatch_memory = await add_scoped_memory(
            owner_id=owner_b,
            session_id=session_ids[1],
            run_tag=run_id,
            subject="Run A Owner Mismatch Project",
            predicate="framework",
            value="Scala",
        )
        owner_mismatch_job = await add_job(
            owner_id=owner_b, memory_id=owner_mismatch_memory, policy=run_policy
        )
        same_owner_other_run_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=other_run_id,
            subject="Same Owner Other Run Project",
            predicate="framework",
            value="Go",
        )
        same_owner_other_run_job = await add_job(
            owner_id=owner_a, memory_id=same_owner_other_run_memory, policy=other_policy
        )
        unrelated_memory = await add_scoped_memory(
            owner_id=owner_c,
            session_id=session_ids[2],
            run_tag="legacy-unrelated",
            subject="Unrelated Project",
            predicate="framework",
            value="Python",
        )
        unrelated_job = await add_job(
            owner_id=owner_c,
            memory_id=unrelated_memory,
            policy="okf-v1.legacy-unrelated",
        )
        leased_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="Leased Run A Project",
            predicate="framework",
            value="Elixir",
        )
        leased_job = await add_job(
            owner_id=owner_a, memory_id=leased_memory, policy=run_policy
        )
        async with factory() as db, db.begin():
            leased = await db.get(OkfSyncJob, leased_job)
            assert leased is not None
            leased.status = "running"
            leased.attempts = 1
            leased.locked_at = now
            leased.updated_at = now
        async with factory() as db, db.begin():
            db.add(
                OkfSyncJob(
                    user_id=owner_a,
                    memory_id=None,
                    event_type="rebuild_user",
                    idempotency_key=f"rebuild_user:{owner_a}:0:{run_policy}",
                    status="pending",
                    attempts=0,
                    available_at=now,
                    policy_version=run_policy,
                    memory_generation=0,
                )
            )
        async with factory() as db:
            broad_job = await db.scalar(
                select(OkfSyncJob).where(
                    OkfSyncJob.user_id == owner_a,
                    OkfSyncJob.event_type == "rebuild_user",
                    OkfSyncJob.policy_version == run_policy,
                )
            )
            assert broad_job is not None
            broad_job_id = broad_job.id

        before = await snapshot_jobs()
        assert {run_b_job, same_owner_other_run_job, unrelated_job}.issubset(before)
        audit = await process_only(
            factory,
            worker,
            evaluation_run_id=run_id,
            owner_id=owner_a,
            approved_disposable_owner_ids={owner_a},
            job_ids=run_a_job_ids,
        )
        assert set(audit.requested_job_ids) == run_a_job_ids
        assert set(audit.processed_job_ids) == run_a_job_ids
        assert not audit.rejected_job_ids and not audit.skipped_job_ids and not audit.failures
        assert {job_id for job_id, _ in audit.job_types} == run_a_job_ids
        assert audit.started_at.tzinfo is not None and audit.ended_at.tzinfo is not None

        async with factory() as db:
            by_memory = {
                row.memory_id: row
                for row in (
                    await db.scalars(
                        select(OkfSyncJob).where(OkfSyncJob.id.in_(run_a_job_ids))
                    )
                ).all()
            }
            assert by_memory[old_memory].status == "cancelled"
            assert by_memory[active_memory].status == "completed"
            assert by_memory[removable_memory].status == "completed"
            current = await OkfRetrievalService(settings).retrieve(
                db,
                KnowledgeRequest(
                    user_id=owner_a,
                    query="Which framework does Scoped Run A Project use?",
                    now=now,
                ),
            )
            assert current.status == KnowledgeDisposition.CONTINUE_WITH_EVIDENCE
            assert all(old_memory not in item.source_memory_ids for item in current.evidence)
            assert any(
                active_memory in item.source_memory_ids and item.display_text == "Rust"
                for item in current.evidence
            )

        after = await snapshot_jobs()
        for unrelated_id in before.keys() - run_a_job_ids:
            assert after[unrelated_id] == before[unrelated_id]

        # Idempotent enqueue remains a no-op for an already represented source.
        async with factory() as db, db.begin():
            assert not await enqueue_memory_sync(
                db, user_id=owner_a, memory_id=active_memory, policy_version=run_policy
            )

        # Deletion is applied synchronously by the lifecycle barrier; the
        # exact resulting remove_memory job can then be acknowledged in scope.
        async with factory() as db, db.begin():
            memory = await db.get(MemoryItem, removable_memory)
            assert memory is not None
            memory.status = "deleted"
            await OkfLifecycleService(
                policy_version=run_policy,
                sync_enabled=True,
            ).remove_memory_source(db, user_id=owner_a, memory_id=removable_memory)
        async with factory() as db:
            removal = await db.scalar(
                select(OkfSyncJob).where(
                    OkfSyncJob.user_id == owner_a,
                    OkfSyncJob.memory_id == removable_memory,
                    OkfSyncJob.event_type == "remove_memory",
                    OkfSyncJob.policy_version == run_policy,
                )
            )
            assert removal is not None
            removal_id = removal.id
        removal_audit = await process_only(
            factory,
            worker,
            evaluation_run_id=run_id,
            owner_id=owner_a,
            approved_disposable_owner_ids={owner_a},
            job_ids=(removal_id,),
        )
        assert removal_audit.processed_job_ids == (removal_id,)
        async with factory() as db:
            removal = await db.get(OkfSyncJob, removal_id)
            assert removal is not None and removal.status == "completed"
            assert (
                await db.scalar(
                    select(func.count(OkfConceptSource.memory_id)).where(
                        OkfConceptSource.user_id == owner_a,
                        OkfConceptSource.memory_id == removable_memory,
                    )
                )
                == 0
            )

        # A stale generation job is cancelled by the existing fence, and its
        # replacement is not processed until that exact new ID is requested.
        stale_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="Generation Fence Project",
            predicate="framework",
            value="Rust",
        )
        stale_job = await add_job(
            owner_id=owner_a, memory_id=stale_memory, policy=run_policy, generation=0
        )
        stale_audit = await process_only(
            factory,
            worker,
            evaluation_run_id=run_id,
            owner_id=owner_a,
            approved_disposable_owner_ids={owner_a},
            job_ids=(stale_job,),
        )
        assert stale_audit.processed_job_ids == (stale_job,)
        async with factory() as db:
            stale_row = await db.get(OkfSyncJob, stale_job)
            assert stale_row is not None and stale_row.status == "cancelled"
            replacement = await db.scalar(
                select(OkfSyncJob).where(
                    OkfSyncJob.user_id == owner_a,
                    OkfSyncJob.memory_id == stale_memory,
                    OkfSyncJob.id != stale_job,
                    OkfSyncJob.policy_version == run_policy,
                )
            )
            assert replacement is not None and replacement.status == "pending"
            replacement_id = replacement.id
        rebased = await process_only(
            factory,
            worker,
            evaluation_run_id=run_id,
            owner_id=owner_a,
            approved_disposable_owner_ids={owner_a},
            job_ids=(replacement_id,),
        )
        assert rebased.processed_job_ids == (replacement_id,)

        # Scoped failures use the existing retry/dead-letter transition; the
        # stable failure record contains only an exception class, never text.
        retry_memory = await add_scoped_memory(
            owner_id=owner_a,
            session_id=session_ids[0],
            run_tag=run_id,
            subject="Retry Run A Project",
            predicate="framework",
            value="Swift",
        )
        retry_job = await add_job(owner_id=owner_a, memory_id=retry_memory, policy=run_policy)
        original_process = worker.process

        async def fail_once(_session, *, job_id: uuid.UUID) -> None:
            assert job_id == retry_job
            raise RuntimeError("synthetic failure text must not be audited")

        worker.process = fail_once
        try:
            failed_audit = await process_only(
                factory,
                worker,
                evaluation_run_id=run_id,
                owner_id=owner_a,
                approved_disposable_owner_ids={owner_a},
                job_ids=(retry_job,),
            )
        finally:
            worker.process = original_process
        assert failed_audit.failures == ((retry_job, "RuntimeError"),)
        async with factory() as db, db.begin():
            retry_row = await db.get(OkfSyncJob, retry_job)
            assert retry_row is not None
            assert retry_row.status == "retry_wait"
            assert retry_row.attempts == 1
            assert retry_row.last_error_code == "scoped_worker_runtimeerror"
            retry_row.available_at = datetime.now(UTC)
        retried = await process_only(
            factory,
            worker,
            evaluation_run_id=run_id,
            owner_id=owner_a,
            approved_disposable_owner_ids={owner_a},
            job_ids=(retry_job,),
        )
        assert retried.processed_job_ids == (retry_job,)
        async with factory() as db:
            retry_row = await db.get(OkfSyncJob, retry_job)
            assert retry_row is not None and retry_row.status == "completed"
            assert retry_row.attempts == 2

        # Every invalid scope is rejected before the requested rows change.
        async def assert_rejected(
            *,
            requested_run: str,
            expected_owner: uuid.UUID,
            approved: set[uuid.UUID],
            ids: tuple[uuid.UUID, ...],
            code: str,
        ) -> None:
            before_reject = await snapshot_jobs()
            with pytest.raises(ScopedSyncValidationError) as error:
                await process_only(
                    factory,
                    worker,
                    evaluation_run_id=requested_run,
                    owner_id=expected_owner,
                    approved_disposable_owner_ids=approved,
                    job_ids=ids,
                )
            assert error.value.code == code
            after_reject = await snapshot_jobs()
            assert after_reject == before_reject

        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(run_b_job,),
            code="job_scope_or_state_mismatch",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(owner_mismatch_job,),
            code="job_scope_or_state_mismatch",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(same_owner_other_run_job,),
            code="job_scope_or_state_mismatch",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(uuid.uuid4(),),
            code="unknown_job_id",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(),
            code="empty_job_scope",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(leased_job,),
            code="job_scope_or_state_mismatch",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved={owner_a},
            ids=(broad_job_id,),
            code="job_scope_or_state_mismatch",
        )
        await assert_rejected(
            requested_run=run_id,
            expected_owner=owner_a,
            approved=set(),
            ids=(run_b_job,),
            code="owner_not_approved",
        )
        final_state = await snapshot_jobs()
        for unrelated_id in before.keys() - run_a_job_ids:
            assert final_state[unrelated_id] == before[unrelated_id]
    finally:
        await engine.dispose()


async def _add_owner_session(
    db,
    *,
    now: datetime,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    auth_session_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    db.add(
        User(
            id=user_id,
            email=f"okf-domain-{user_id.hex}@example.invalid",
            password_hash="fixture-only",
            memory_enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    db.add(
        Device(
            id=device_id,
            user_id=user_id,
            device_identifier=f"okf-domain-{device_id.hex}",
            platform="test",
            device_kind="synthetic",
            created_at=now,
            last_seen_at=now,
        )
    )
    await db.flush()
    db.add(
        AuthSession(
            id=auth_session_id,
            user_id=user_id,
            device_id=device_id,
            refresh_token_hash=uuid.uuid4().hex * 2,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=1),
        )
    )
    await db.flush()
    db.add(
        VoiceSession(
            id=session_id,
            user_id=user_id,
            device_id=device_id,
            auth_session_id=auth_session_id,
            protocol_version=1,
            client_metadata={},
            status="completed",
            started_at=now,
            last_activity_at=now,
            ended_at=now,
            created_at=now,
        )
    )
    await db.flush()


async def _add_memory(
    db,
    *,
    now: datetime,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    memory_id: uuid.UUID,
    value: str,
) -> None:
    db.add(
        MemoryItem(
            id=memory_id,
            user_id=user_id,
            content=f"Prefers {value} responses.",
            memory_type="preference",
            subject="user",
            predicate="response_style",
            object_json={"value": value},
            confidence=0.9,
            salience=0.8,
            source_kind="explicit_tool",
            source_session_id=session_id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()


async def _add_project_memory(
    db,
    *,
    now: datetime,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    memory_id: uuid.UUID,
) -> None:
    db.add(
        MemoryItem(
            id=memory_id,
            user_id=user_id,
            content="The Voice Assistant project uses FastAPI.",
            memory_type="project",
            subject="Voice Assistant",
            predicate="framework",
            object_json={"value": "FastAPI"},
            confidence=0.9,
            salience=0.8,
            source_kind="explicit_tool",
            source_session_id=session_id,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()


async def _drop_database(source_url: URL, database_name: str) -> None:
    engine = create_async_engine(source_url)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await engine.dispose()
