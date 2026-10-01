from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest

from app.okf.jobs import OkfSyncWorker
from app.okf.scoped_sync import ScopedSyncValidationError, process_only


def test_empty_scope_is_audited_and_never_means_global_work(caplog) -> None:
    owner_id = uuid.uuid4()
    worker = OkfSyncWorker(SimpleNamespace(okf_job_lease_seconds=30, okf_job_max_attempts=3))

    async def run() -> None:
        with pytest.raises(ScopedSyncValidationError) as error:
            await process_only(
                None,  # Empty scopes must reject before opening a database session.
                worker,
                evaluation_run_id="OKF6-20260930T203218Z-4bcb55",
                owner_id=owner_id,
                approved_disposable_owner_ids={owner_id},
                job_ids=(),
            )
        assert error.value.code == "empty_job_scope"
        assert error.value.audit.requested_job_ids == ()
        assert error.value.audit.processed_job_ids == ()
        assert error.value.audit.rejected_job_ids == ()

    with caplog.at_level(logging.INFO, logger="voice-assistance-backend"):
        asyncio.run(run())

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "okf.scoped_sync.execution"
    ]
    assert len(records) == 1
    assert records[0].execution_status == "rejected"
    assert records[0].failure_code == "empty_job_scope"
    assert records[0].requested_job_count == 0
    assert records[0].processed_job_ids == []


def test_invalid_run_identifier_fails_before_database_or_worker_access() -> None:
    owner_id = uuid.uuid4()
    job_id = uuid.uuid4()
    worker = OkfSyncWorker(SimpleNamespace(okf_job_lease_seconds=30, okf_job_max_attempts=3))

    async def run() -> None:
        with pytest.raises(ScopedSyncValidationError) as error:
            await process_only(
                None,
                worker,
                evaluation_run_id="not-a-run-id",
                owner_id=owner_id,
                approved_disposable_owner_ids={owner_id},
                job_ids=(job_id,),
            )
        assert error.value.code == "invalid_evaluation_run_id"
        assert error.value.audit.rejected_job_ids == (job_id,)
        assert not error.value.audit.processed_job_ids

    asyncio.run(run())
