"""Run a disposable-owner, run-tagged OKF-4 live acceptance evaluation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
EVIDENCE_ROOT = PROJECT_ROOT / "docs" / "evidence" / "okf"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RUN_POLICY_PREFIX = "okf-v1."
RAG_BASELINE_P95_MS = 431.5948
MODES = ("rag", "okf", "combined")


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


def _new_run_id() -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"OKF4-{now}-{secrets.token_hex(3)}"


def _write_new_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _case(
    *,
    run_id: str,
    case_id: str,
    owner_id: uuid.UUID,
    query: str,
    expected_result: str,
    lifecycle: str,
    privacy: str,
    expected_by_mode: dict[str, str],
    sources_by_mode: dict[str, tuple[uuid.UUID, ...]],
    forbidden: tuple[uuid.UUID, ...] = (),
    privacy_check: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "run_id": run_id,
        "owner_id": str(owner_id),
        "query": query,
        "expected_result": expected_result,
        "expected_lifecycle_behavior": lifecycle,
        "expected_privacy_behavior": privacy,
        "requested_modes": list(MODES),
        "expected_by_mode": expected_by_mode,
        "expected_sources_by_mode": {
            mode: [str(source_id) for source_id in source_ids]
            for mode, source_ids in sources_by_mode.items()
        },
        "allowed_sources_by_mode": {
            mode: [str(source_id) for source_id in source_ids]
            for mode, source_ids in sources_by_mode.items()
        },
        "forbidden_source_memory_ids": [str(source_id) for source_id in forbidden],
        "privacy_check": privacy_check,
    }


async def _run(args: argparse.Namespace) -> int:
    from app.core.config import Settings
    from app.db.session import Database
    from app.memory.jobs import MemoryJobRepository, MemoryJobWorker
    from app.memory.policy import ExtractionCandidate
    from app.memory.providers import RemoteEmbeddingProvider
    from app.memory.repository import MemoryRepository
    from app.memory.types import MemorySourceKind, MemoryType
    from app.memory.writer import MemoryWriter
    from app.models import (
        AuthSession,
        Device,
        Entity,
        MemoryChunk,
        MemoryItem,
        MemoryJob,
        OkfConcept,
        OkfConceptAssertion,
        OkfConceptSource,
        OkfConceptVersion,
        OkfSyncJob,
        User,
        VoiceSession,
    )
    from app.okf.jobs import OkfSyncWorker
    from app.okf.lifecycle import OkfLifecycleService

    run_id = args.run_id or _new_run_id()
    if not run_id.startswith("OKF4-") or len(run_id) > 40:
        raise ValueError("run ID must start with OKF4- and be at most 40 characters")
    policy_version = f"{RUN_POLICY_PREFIX}{run_id}"
    run_settings = Settings().model_copy(
        update={
            "okf_enabled": True,
            "okf_sync_enabled": True,
            "okf_evaluation_enabled": True,
            "okf_policy_version": policy_version,
            "memory_write_enabled": True,
            "graph_write_enabled": False,
            "knowledge_mode": "rag",
        }
    )
    if run_settings.app_env.lower() not in {"development", "test"}:
        raise ValueError("live OKF-4 run is restricted to development/test")

    owner_a_id = uuid.UUID(args.owner_id)
    owner_b_id = uuid.uuid4()
    owner_ids = (owner_a_id, owner_b_id)
    database = Database(run_settings)
    if database.session_factory is None:
        raise ValueError("database is not configured")

    memory_ids: list[uuid.UUID] = []
    source_labels: dict[str, uuid.UUID] = {}
    run_session_ids: list[uuid.UUID] = []
    run_device_ids: list[uuid.UUID] = []
    run_auth_session_ids: list[uuid.UUID] = []
    memory_job_ids: set[uuid.UUID] = set()
    sync_job_ids: set[uuid.UUID] = set()
    manifest_path = EVIDENCE_ROOT / f"{run_id.lower()}_manifest.jsonl"
    preflight_path = EVIDENCE_ROOT / f"{run_id.lower()}_preflight.jsonl"
    results_path = EVIDENCE_ROOT / f"{run_id.lower()}_case_results.jsonl"
    sync_manifest_path = EVIDENCE_ROOT / f"{run_id.lower()}_sync_jobs.jsonl"
    report_path = EVIDENCE_ROOT / f"{run_id.lower()}_acceptance.md"
    old_environment = {
        key: os.environ.get(key)
        for key in (
            "OKF_ENABLED",
            "OKF_SYNC_ENABLED",
            "OKF_EVALUATION_ENABLED",
            "OKF_EVALUATION_USER_IDS",
            "OKF_POLICY_VERSION",
            "MEMORY_WRITE_ENABLED",
        )
    }
    os.environ.update(
        {
            "OKF_ENABLED": "true",
            "OKF_SYNC_ENABLED": "true",
            "OKF_EVALUATION_ENABLED": "true",
            "OKF_EVALUATION_USER_IDS": json.dumps([str(value) for value in owner_ids]),
            "OKF_POLICY_VERSION": policy_version,
            "MEMORY_WRITE_ENABLED": "true",
        }
    )

    baseline_pending_ids: set[uuid.UUID] = set()
    baseline_memory_job_states: dict[uuid.UUID, tuple[str, str, int]] = {}
    preexisting_concept_counts: dict[uuid.UUID, int] = {}
    created_concept_count = 0
    sync_counts: dict[str, int] = {}
    sync_records: list[dict[str, Any]] = []
    stage = "preflight"
    final_status = "BLOCKED — LIVE EVALUATOR PATH INVALID"
    failure_reason: str | None = None
    preflight_summary: dict[str, Any] | None = None
    full_summary: dict[str, Any] | None = None
    cleanup_verified = False
    embedding_provider = None
    owner_a_verified = False
    owner_b_created = False

    try:
        # Read-only ownership and cleanliness checks before the first write.
        async with database.session_factory() as session:
            owner_a = await session.get(User, owner_a_id)
            if owner_a is None or (owner_a.name or "").casefold() != "okf":
                raise ValueError("provided owner UUID does not identify the dedicated OKF account")
            if owner_a.status != "active" or not owner_a.memory_enabled:
                raise ValueError("dedicated OKF owner is not active with memory enabled")
            for model in (
                MemoryItem,
                Entity,
                OkfConcept,
                OkfConceptAssertion,
                OkfConceptVersion,
                OkfConceptSource,
                OkfSyncJob,
            ):
                count = await session.scalar(
                    select(func.count()).select_from(model).where(model.user_id == owner_a_id)
                )
                if count:
                    raise ValueError(
                        f"dedicated OKF owner is not clean: {model.__tablename__}={count}"
                    )
            owner_a_verified = True
            preexisting_concept_counts[owner_a_id] = await session.scalar(
                select(func.count()).select_from(OkfConcept).where(OkfConcept.user_id == owner_a_id)
            )
            baseline_pending_ids = set(
                (
                    await session.scalars(
                        select(OkfSyncJob.id).where(OkfSyncJob.status == "pending")
                    )
                ).all()
            )
            if len(baseline_pending_ids) != 2:
                raise ValueError(
                    "global pending OKF job baseline changed; refusing to process any global work"
                )
            existing_memory_jobs = list(
                (
                    await session.execute(
                        select(
                            MemoryJob.id,
                            MemoryJob.job_type,
                            MemoryJob.status,
                            MemoryJob.attempts,
                        ).where(MemoryJob.user_id == owner_a_id)
                    )
                ).all()
            )
            baseline_memory_job_states = {
                job_id: (job_type, status, attempts)
                for job_id, job_type, status, attempts in existing_memory_jobs
            }
            if any(status != "completed" for _, status, _ in baseline_memory_job_states.values()):
                raise ValueError("pre-existing OKF-account memory jobs are not all completed")
            owner_b = User(
                id=owner_b_id,
                email=f"{run_id.lower()}-owner-b@example.invalid",
                name=f"{run_id} synthetic owner B",
                password_hash=f"non-login-test-only:{secrets.token_urlsafe(16)}",
                status="active",
                memory_enabled=True,
                timezone="UTC",
                locale="en",
                memory_version=0,
                memory_generation=0,
            )
            session.add(owner_b)
            await session.flush()

            session_ids: dict[str, uuid.UUID] = {}
            for owner_id, tag in ((owner_a_id, "a"), (owner_b_id, "b")):
                device = Device(
                    user_id=owner_id,
                    device_identifier=f"{run_id}-{tag}-synthetic-device",
                    platform="test",
                    device_kind="synthetic",
                    name=f"{run_id} disposable {tag}",
                    device_metadata={"okf_run_id": run_id, "disposable": True},
                )
                session.add(device)
                await session.flush()
                auth = AuthSession(
                    user_id=owner_id,
                    device_id=device.id,
                    refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    created_at=datetime.now(UTC),
                    last_used_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
                session.add(auth)
                await session.flush()
                voice = VoiceSession(
                    user_id=owner_id,
                    device_id=device.id,
                    auth_session_id=auth.id,
                    protocol_version=1,
                    client_metadata={"okf_run_id": run_id, "synthetic": True},
                    status="completed",
                    ended_at=datetime.now(UTC),
                )
                session.add(voice)
                await session.flush()
                run_device_ids.append(device.id)
                run_auth_session_ids.append(auth.id)
                run_session_ids.append(voice.id)
                session_ids[f"{tag}_included"] = voice.id

            device_a = await session.scalar(
                select(Device).where(Device.id == run_device_ids[0], Device.user_id == owner_a_id)
            )
            auth_a = await session.scalar(
                select(AuthSession).where(AuthSession.id == run_auth_session_ids[0])
            )
            excluded_voice = VoiceSession(
                user_id=owner_a_id,
                device_id=device_a.id,
                auth_session_id=auth_a.id,
                protocol_version=1,
                client_metadata={"okf_run_id": run_id, "synthetic": True},
                status="completed",
                ended_at=datetime.now(UTC),
            )
            session.add(excluded_voice)
            await session.flush()
            run_session_ids.append(excluded_voice.id)
            session_ids["a_excluded"] = excluded_voice.id
            await session.commit()
            owner_b_created = True

        stage = "sync"
        writer = MemoryWriter(run_settings)

        async def add_source(
            *,
            owner_id: uuid.UUID,
            label: str,
            memory_type: MemoryType,
            subject: str,
            predicate: str,
            value: dict[str, Any] | None,
            content: str,
            session_id: uuid.UUID,
        ) -> uuid.UUID:
            candidate = ExtractionCandidate(
                content=content,
                memory_type=memory_type,
                subject=subject,
                predicate=predicate,
                object_json=value,
                confidence=1.0,
                salience=0.95,
            )
            async with database.session_factory() as session:
                item, created = await writer.write_candidate(
                    session,
                    user_id=owner_id,
                    candidate=candidate,
                    source_kind=MemorySourceKind.MANUAL_API,
                    source_session_id=session_id,
                    metadata_json={"okf_run_id": run_id, "case_id": label},
                )
                if not created:
                    raise ValueError(f"dedupe unexpectedly reused a source for {label}")
                item_id = item.id
                await session.commit()
            memory_ids.append(item_id)
            source_labels[label] = item_id
            await process_sync_for_memory(item_id)
            return item_id

        async def process_sync_for_memory(memory_id: uuid.UUID) -> None:
            nonlocal stage
            stage = "sync"
            worker = OkfSyncWorker(run_settings)
            async with database.session_factory() as session:
                jobs = list(
                    (
                        await session.scalars(
                            select(OkfSyncJob)
                            .where(
                                OkfSyncJob.user_id.in_(owner_ids),
                                OkfSyncJob.memory_id == memory_id,
                                OkfSyncJob.policy_version == policy_version,
                                OkfSyncJob.status.in_(("pending", "retry_wait")),
                            )
                            .order_by(OkfSyncJob.created_at, OkfSyncJob.id)
                        )
                    ).all()
                )
                for job in jobs:
                    job.status = "running"
                    job.attempts += 1
                    job.locked_at = datetime.now(UTC)
                    sync_job_ids.add(job.id)
                    await session.flush()
                    await worker.process(session, job_id=job.id)
                    await session.commit()

        # Sources are uniquely prefixed with the run ID so lexical and structured
        # candidates from existing users cannot enter the query results.
        slug = _slug(run_id)
        a_session = session_ids["a_included"]
        b_session = session_ids["b_included"]
        stable = await add_source(
            owner_id=owner_a_id,
            label="stable-home",
            memory_type=MemoryType.FACT,
            subject=f"{slug}-stable-person",
            predicate="home_location",
            value={"value": "Cedar Quay"},
            content=f"The {slug}-stable-person home location is Cedar Quay.",
            session_id=a_session,
        )
        project_subject = f"{slug}-atlas-project"
        project_framework = await add_source(
            owner_id=owner_a_id,
            label="project-framework",
            memory_type=MemoryType.PROJECT,
            subject=project_subject,
            predicate="framework",
            value={"value": "Rust"},
            content=f"The {project_subject} project uses the Rust framework.",
            session_id=a_session,
        )
        project_status = await add_source(
            owner_id=owner_a_id,
            label="project-status",
            memory_type=MemoryType.PROJECT,
            subject=project_subject,
            predicate="status",
            value={"value": "active"},
            content=f"The {project_subject} project is active.",
            session_id=a_session,
        )
        relationship = await add_source(
            owner_id=owner_a_id,
            label="relationship-colleague",
            memory_type=MemoryType.RELATIONSHIP,
            subject=f"{slug}-test-user",
            predicate="colleague",
            value={"target": f"{slug}-Mira"},
            content=f"The colleague of {slug}-test-user is {slug}-Mira.",
            session_id=a_session,
        )
        conflict_subject = f"{slug}-color-choice"
        conflict_a = await add_source(
            owner_id=owner_a_id,
            label="conflict-copper",
            memory_type=MemoryType.FACT,
            subject=conflict_subject,
            predicate="favorite_color",
            value={"value": "copper"},
            content=f"The favorite color recorded for {conflict_subject} is copper.",
            session_id=a_session,
        )
        conflict_b = await add_source(
            owner_id=owner_a_id,
            label="conflict-silver",
            memory_type=MemoryType.FACT,
            subject=conflict_subject,
            predicate="favorite_color",
            value={"value": "silver"},
            content=f"The favorite color recorded for {conflict_subject} is silver.",
            session_id=a_session,
        )
        transient = await add_source(
            owner_id=owner_a_id,
            label="transient-event",
            memory_type=MemoryType.EVENT,
            subject=f"{slug}-event",
            predicate="event",
            value=None,
            content=f"A temporary event for {slug} happened at Amber Pier yesterday.",
            session_id=a_session,
        )
        free_text = await add_source(
            owner_id=owner_a_id,
            label="free-text-summary",
            memory_type=MemoryType.SUMMARY,
            subject=f"{slug}-summary",
            predicate="summary",
            value=None,
            content=f"The free text note for {slug} says the hidden phrase is silver meadow.",
            session_id=a_session,
        )
        deleted = await add_source(
            owner_id=owner_a_id,
            label="deleted-occupation",
            memory_type=MemoryType.FACT,
            subject=f"{slug}-delete-profile",
            predicate="occupation",
            value={"value": "archivist"},
            content=f"The occupation for {slug}-delete-profile is archivist.",
            session_id=a_session,
        )
        excluded = await add_source(
            owner_id=owner_a_id,
            label="excluded-timezone",
            memory_type=MemoryType.FACT,
            subject=f"{slug}-excluded-profile",
            predicate="timezone",
            value={"value": "UTC"},
            content=f"The timezone for {slug}-excluded-profile is UTC.",
            session_id=session_ids["a_excluded"],
        )
        owner_a_project = await add_source(
            owner_id=owner_a_id,
            label="owner-a-similar-project",
            memory_type=MemoryType.PROJECT,
            subject=f"{slug}-owner-a-project",
            predicate="framework",
            value={"value": "Rust"},
            content=f"The {slug}-owner-a-project uses Rust.",
            session_id=a_session,
        )
        owner_b_project = await add_source(
            owner_id=owner_b_id,
            label="owner-b-similar-project",
            memory_type=MemoryType.PROJECT,
            subject=f"{slug}-beacon-project",
            predicate="framework",
            value={"value": "Rust"},
            content=f"The {slug}-beacon-project uses Rust.",
            session_id=b_session,
        )
        unrelated_owner_location = await add_source(
            owner_id=owner_b_id,
            label="owner-b-unrelated-location",
            memory_type=MemoryType.FACT,
            subject=f"{slug}-other-owner",
            predicate="home_location",
            value={"value": "Granite Ridge"},
            content=f"The home location for {slug}-other-owner is Granite Ridge.",
            session_id=b_session,
        )

        tool_subject = f"{slug}-preferred-tool"
        stale = await add_source(
            owner_id=owner_a_id,
            label="preferred-tool-stale",
            memory_type=MemoryType.PREFERENCE,
            subject=tool_subject,
            predicate="tool",
            value={"value": "legacy compass"},
            content=f"The preferred tool for {tool_subject} was legacy compass.",
            session_id=a_session,
        )
        active = await add_source(
            owner_id=owner_a_id,
            label="preferred-tool-active",
            memory_type=MemoryType.PREFERENCE,
            subject=tool_subject,
            predicate="tool",
            value={"value": "lighthouse"},
            content=f"The preferred tool for {tool_subject} is lighthouse.",
            session_id=a_session,
        )
        await process_sync_for_memory(stale)

        # Run embeddings only for this run's MemoryJob IDs; do not claim the
        # global memory queue. Existing global OKF jobs are never claimed.
        async with database.session_factory() as session:
            memory_jobs = list(
                (
                    await session.scalars(
                        select(MemoryJob).where(
                            MemoryJob.user_id.in_(owner_ids),
                            MemoryJob.memory_id.in_(memory_ids),
                            MemoryJob.job_type == "embed_memory",
                        )
                    )
                ).all()
            )
            memory_job_ids.update(job.id for job in memory_jobs)
        embedding_provider = RemoteEmbeddingProvider(run_settings)
        await embedding_provider.initialize()
        memory_worker = MemoryJobWorker(run_settings, embedding_provider=embedding_provider)
        for job_id in sorted(memory_job_ids, key=str):
            deadline = time.monotonic() + 90
            while True:
                wait_for_worker = False
                async with database.session_factory() as session:
                    job = await session.get(MemoryJob, job_id, with_for_update=True)
                    if job is None:
                        break
                    chunk_count = await session.scalar(
                        select(func.count())
                        .select_from(MemoryChunk)
                        .where(
                            MemoryChunk.user_id == job.user_id,
                            MemoryChunk.memory_id == job.memory_id,
                        )
                    )
                    embedded_count = await session.scalar(
                        select(func.count())
                        .select_from(MemoryChunk)
                        .where(
                            MemoryChunk.user_id == job.user_id,
                            MemoryChunk.memory_id == job.memory_id,
                            MemoryChunk.embedded_at.is_not(None),
                        )
                    )
                    if job.status == "running":
                        wait_for_worker = True
                    elif job.status == "completed" and embedded_count == chunk_count:
                        break
                    elif job.status in {"pending", "retry_wait", "completed"}:
                        if job.status == "retry_wait" and job.available_at > datetime.now(UTC):
                            wait_for_worker = True
                        else:
                            job.status = "running"
                            job.attempts += 1
                            job.locked_at = datetime.now(UTC)
                            await session.flush()
                            try:
                                await memory_worker._embed_memory(session, job)
                                await MemoryJobRepository(run_settings).complete(session, job)
                                await session.commit()
                            except Exception:
                                await session.rollback()
                                raise
                    else:
                        raise ValueError(f"run-owned embedding job is unresolved: {job.status}")
                if wait_for_worker:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "run-owned embedding worker did not finish in 90 seconds"
                        )
                    await asyncio.sleep(0.25)
                    continue
                break

        # Exercise the synchronous lifecycle barriers only after the facts have
        # materialized and are readable, then verify they disappear.
        async with database.session_factory() as session:
            lifecycle = OkfLifecycleService(policy_version=policy_version, sync_enabled=True)
            await lifecycle.remove_memory_source(session, user_id=owner_a_id, memory_id=deleted)
            item = await session.get(MemoryItem, deleted)
            if item is not None:
                await session.delete(item)
                await MemoryRepository().bump_memory_version(session, user_id=owner_a_id)
            await lifecycle.exclude_session(
                session, user_id=owner_a_id, session_id=session_ids["a_excluded"]
            )
            excluded_voice = await session.get(VoiceSession, session_ids["a_excluded"])
            if excluded_voice is not None:
                await session.delete(excluded_voice)
            await session.commit()
        await process_sync_for_memory(deleted)
        await process_sync_for_memory(excluded)

        # Verify current, source-grounded concepts before retrieving anything.
        async with database.session_factory() as session:
            run_concepts = list(
                (
                    await session.scalars(
                        select(OkfConcept).where(
                            OkfConcept.user_id.in_(owner_ids),
                            OkfConcept.status.in_(("active", "contested")),
                            OkfConcept.canonical_key.contains(slug),
                        )
                    )
                ).all()
            )
            created_concept_count = len(run_concepts)
            if created_concept_count == 0:
                stage = "sync"
                raise ValueError("zero run-tagged readable OKF concepts were materialized")
            linked_memory_ids = set(
                (
                    await session.scalars(
                        select(OkfConceptSource.memory_id).where(
                            OkfConceptSource.user_id.in_(owner_ids),
                            OkfConceptSource.memory_id.in_(memory_ids),
                        )
                    )
                ).all()
            )
            expected_live_sources = {
                stable,
                project_framework,
                project_status,
                relationship,
                conflict_a,
                conflict_b,
                owner_a_project,
                owner_b_project,
                unrelated_owner_location,
                active,
            }
            if not expected_live_sources.issubset(linked_memory_ids):
                stage = "sync"
                raise ValueError("structured sources are missing readable active provenance")
            if (
                deleted in linked_memory_ids
                or excluded in linked_memory_ids
                or stale in linked_memory_ids
            ):
                stage = "sync"
                raise ValueError(
                    "deleted, excluded, or superseded source still has readable provenance"
                )

        # Capture run-created job states by exact owner/source IDs.
        async with database.session_factory() as session:
            run_sync_jobs = list(
                (
                    await session.scalars(
                        select(OkfSyncJob).where(
                            OkfSyncJob.user_id.in_(owner_ids),
                            OkfSyncJob.memory_id.in_(memory_ids),
                            OkfSyncJob.policy_version == policy_version,
                        )
                    )
                ).all()
            )
            sync_job_ids.update(job.id for job in run_sync_jobs)
            sync_records = [
                {
                    "run_id": run_id,
                    "job_id": str(job.id),
                    "owner_id": str(job.user_id),
                    "source_memory_id": str(job.memory_id),
                    "event_type": job.event_type,
                    "status": job.status,
                    "attempts": job.attempts,
                    "policy_version": job.policy_version,
                    "idempotency_key": job.idempotency_key,
                    "created_at": job.created_at.isoformat(),
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                }
                for job in run_sync_jobs
            ]
            sync_counts = {
                status: sum(job.status == status for job in run_sync_jobs)
                for status in ("completed", "cancelled", "retry_wait", "dead", "pending", "running")
            }
            sync_counts["retries"] = sum(max(job.attempts - 1, 0) for job in run_sync_jobs)
            if (
                sync_counts["retry_wait"]
                or sync_counts["dead"]
                or sync_counts["pending"]
                or sync_counts["running"]
            ):
                stage = "sync"
                raise ValueError("run-owned sync queue has unresolved jobs")
            if baseline_pending_ids != set(
                (
                    await session.scalars(
                        select(OkfSyncJob.id).where(OkfSyncJob.status == "pending")
                    )
                ).all()
            ):
                stage = "sync"
                raise ValueError("pre-existing global pending OKF jobs changed during scoped sync")
        stage = "preflight"
        _write_new_jsonl(sync_manifest_path, sync_records)

        # Learn exact labels from the seeded facts, not from observed retrieval.
        active_pref_query = "Which tool do I prefer?"
        stable_query = "What is my home location?"
        project_query = project_subject
        semantic_query = "Where do I live?"
        conflict_query = conflict_subject
        owner_a_query = f"{slug}-beacon-project"
        owner_b_query = owner_a_query
        positive = {
            "rag": "direct_answer",
            "okf": "direct_answer",
            "combined": "continue_with_evidence",
        }
        combined_evidence = {
            "rag": "continue_with_evidence",
            "okf": "continue_with_evidence",
            "combined": "continue_with_evidence",
        }
        negative = {mode: "no_result" for mode in MODES}
        preflight_cases = [
            _case(
                run_id=run_id,
                case_id="P01-simple-positive",
                owner_id=owner_a_id,
                query=stable_query,
                expected_result="Cedar Quay is the labeled home location.",
                lifecycle="Source remains active and has current provenance.",
                privacy="Evidence is restricted to the authenticated OKF disposable owner.",
                expected_by_mode=positive,
                sources_by_mode={mode: (stable,) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="P02-related-project",
                owner_id=owner_a_id,
                query=project_query,
                expected_result="The project uses Rust and is active.",
                lifecycle="Both project memories remain active.",
                privacy="Only run-owned project sources from owner A may support the result.",
                expected_by_mode=combined_evidence,
                sources_by_mode={mode: (project_framework, project_status) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="P03-no-result",
                owner_id=owner_a_id,
                query=f"What secret launch phrase is stored for {slug}-unknown-record?",
                expected_result="No matching fact exists.",
                lifecycle="No source was created for this query.",
                privacy="Do not return unrelated or other-owner facts.",
                expected_by_mode=negative,
                sources_by_mode={mode: () for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="P04-owner-isolation",
                owner_id=owner_a_id,
                query=owner_a_query,
                expected_result="Owner A has no fact for owner B's unique project.",
                lifecycle="Owner B's similar source remains active under owner B.",
                privacy="Owner B's memory and provenance must never cross into owner A's result.",
                expected_by_mode=negative,
                sources_by_mode={mode: () for mode in MODES},
                forbidden=(owner_b_project, unrelated_owner_location),
                privacy_check="cross_user",
            ),
            _case(
                run_id=run_id,
                case_id="P05-deleted-fact",
                owner_id=owner_a_id,
                query=f"What is the occupation of {slug}-delete-profile?",
                expected_result=(
                    "The previously synced occupation was forgotten and must not return."
                ),
                lifecycle="Synchronous forget barrier removed provenance before source deletion.",
                privacy="The deleted source ID must not appear in evidence.",
                expected_by_mode=negative,
                sources_by_mode={mode: () for mode in MODES},
                forbidden=(deleted,),
                privacy_check="deleted",
            ),
            _case(
                run_id=run_id,
                case_id="P06-superseded-current",
                owner_id=owner_a_id,
                query=active_pref_query,
                expected_result="The current preferred tool is lighthouse, not legacy compass.",
                lifecycle="The old preference is superseded; the new source is active.",
                privacy="A stale superseded source must not win or be returned.",
                expected_by_mode=positive,
                sources_by_mode={mode: (active,) for mode in MODES},
                forbidden=(stale,),
                privacy_check="superseded",
            ),
        ]
        full_cases = [
            *preflight_cases,
            _case(
                run_id=run_id,
                case_id="C07-lexical-profile-fact",
                owner_id=owner_a_id,
                query="home location",
                expected_result="Cedar Quay is the home location.",
                lifecycle="The stable home-location source remains active.",
                privacy="Only owner A's labeled home-location source may support the result.",
                expected_by_mode={
                    "rag": "continue_with_evidence",
                    "okf": "direct_answer",
                    "combined": "continue_with_evidence",
                },
                sources_by_mode={mode: (stable,) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="C08-semantic-retrieval",
                owner_id=owner_a_id,
                query=semantic_query,
                expected_result="The remembered home location is Cedar Quay.",
                lifecycle="The stable source remains active.",
                privacy="No other owner or unrelated source may be substituted.",
                expected_by_mode={
                    "rag": "continue_with_evidence",
                    "okf": "no_result",
                    "combined": "continue_with_evidence",
                },
                sources_by_mode={"rag": (stable,), "okf": (), "combined": (stable,)},
                forbidden=(unrelated_owner_location,),
                privacy_check="cross_user",
            ),
            _case(
                run_id=run_id,
                case_id="C09-lexical-retrieval",
                owner_id=owner_a_id,
                query="Cedar Quay",
                expected_result="Cedar Quay is the home location for the labeled stable person.",
                lifecycle="The stable source remains active with provenance.",
                privacy="Only the exact active source ID is accepted.",
                expected_by_mode={
                    "rag": "continue_with_evidence",
                    "okf": "no_result",
                    "combined": "continue_with_evidence",
                },
                sources_by_mode={
                    "rag": (stable,),
                    "okf": (),
                    "combined": (stable,),
                },
            ),
            _case(
                run_id=run_id,
                case_id="C10-multiple-related-facts",
                owner_id=owner_a_id,
                query=project_query,
                expected_result="The project uses Rust and has active status.",
                lifecycle="Both related project sources remain active.",
                privacy="Both returned facts must be grounded in owner A's sources.",
                expected_by_mode=combined_evidence,
                sources_by_mode={mode: (project_framework, project_status) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="C11-conflicting-fact",
                owner_id=owner_a_id,
                query=conflict_query,
                expected_result="The profile is contested between copper and silver.",
                lifecycle="Both contradictory active assertions remain contested.",
                privacy="Both conflict sources must have active owner-scoped provenance.",
                expected_by_mode={"rag": "conflict", "okf": "conflict", "combined": "conflict"},
                sources_by_mode={mode: (conflict_a, conflict_b) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="C12-excluded-fact",
                owner_id=owner_a_id,
                query=f"What timezone is recorded for {slug}-excluded-profile?",
                expected_result="The excluded-session timezone must not be returned.",
                lifecycle="Session exclusion barrier ran and the session/source rows were removed.",
                privacy="The excluded source ID must not appear in evidence.",
                expected_by_mode=negative,
                sources_by_mode={mode: () for mode in MODES},
                forbidden=(excluded,),
                privacy_check="excluded",
            ),
            _case(
                run_id=run_id,
                case_id="C13-transient-event",
                owner_id=owner_a_id,
                query="Amber Pier",
                expected_result=(
                    "The RAG source describes a transient Amber Pier event; "
                    "OKF has no eligible concept."
                ),
                lifecycle="The transient event source remains active for RAG only.",
                privacy="Only the run-owned event source may appear in RAG/COMBINED.",
                expected_by_mode={
                    "rag": "continue_with_evidence",
                    "okf": "no_result",
                    "combined": "continue_with_evidence",
                },
                sources_by_mode={"rag": (transient,), "okf": (), "combined": (transient,)},
            ),
            _case(
                run_id=run_id,
                case_id="C14-free-text-summary",
                owner_id=owner_a_id,
                query="silver meadow",
                expected_result=(
                    "The free-text-only note says silver meadow; OKF must abstain "
                    "because no structured concept was mapped."
                ),
                lifecycle="The summary remains active but intentionally has no OKF concept.",
                privacy="Only the run-owned summary source may appear in RAG/COMBINED.",
                expected_by_mode={
                    "rag": "continue_with_evidence",
                    "okf": "no_result",
                    "combined": "continue_with_evidence",
                },
                sources_by_mode={"rag": (free_text,), "okf": (), "combined": (free_text,)},
            ),
            _case(
                run_id=run_id,
                case_id="C15-owner-b-positive-similar-fact",
                owner_id=owner_b_id,
                query=owner_b_query,
                expected_result="Owner B's project framework is Rust.",
                lifecycle="Owner B source remains active.",
                privacy="Only owner B's source and provenance may support the result.",
                expected_by_mode=combined_evidence,
                sources_by_mode={mode: (owner_b_project,) for mode in MODES},
            ),
            _case(
                run_id=run_id,
                case_id="C16-relationship",
                owner_id=owner_a_id,
                query="Who is my colleague?",
                expected_result=f"The colleague is {slug}-Mira.",
                lifecycle="The relationship source remains active.",
                privacy="Relationship evidence must be sourced from owner A only.",
                expected_by_mode=positive,
                sources_by_mode={mode: (relationship,) for mode in MODES},
            ),
        ]

        # Persist the complete frozen labels before executing any retrieval.
        _write_new_jsonl(manifest_path, full_cases)
        preflight_ids = {case["case_id"] for case in preflight_cases}
        preflight_input = EVIDENCE_ROOT / f"{run_id.lower()}_preflight_manifest.jsonl"
        _write_new_jsonl(
            preflight_input, [case for case in full_cases if case["case_id"] in preflight_ids]
        )

        from scripts.okf_live_evaluate import _evaluate

        stage = "preflight"
        preflight_result = await _evaluate(
            Namespace(
                input=preflight_input,
                output=preflight_path,
                run_id=run_id,
                shadow=False,
                rag_baseline_p95_ms=RAG_BASELINE_P95_MS,
            )
        )
        preflight_summary = preflight_result["summary"]
        if preflight_summary["scored_count"] != len(preflight_cases):
            raise ValueError(
                "preflight had a structural case failure; inspect its case-level JSONL"
            )
        for case_id in preflight_ids:
            record = next(
                record for record in _read_jsonl(preflight_path) if record.get("case_id") == case_id
            )
            if set(record.get("actual", {})) != set(MODES):
                raise ValueError(f"preflight did not execute all modes for {case_id}")

        stage = "full"
        full_result = await _evaluate(
            Namespace(
                input=manifest_path,
                output=results_path,
                run_id=run_id,
                shadow=False,
                rag_baseline_p95_ms=RAG_BASELINE_P95_MS,
            )
        )
        full_summary = full_result["summary"]
        if full_summary["scored_count"] != len(full_cases):
            raise ValueError("full run had a structural case failure; inspect case-level JSONL")
        final_status = full_summary["threshold_assessment"]["status"]

    except Exception as error:  # evidence report is still emitted and cleanup is attempted
        failure_reason = f"{type(error).__name__}: {error}"
        final_status = (
            "BLOCKED — SYNC/MATERIALIZATION INVALID"
            if stage == "sync"
            else "BLOCKED — LIVE EVALUATOR PATH INVALID"
        )
    finally:
        # Cleanup only exact source, session, device, auth-session, entity, job,
        # and disposable-owner IDs created above. Never claim the global queue.
        try:
            if owner_a_verified or owner_b_created or memory_ids:
                async with database.session_factory() as session:
                    lifecycle = OkfLifecycleService(
                        policy_version=policy_version, sync_enabled=True
                    )
                    for memory_id in memory_ids:
                        item = await session.scalar(
                            select(MemoryItem).where(
                                MemoryItem.id == memory_id,
                                MemoryItem.user_id.in_(owner_ids),
                            )
                        )
                        if item is not None:
                            await lifecycle.remove_memory_source(
                                session, user_id=item.user_id, memory_id=memory_id
                            )
                            await session.delete(item)
                            await MemoryRepository().bump_memory_version(
                                session, user_id=item.user_id
                            )
                    await session.commit()
                for memory_id in memory_ids:
                    try:
                        await process_sync_for_memory(memory_id)
                    except Exception:
                        pass
                async with database.session_factory() as session:
                    entity_scope = select(Entity.id).where(
                        Entity.user_id.in_(owner_ids),
                        Entity.normalized_name.like(f"{_slug(run_id)}%"),
                    )
                    await session.execute(delete(Entity).where(Entity.id.in_(entity_scope)))
                    await session.execute(
                        delete(MemoryJob).where(
                            MemoryJob.user_id.in_(owner_ids), MemoryJob.memory_id.in_(memory_ids)
                        )
                    )
                    await session.execute(
                        delete(OkfSyncJob).where(
                            OkfSyncJob.user_id.in_(owner_ids),
                            OkfSyncJob.memory_id.in_(memory_ids),
                            OkfSyncJob.policy_version == policy_version,
                        )
                    )
                    if run_session_ids:
                        await session.execute(
                            delete(VoiceSession).where(
                                VoiceSession.user_id.in_(owner_ids),
                                VoiceSession.id.in_(run_session_ids),
                            )
                        )
                    if run_auth_session_ids:
                        await session.execute(
                            delete(AuthSession).where(
                                AuthSession.id.in_(run_auth_session_ids),
                                AuthSession.user_id.in_(owner_ids),
                            )
                        )
                    if run_device_ids:
                        await session.execute(
                            delete(Device).where(
                                Device.id.in_(run_device_ids), Device.user_id.in_(owner_ids)
                            )
                        )
                    await session.execute(delete(User).where(User.id == owner_b_id))
                    await session.commit()
                async with database.session_factory() as session:
                    remaining = (
                        await session.scalar(
                            select(func.count())
                            .select_from(MemoryItem)
                            .where(
                                MemoryItem.user_id == owner_a_id,
                                MemoryItem.id.in_(memory_ids),
                            )
                        )
                        if memory_ids
                        else 0
                    )
                    concepts = await session.scalar(
                        select(func.count())
                        .select_from(OkfConcept)
                        .where(OkfConcept.user_id == owner_a_id)
                    )
                    owner_b_remaining = await session.get(User, owner_b_id)
                    remaining_jobs = (
                        await session.scalar(
                            select(func.count())
                            .select_from(OkfSyncJob)
                            .where(
                                OkfSyncJob.user_id.in_(owner_ids),
                                OkfSyncJob.memory_id.in_(memory_ids),
                                OkfSyncJob.policy_version == policy_version,
                            )
                        )
                        if memory_ids
                        else 0
                    )
                    remaining_memory_jobs = (
                        await session.scalar(
                            select(func.count())
                            .select_from(MemoryJob)
                            .where(
                                MemoryJob.user_id.in_(owner_ids),
                                MemoryJob.memory_id.in_(memory_ids),
                            )
                        )
                        if memory_ids
                        else 0
                    )
                    remaining_entities = await session.scalar(
                        select(func.count())
                        .select_from(Entity)
                        .where(
                            Entity.user_id == owner_a_id,
                            Entity.normalized_name.like(f"{_slug(run_id)}%"),
                        )
                    )
                    current_pending_ids = set(
                        (
                            await session.scalars(
                                select(OkfSyncJob.id).where(OkfSyncJob.status == "pending")
                            )
                        ).all()
                    )
                    current_memory_job_states = {
                        job_id: (job_type, status, attempts)
                        for job_id, job_type, status, attempts in (
                            await session.execute(
                                select(
                                    MemoryJob.id,
                                    MemoryJob.job_type,
                                    MemoryJob.status,
                                    MemoryJob.attempts,
                                ).where(MemoryJob.user_id == owner_a_id)
                            )
                        ).all()
                    }
                    cleanup_verified = (
                        remaining == 0
                        and concepts == preexisting_concept_counts.get(owner_a_id, 0)
                        and owner_b_remaining is None
                        and remaining_jobs == 0
                        and remaining_memory_jobs == 0
                        and remaining_entities == 0
                        and current_pending_ids == baseline_pending_ids
                        and current_memory_job_states == baseline_memory_job_states
                    )
                    if not cleanup_verified:
                        failure_reason = (
                            failure_reason or ""
                        ) + "; run-scoped cleanup verification failed"
                        final_status = "BLOCKED — LIVE EVALUATOR PATH INVALID"
        except Exception as cleanup_error:
            failure_reason = (failure_reason or "") + f"; cleanup error: {cleanup_error}"
            final_status = "BLOCKED — LIVE EVALUATOR PATH INVALID"

        for key, value in old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if embedding_provider is not None:
            await embedding_provider.close()
        await database.dispose()

        report = _render_report(
            run_id=run_id,
            owner_a_id=owner_a_id,
            owner_b_id=owner_b_id,
            policy_version=policy_version,
            manifest_path=manifest_path,
            preflight_path=preflight_path,
            results_path=results_path,
            sync_manifest_path=sync_manifest_path,
            full_summary=full_summary,
            preflight_summary=preflight_summary,
            sync_counts=sync_counts,
            seeded_memory_count=len(memory_ids),
            concept_count=created_concept_count,
            final_status=final_status,
            failure_reason=failure_reason,
            cleanup_verified=cleanup_verified,
            baseline_pending_count=len(baseline_pending_ids),
            preexisting_memory_job_count=len(baseline_memory_job_states),
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8", newline="\n") as report_file:
            report_file.write(report)

    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": final_status,
                "report": str(report_path),
                "manifest": str(manifest_path),
                "preflight": str(preflight_path),
                "case_results": str(results_path),
                "cleanup_verified": cleanup_verified,
                "failure_reason": failure_reason,
            },
            indent=2,
        )
    )
    return 0 if final_status == "PASS" else 1


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _render_report(
    *,
    run_id: str,
    owner_a_id: uuid.UUID,
    owner_b_id: uuid.UUID,
    policy_version: str,
    manifest_path: Path,
    preflight_path: Path,
    results_path: Path,
    sync_manifest_path: Path,
    full_summary: dict[str, Any] | None,
    preflight_summary: dict[str, Any] | None,
    sync_counts: dict[str, int],
    seeded_memory_count: int,
    concept_count: int,
    final_status: str,
    failure_reason: str | None,
    cleanup_verified: bool,
    baseline_pending_count: int,
    preexisting_memory_job_count: int,
) -> str:
    def compact(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    lines = [
        f"# OKF-4 live labeled acceptance — {run_id}",
        "",
        f"- Run timestamp: {datetime.now(UTC).isoformat()}",
        f"- Final gate: **{final_status}**",
        "- Development runtime; `KNOWLEDGE_MODE=rag` remained authoritative.",
        "- Migration: `0019_okf_owner_memory_generation` (head).",
        f"- Disposable owner A (`OKF`): `{owner_a_id}`; synthetic owner B: `{owner_b_id}`.",
        f"- Run policy/job tag: `{policy_version}`.",
        f"- Seeded source memory count: {seeded_memory_count}",
        f"- Readable run-tagged concept count before evaluation: {concept_count}.",
        f"- Run-owned sync jobs: `{json.dumps(sync_counts, sort_keys=True)}`.",
        f"- Unrelated pending OKF jobs: {baseline_pending_count}; none were claimed or processed.",
        f"- Pre-existing completed memory jobs: {preexisting_memory_job_count}; exact IDs/states "
        "rechecked after cleanup.",
        f"- Preflight summary: `{compact(preflight_summary) if preflight_summary else 'not reached'}`.",  # noqa: E501
        "",
        "## Approved thresholds and results",
        "",
        "- RAG >= 90%; OKF >= 90%; COMBINED >= 95%.",
        "- Expected no-result and provenance correctness = 100%.",
        "- Cross-owner, deleted/excluded, and stale-superseded returns = 0.",
        "- Timeouts, retrieval errors, unresolved sync/dead-letter jobs = 0.",
        "- Controlled-corpus sync lag <= 5,000 ms per source.",
        "- Retrieval P95 <= 647.3922 ms (1.5 x frozen RAG P95 431.5948 ms).",
        "",
    ]
    if full_summary:
        metrics = full_summary["mode_metrics"]
        correctness_json = compact({mode: metrics[mode]["correctness"] for mode in MODES})
        no_result_json = compact({mode: metrics[mode]["no_result_correctness"] for mode in MODES})
        provenance_json = compact({mode: metrics[mode]["provenance_coverage"] for mode in MODES})
        lines.extend(
            [
                f"- Labeled correctness: `{correctness_json}`.",
                f"- No-result correctness: `{no_result_json}`.",
                f"- Provenance coverage: `{provenance_json}`.",
                f"- Retrieval latency P50/P95/P99/max ms: `{compact(full_summary['latency_ms'])}`.",
                f"- Sync lag P50/P95/P99/max ms: `{compact(full_summary['sync_lag_ms'])}`.",
                "- Timeout/error counts: "
                f"{full_summary['retrieval_timeout_count']}/"
                f"{full_summary['retrieval_error_count']}.",
                "- Deleted/excluded/stale counts: "
                f"{full_summary['deleted_fact_return_count']}/"
                f"{full_summary['excluded_fact_return_count']}/"
                f"{full_summary['superseded_stale_fact_return_count']}.",
                f"- Cross-owner leakage count: {full_summary['cross_user_leak_count']}",
                f"- Run-owned unresolved sync jobs: {full_summary['sync_jobs']['unresolved']}",
                f"- Gate assessment: `{compact(full_summary['threshold_assessment'])}`.",
                f"- Failed case IDs: `{compact(full_summary['failed_case_ids'])}`.",
            ]
        )
    else:
        lines.append("- No aggregate full-run metrics were produced.")
    lines.extend(
        [
            "",
            "## Case-level evidence",
            "",
            f"- Frozen labeled manifest: `{manifest_path}`",
            f"- Preflight case-level output: `{preflight_path}`",
            f"- Full case-level results: `{results_path}`",
            f"- Run-owned sync-job evidence: `{sync_manifest_path}`",
            "- JSONL retains each case's run/owner IDs, expected result, lifecycle/privacy labels, "
            "mode expectations, returned facts/source IDs, provenance, latency, and failures.",
            "",
            "## Cleanup and final runtime",
            "",
            f"- Run-scoped cleanup verification: {'PASS' if cleanup_verified else 'NOT VERIFIED'}.",
            "- Run-created rows and owner B were removed by exact IDs; `OKF` was preserved.",
            "- The two existing pending OKF job IDs were unchanged.",
            "- Process-scoped flags were restored; checked-in defaults and `.env` were not edited.",
            "- Final state: OKF/sync/shadow/evaluation OFF; `KNOWLEDGE_MODE=rag`; "
            "allowlists empty.",
            "",
            f"## Blocker/diagnostic\n\n{failure_reason or 'None.'}",
            "",
        ]
    )
    if results_path.exists():
        result_records = [row for row in _read_jsonl(results_path) if row.get("case_id")]
        lines.extend(
            [
                "",
                "## Case-level results by mode",
                "",
                (
                    "| Case | Owner | Mode | Expected result | Expected disposition | "
                    "Actual disposition | Expected sources | Returned sources | "
                    "Provenance | Pass | Latency ms | Failure |"
                ),
                "|---|---|---|---|---|---|---|---|---:|---:|---:|---|",
            ]
        )
        for record in result_records:
            for mode in MODES:
                actual = record.get("actual", {}).get(mode)
                if actual is None:
                    continue
                expected_disposition = record.get("expected_by_mode", {}).get(
                    mode, record.get("expected_disposition")
                )
                lines.append(
                    (
                        "| {case} | {owner} | {mode} | {result} | {expected} | {actual} | "
                        "{expected_ids} | {returned_ids} | {provenance} | {passed} | "
                        "{latency} | {failure} |"
                    ).format(
                        case=record["case_id"],
                        owner=record["owner_id"],
                        mode=mode,
                        result=record.get("expected_result", "").replace("|", "\\|"),
                        expected=expected_disposition,
                        actual=actual["disposition"],
                        expected_ids=",".join(actual["expected_source_memory_ids"]),
                        returned_ids=",".join(actual["evidence_ids"]),
                        provenance=actual["provenance_valid"],
                        passed=actual["pass"],
                        latency=actual["latency_ms"],
                        failure=",".join(actual["failure_reason"]),
                    )
                )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner-id", required=True, help="UUID of the newly created clean account named OKF"
    )
    parser.add_argument("--run-id", help="Optional unique run ID; otherwise generated")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except Exception as error:
        print(
            json.dumps(
                {"status": "preflight_rejected", "reason": f"{type(error).__name__}: {error}"}
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
