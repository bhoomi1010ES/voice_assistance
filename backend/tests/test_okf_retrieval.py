from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.okf.retrieval import OkfRetrievalService, _canonical_term_pattern
from app.okf.types import KnowledgeDisposition, KnowledgeRequest


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        okf_enabled=True,
        okf_sync_enabled=True,
        **overrides,
    )


def _request(**overrides) -> KnowledgeRequest:
    values = {
        "user_id": uuid.uuid4(),
        "query": "Which response style do I prefer?",
        "now": datetime.now(UTC),
    }
    values.update(overrides)
    return KnowledgeRequest(**values)


def test_canonical_key_terms_match_complete_segments_not_run_id_substrings() -> None:
    pattern = _canonical_term_pattern("b")
    owner_a_key = "projects/okf4-2026-e38fab-owner-a-project/framework"
    owner_b_key = "projects/okf4-2026-e38fab-owner-b-project/framework"

    assert re.search(pattern, owner_a_key) is None
    assert re.search(pattern, owner_b_key) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_request", "expected"),
    [
        (_request(memory_enabled=False), KnowledgeDisposition.UNAVAILABLE),
        (_request(memory_excluded=True), KnowledgeDisposition.UNAVAILABLE),
        (_request(cancellation_check=True), KnowledgeDisposition.CANCELLED),
    ],
)
async def test_retrieval_short_circuits_disabled_excluded_and_cancelled_requests(
    input_request: KnowledgeRequest, expected: KnowledgeDisposition
) -> None:
    session = AsyncMock()

    result = await OkfRetrievalService(_settings()).retrieve(session, input_request)

    assert result.status == expected
    assert result.evidence == ()
    session.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieval_fails_closed_with_content_free_database_reason() -> None:
    class Savepoint:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return False

    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=Savepoint())
    session.scalar.side_effect = SQLAlchemyError("private database details")

    result = await OkfRetrievalService(_settings()).retrieve(session, _request())

    assert result.status == KnowledgeDisposition.UNAVAILABLE
    assert result.degraded_reason == "database_unavailable"
    assert result.evidence == ()
    assert "private database details" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_master_off_never_queries_okf_tables() -> None:
    session = AsyncMock()
    settings = Settings(_env_file=None, okf_enabled=False, okf_sync_enabled=False)

    result = await OkfRetrievalService(settings).retrieve(session, _request())

    assert result.status == KnowledgeDisposition.UNAVAILABLE
    assert result.degraded_reason == "okf_disabled"
    session.scalar.assert_not_awaited()
