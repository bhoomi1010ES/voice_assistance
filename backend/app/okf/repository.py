from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MemoryItem,
    OkfConcept,
    OkfConceptAssertion,
    OkfConceptSource,
    OkfConceptVersion,
    User,
    VoiceSession,
)

from .types import OkfConceptProposal


class OkfRepositoryError(RuntimeError):
    """Base class for safe OKF persistence failures."""


class OkfSourceUnavailable(OkfRepositoryError):
    """The source is absent, inactive, excluded, or not owned by the caller."""


class OkfRepository:
    """Persistence methods require and apply an owner ID on every operation."""

    async def lock_eligible_source(
        self, session: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID
    ) -> MemoryItem:
        memory = await session.scalar(
            select(MemoryItem)
            .where(MemoryItem.user_id == user_id, MemoryItem.id == memory_id)
            .with_for_update()
        )
        owner = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if memory is None or owner is None or not owner.memory_enabled or memory.status != "active":
            raise OkfSourceUnavailable("OKF source memory is unavailable")
        if memory.source_session_id is None:
            raise OkfSourceUnavailable("OKF source must belong to an included session")
        source_session = await session.scalar(
            select(VoiceSession).where(
                VoiceSession.id == memory.source_session_id,
                VoiceSession.user_id == user_id,
            )
        )
        if (
            source_session is None
            or (source_session.client_metadata or {}).get("memory_excluded") is True
        ):
            raise OkfSourceUnavailable("OKF source session is unavailable")
        return memory

    async def lock_source_for_lifecycle(
        self, session: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID
    ) -> MemoryItem:
        """Lock an owned source even when it is being disabled or excluded."""

        memory = await session.scalar(
            select(MemoryItem)
            .where(MemoryItem.user_id == user_id, MemoryItem.id == memory_id)
            .with_for_update()
        )
        owner_exists = await session.scalar(select(User.id).where(User.id == user_id))
        if memory is None or owner_exists is None:
            raise OkfSourceUnavailable("OKF source memory is unavailable")
        return memory

    async def get_or_create_concept(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        proposal: OkfConceptProposal,
    ) -> tuple[OkfConcept, bool]:
        concept_id = uuid.uuid4()
        statement = (
            pg_insert(OkfConcept)
            .values(
                id=concept_id,
                user_id=user_id,
                concept_type=proposal.concept_type,
                canonical_key=proposal.canonical_key,
                title=proposal.title,
                status="active",
                policy_version=proposal.policy_version,
            )
            .on_conflict_do_nothing(constraint="uq_okf_concepts_user_key")
            .returning(OkfConcept.id)
        )
        inserted_id = await session.scalar(statement)
        concept = await session.scalar(
            select(OkfConcept)
            .where(
                OkfConcept.user_id == user_id, OkfConcept.canonical_key == proposal.canonical_key
            )
            .with_for_update()
        )
        if concept is None:
            raise OkfRepositoryError("OKF concept could not be read after upsert")
        if concept.concept_type != proposal.concept_type:
            raise OkfRepositoryError("canonical key is already bound to a different concept type")
        return concept, inserted_id is not None

    async def link_project_child(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        parent_key: str,
        child_key: str,
    ) -> None:
        parent = await session.scalar(
            select(OkfConcept)
            .where(
                OkfConcept.user_id == user_id,
                OkfConcept.canonical_key == parent_key,
                OkfConcept.concept_type == "project",
            )
            .with_for_update()
        )
        child = await session.scalar(
            select(OkfConcept)
            .where(OkfConcept.user_id == user_id, OkfConcept.canonical_key == child_key)
            .with_for_update()
        )
        if parent is None or child is None or not child_key.startswith(f"{parent_key}/"):
            raise OkfRepositoryError("OKF project parent/child identity is inconsistent")
        child.parent_concept_id = parent.id
        child.updated_at = datetime.now(UTC)
        await session.flush()

    async def active_assertions(
        self, session: AsyncSession, *, user_id: uuid.UUID, concept_id: uuid.UUID
    ) -> list[OkfConceptAssertion]:
        return list(
            (
                await session.scalars(
                    select(OkfConceptAssertion)
                    .where(
                        OkfConceptAssertion.user_id == user_id,
                        OkfConceptAssertion.concept_id == concept_id,
                        OkfConceptAssertion.status == "active",
                    )
                    .order_by(OkfConceptAssertion.id)
                    .with_for_update()
                )
            ).all()
        )

    async def current_version(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        assertion: OkfConceptAssertion,
    ) -> OkfConceptVersion:
        version = await session.scalar(
            select(OkfConceptVersion).where(
                OkfConceptVersion.user_id == user_id,
                OkfConceptVersion.assertion_id == assertion.id,
                OkfConceptVersion.version == assertion.current_version,
            )
        )
        if version is None:
            raise OkfRepositoryError("current OKF assertion version is missing")
        return version

    async def source_was_applied(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """Return prior assertion IDs for this owner/source/concept tuple."""

        rows = (
            await session.execute(
                select(OkfConceptVersion.assertion_id)
                .join(
                    OkfConceptSource,
                    OkfConceptSource.concept_version_id == OkfConceptVersion.id,
                )
                .where(
                    OkfConceptSource.user_id == user_id,
                    OkfConceptSource.memory_id == memory_id,
                    OkfConceptVersion.user_id == user_id,
                    OkfConceptVersion.concept_id == concept_id,
                )
                .distinct()
            )
        ).all()
        return tuple(row[0] for row in rows)

    async def append_version(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        assertion: OkfConceptAssertion,
        value_json: dict[str, Any],
        display_text: str,
        status: str,
        confidence: float,
        valid_from: datetime | None,
        valid_to: datetime | None,
        change_kind: str,
        policy_version: str,
    ) -> OkfConceptVersion:
        next_version = assertion.current_version + 1
        version = OkfConceptVersion(
            id=uuid.uuid4(),
            user_id=user_id,
            concept_id=concept_id,
            assertion_id=assertion.id,
            version=next_version,
            value_json=value_json,
            display_text=display_text,
            status=status,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            change_kind=change_kind,
            policy_version=policy_version,
        )
        session.add(version)
        assertion.current_version = next_version
        assertion.status = status
        assertion.confidence = confidence
        assertion.valid_from = valid_from
        assertion.valid_to = valid_to
        assertion.updated_at = datetime.now(UTC)
        await session.flush()
        return version

    async def create_assertion(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        proposal: OkfConceptProposal,
    ) -> tuple[OkfConceptAssertion, OkfConceptVersion]:
        assertion = OkfConceptAssertion(
            id=uuid.uuid4(),
            user_id=user_id,
            concept_id=concept_id,
            value_json=proposal.value_json,
            display_text=proposal.display_text,
            status="active",
            current_version=1,
            confidence=proposal.confidence,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
            policy_version=proposal.policy_version,
        )
        session.add(assertion)
        await session.flush()
        version = OkfConceptVersion(
            id=uuid.uuid4(),
            user_id=user_id,
            concept_id=concept_id,
            assertion_id=assertion.id,
            version=1,
            value_json=proposal.value_json,
            display_text=proposal.display_text,
            status="active",
            confidence=proposal.confidence,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
            change_kind="create",
            policy_version=proposal.policy_version,
        )
        session.add(version)
        await session.flush()
        return assertion, version

    async def add_source(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        version_id: uuid.UUID,
        memory_id: uuid.UUID,
        evidence_role: str = "supports",
    ) -> bool:
        statement = (
            pg_insert(OkfConceptSource)
            .values(
                user_id=user_id,
                concept_version_id=version_id,
                memory_id=memory_id,
                evidence_role=evidence_role,
            )
            .on_conflict_do_nothing(constraint="pk_okf_concept_sources")
            .returning(OkfConceptSource.memory_id)
        )
        inserted = await session.scalar(statement)
        return inserted is not None

    async def derive_concept_status(
        self, session: AsyncSession, *, user_id: uuid.UUID, concept: OkfConcept
    ) -> str:
        active_count = int(
            await session.scalar(
                select(func.count(OkfConceptAssertion.id)).where(
                    OkfConceptAssertion.user_id == user_id,
                    OkfConceptAssertion.concept_id == concept.id,
                    OkfConceptAssertion.status == "active",
                )
            )
            or 0
        )
        status = "retired" if active_count == 0 else "contested" if active_count > 1 else "active"
        concept.status = status
        concept.updated_at = datetime.now(UTC)
        await session.flush()
        return status

    async def get_assertion(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        assertion_id: uuid.UUID,
    ) -> OkfConceptAssertion | None:
        return await session.scalar(
            select(OkfConceptAssertion)
            .where(
                OkfConceptAssertion.id == assertion_id,
                OkfConceptAssertion.user_id == user_id,
            )
            .with_for_update()
        )

    async def get_concept(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        concept_id: uuid.UUID,
        lock: bool = False,
    ) -> OkfConcept | None:
        statement = select(OkfConcept).where(
            OkfConcept.id == concept_id,
            OkfConcept.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        return await session.scalar(statement)

    async def bump_memory_generation(self, session: AsyncSession, *, user_id: uuid.UUID) -> int:
        generation = await session.scalar(
            update(User)
            .where(User.id == user_id)
            .values(memory_generation=User.memory_generation + 1)
            .returning(User.memory_generation)
        )
        if generation is None:
            raise OkfRepositoryError("OKF owner is unavailable")
        return int(generation)
