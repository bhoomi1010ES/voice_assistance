from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.memory.repository import MemoryRepository
from app.memory.types import MemoryType, normalize_memory_text, stable_dedupe_key
from app.models import MemoryChunk, MemoryItem, MemoryJob, Message
from app.models.base import Base
from app.services.auth import AuthPrincipal


def test_phase6_defaults_are_safe_and_do_not_require_model_services() -> None:
    settings = Settings(_env_file=None)

    assert settings.memory_retrieval_mode == "off"
    assert settings.memory_write_enabled is False
    assert settings.embedding_api_url is None
    assert settings.rerank_api_url is None


def test_phase6_inject_requires_shared_key_and_exact_model_endpoints() -> None:
    with pytest.raises(
        ValueError,
        match="STT_API_KEY must be configured when Phase 6 memory retrieval or writes are enabled",
    ):
        Settings(
            _env_file=None,
            memory_retrieval_mode="inject",
            embedding_api_url="https://memory.example.test/v1/embeddings",
            rerank_api_url="https://memory.example.test/v1/rerank",
        )

    with pytest.raises(ValueError, match="EMBEDDING_API_URL must not contain query"):
        Settings(
            _env_file=None,
            app_env="production",
            stt_api_key="shared-test-key",
            memory_retrieval_mode="inject",
            embedding_api_url="https://memory.example.test/v1/embeddings?model=bge",
            rerank_api_url="https://memory.example.test/v1/rerank",
        )


def test_phase6_write_only_requires_embedding_but_not_reranker() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        stt_api_key="shared-test-key",
        memory_write_enabled=True,
        embedding_api_url="http://127.0.0.1:8000/v1/embeddings",
    )

    assert settings.memory_write_enabled is True


def test_memory_normalization_and_dedupe_key_are_deterministic() -> None:
    assert normalize_memory_text("  Café\u00a0  Mumbai ") == "café mumbai"
    first = stable_dedupe_key(
        memory_type=MemoryType.PREFERENCE,
        subject="User",
        predicate="prefers",
        object_json={"drink": "Tea"},
        content="The user prefers tea.",
    )
    second = stable_dedupe_key(
        memory_type="preference",
        subject=" user ",
        predicate="PREFERS",
        object_json={"drink": "Tea"},
        content=" the USER prefers tea. ",
    )
    assert first == second


def test_phase6_metadata_tables_and_ownership_constraints_exist() -> None:
    phase6_tables = {
        "messages",
        "memory_items",
        "memory_chunks",
        "entities",
        "memory_entities",
        "memory_jobs",
    }
    assert phase6_tables.issubset(Base.metadata.tables)
    assert "user_id" in MemoryItem.__table__.c
    assert "embedding" in MemoryChunk.__table__.c
    assert "source_message_id" in MemoryJob.__table__.c
    assert any(
        constraint.name == "uq_messages_turn_role_sequence"
        for constraint in Message.__table__.constraints
    )
    supersession = next(
        constraint
        for constraint in MemoryItem.__table__.foreign_key_constraints
        if constraint.name == "fk_memory_items_supersedes_user"
    )
    assert [column.name for column in supersession.columns] == ["supersedes_id", "user_id"]
    assert [column.name for column in supersession.elements[0].column.table.primary_key] == ["id"]


@pytest.mark.asyncio
async def test_final_message_repository_replays_without_overwrite() -> None:
    user_id = uuid.uuid4()
    principal = AuthPrincipal(user_id=user_id, session_id=uuid.uuid4(), device_id=uuid.uuid4())
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar = AsyncMock(side_effect=[SimpleNamespace(), None])

    message, created = await MemoryRepository().persist_final_message(
        session,
        principal,
        turn_id=uuid.uuid4(),
        role="user",
        content="Remember that I prefer tea.",
    )

    assert created is True
    assert message.user_id == user_id
    assert message.is_final is True
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_message_repository_rejects_unowned_turn() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    principal = AuthPrincipal(user_id=uuid.uuid4(), session_id=uuid.uuid4(), device_id=uuid.uuid4())

    with pytest.raises(LookupError, match="not owned"):
        await MemoryRepository().persist_final_message(
            session,
            principal,
            turn_id=uuid.uuid4(),
            role="user",
            content="private content",
        )


def test_phase6_job_and_memory_enums_match_schema_contract() -> None:
    assert MemoryJob.__table__.c.job_type.type.length == 32
    assert MemoryItem.__table__.c.memory_type.type.length == 32
