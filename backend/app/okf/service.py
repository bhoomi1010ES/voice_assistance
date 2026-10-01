from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

from .policy import map_memory_to_proposals, proposals_match, validate_proposal
from .repository import OkfRepository, OkfRepositoryError, OkfSourceUnavailable
from .types import OkfConceptProposal, OkfSyncOutcome


class OkfKnowledgeService:
    """Transactional concept writer; the caller owns commit/rollback."""

    def __init__(
        self, repository: OkfRepository | None = None, settings: Settings | None = None
    ) -> None:
        self.repository = repository or OkfRepository()
        self.settings = settings or get_settings()

    async def sync_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> tuple[OkfSyncOutcome, ...]:
        """Map one source memory deterministically and persist all its concepts."""

        if not self.settings.okf_enabled:
            raise RuntimeError("OKF_ENABLED must be true before concept writes are allowed")
        memory = await self.repository.lock_eligible_source(
            session, user_id=user_id, memory_id=memory_id
        )
        proposals = map_memory_to_proposals(memory, policy_version=self.settings.okf_policy_version)
        outcomes_list = []
        for proposal in proposals:
            outcomes_list.append(
                await self.sync_source_memory(session, user_id=user_id, proposal=proposal)
            )
        outcomes = tuple(outcomes_list)
        if len(proposals) == 2 and proposals[0].concept_type == "project":
            await self.repository.link_project_child(
                session,
                user_id=user_id,
                parent_key=proposals[0].canonical_key,
                child_key=proposals[1].canonical_key,
            )
        return outcomes

    async def sync_source_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        proposal: OkfConceptProposal,
    ) -> OkfSyncOutcome:
        """Apply one structured proposal after reloading its owned active source."""

        if not self.settings.okf_enabled:
            raise RuntimeError("OKF_ENABLED must be true before concept writes are allowed")
        proposal = validate_proposal(proposal)
        if proposal.policy_version != self.settings.okf_policy_version:
            raise ValueError("okf_policy_version_mismatch")
        memory = await self.repository.lock_eligible_source(
            session, user_id=user_id, memory_id=proposal.source_memory_id
        )
        deterministic = map_memory_to_proposals(
            memory, policy_version=self.settings.okf_policy_version
        )
        if not any(proposals_match(proposal, item) for item in deterministic):
            raise ValueError("okf_proposal_not_deterministically_grounded")

        concept, concept_created = await self.repository.get_or_create_concept(
            session, user_id=user_id, proposal=proposal
        )
        active = await self.repository.active_assertions(
            session, user_id=user_id, concept_id=concept.id
        )
        prior_assertion_ids = await self.repository.source_was_applied(
            session,
            user_id=user_id,
            concept_id=concept.id,
            memory_id=proposal.source_memory_id,
        )
        if prior_assertion_ids:
            return OkfSyncOutcome(
                status="unchanged",
                reason_code="source_already_applied",
                concept_ids=(concept.id,),
                assertion_ids=prior_assertion_ids,
                source_memory_id=proposal.source_memory_id,
                policy_version=proposal.policy_version,
            )

        if proposal.supersedes_assertion_id is not None:
            old_assertion = await self.repository.get_assertion(
                session,
                user_id=user_id,
                assertion_id=proposal.supersedes_assertion_id,
            )
            if (
                old_assertion is None
                or old_assertion.concept_id != concept.id
                or old_assertion.status != "active"
            ):
                raise OkfRepositoryError("superseded assertion is not active under this owner")
            old_version = await self.repository.append_version(
                session,
                user_id=user_id,
                concept_id=concept.id,
                assertion=old_assertion,
                value_json=old_assertion.value_json,
                display_text=old_assertion.display_text,
                status="superseded",
                confidence=old_assertion.confidence,
                valid_from=old_assertion.valid_from,
                valid_to=old_assertion.valid_to,
                change_kind="supersede",
                policy_version=self.settings.okf_policy_version,
            )
            await self.repository.add_source(
                session,
                user_id=user_id,
                version_id=old_version.id,
                memory_id=proposal.source_memory_id,
                evidence_role="supersedes",
            )
            assertion, version = await self.repository.create_assertion(
                session,
                user_id=user_id,
                concept_id=concept.id,
                proposal=proposal,
            )
            await self.repository.add_source(
                session,
                user_id=user_id,
                version_id=version.id,
                memory_id=proposal.source_memory_id,
            )
            await self.repository.derive_concept_status(session, user_id=user_id, concept=concept)
            return OkfSyncOutcome(
                status="updated",
                reason_code="explicit_supersession",
                concept_ids=(concept.id,),
                assertion_ids=(old_assertion.id, assertion.id),
                source_memory_id=proposal.source_memory_id,
                policy_version=proposal.policy_version,
            )

        for existing in active:
            if existing.value_json == proposal.value_json:
                version = await self.repository.current_version(
                    session, user_id=user_id, assertion=existing
                )
                await self.repository.add_source(
                    session,
                    user_id=user_id,
                    version_id=version.id,
                    memory_id=proposal.source_memory_id,
                )
                await self.repository.derive_concept_status(
                    session, user_id=user_id, concept=concept
                )
                return OkfSyncOutcome(
                    status="unchanged",
                    reason_code="equivalent_value_provenance_attached",
                    concept_ids=(concept.id,),
                    assertion_ids=(existing.id,),
                    source_memory_id=proposal.source_memory_id,
                    policy_version=proposal.policy_version,
                )

        assertion, version = await self.repository.create_assertion(
            session,
            user_id=user_id,
            concept_id=concept.id,
            proposal=proposal,
        )
        await self.repository.add_source(
            session,
            user_id=user_id,
            version_id=version.id,
            memory_id=proposal.source_memory_id,
        )
        status = await self.repository.derive_concept_status(
            session, user_id=user_id, concept=concept
        )
        is_conflict = status == "contested"
        return OkfSyncOutcome(
            status="contested" if is_conflict else "created" if concept_created else "updated",
            reason_code="incompatible_current_assertions" if is_conflict else None,
            concept_ids=(concept.id,),
            assertion_ids=(assertion.id,),
            source_memory_id=proposal.source_memory_id,
            policy_version=proposal.policy_version,
        )

    async def retire_assertion(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        assertion_id: uuid.UUID,
        source_memory_id: uuid.UUID,
    ) -> OkfSyncOutcome:
        """Append a retirement version supported by an active owned memory."""

        source_memory = await self.repository.lock_eligible_source(
            session, user_id=user_id, memory_id=source_memory_id
        )
        assertion = await self.repository.get_assertion(
            session, user_id=user_id, assertion_id=assertion_id
        )
        if assertion is None or assertion.status != "active":
            raise OkfRepositoryError("active assertion is unavailable")
        concept = await self.repository.get_concept(
            session, user_id=user_id, concept_id=assertion.concept_id, lock=True
        )
        if concept is None:
            raise OkfRepositoryError("assertion concept is unavailable")
        current_version = await self.repository.current_version(
            session, user_id=user_id, assertion=assertion
        )
        source_proposals = map_memory_to_proposals(
            source_memory, policy_version=self.settings.okf_policy_version
        )
        if not any(
            item.canonical_key == concept.canonical_key
            and item.value_json == current_version.value_json
            for item in source_proposals
        ):
            raise ValueError("okf_retirement_source_does_not_support_assertion")
        version = await self.repository.append_version(
            session,
            user_id=user_id,
            concept_id=concept.id,
            assertion=assertion,
            value_json=assertion.value_json,
            display_text=assertion.display_text,
            status="retired",
            confidence=assertion.confidence,
            valid_from=assertion.valid_from,
            valid_to=assertion.valid_to,
            change_kind="retire",
            policy_version=self.settings.okf_policy_version,
        )
        await self.repository.add_source(
            session,
            user_id=user_id,
            version_id=version.id,
            memory_id=source_memory_id,
            evidence_role="supports",
        )
        status = await self.repository.derive_concept_status(
            session, user_id=user_id, concept=concept
        )
        return OkfSyncOutcome(
            status="retired",
            reason_code=f"concept_{status}",
            concept_ids=(concept.id,),
            assertion_ids=(assertion.id,),
            source_memory_id=source_memory_id,
            policy_version=self.settings.okf_policy_version,
        )


__all__ = ["OkfKnowledgeService", "OkfSourceUnavailable"]
