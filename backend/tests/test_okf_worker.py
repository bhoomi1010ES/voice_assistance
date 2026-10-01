from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.okf.jobs import OkfSyncWorker


def test_worker_retries_with_bounded_backoff_then_dead_letters() -> None:
    worker = OkfSyncWorker(SimpleNamespace(okf_job_max_attempts=2))
    job = SimpleNamespace(
        attempts=1,
        status="running",
        locked_at=datetime.now(UTC),
        last_error_code=None,
        updated_at=None,
        available_at=None,
    )

    worker.fail(job, "worker_timeout")

    assert job.status == "retry_wait"
    assert job.last_error_code == "worker_timeout"
    assert job.locked_at is None
    assert timedelta(seconds=2) <= job.available_at - job.updated_at <= timedelta(seconds=3)

    job.attempts = 2
    worker.fail(job, "worker_timeout")

    assert job.status == "dead"
    assert job.last_error_code == "worker_timeout"
    assert job.locked_at is None


def test_worker_truncates_error_codes_without_exposing_exception_text() -> None:
    worker = OkfSyncWorker(SimpleNamespace(okf_job_max_attempts=1))
    job = SimpleNamespace(
        attempts=1,
        status="running",
        locked_at=datetime.now(UTC),
        last_error_code=None,
        updated_at=None,
        available_at=None,
    )

    worker.fail(job, "x" * 256)

    assert job.status == "dead"
    assert len(job.last_error_code) == 128
