from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.models import (
    OkfConcept,
    OkfConceptAssertion,
    OkfConceptSource,
    OkfConceptVersion,
    OkfSyncJob,
)
from app.models.base import Base


def _foreign_key(table, name: str) -> ForeignKeyConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name == name
    )


def test_okf_metadata_contains_exactly_the_five_additive_tables() -> None:
    expected = {
        "okf_concepts",
        "okf_concept_assertions",
        "okf_concept_versions",
        "okf_concept_sources",
        "okf_sync_jobs",
    }

    assert {name for name in Base.metadata.tables if name.startswith("okf_")} == expected
    assert {
        OkfConcept.__tablename__,
        OkfConceptAssertion.__tablename__,
        OkfConceptVersion.__tablename__,
        OkfConceptSource.__tablename__,
        OkfSyncJob.__tablename__,
    } == expected


def test_okf_identity_and_provenance_are_owner_scoped() -> None:
    concept = OkfConcept.__table__
    assertion = OkfConceptAssertion.__table__
    version = OkfConceptVersion.__table__
    source = OkfConceptSource.__table__

    assert {
        column.name for column in _foreign_key(concept, "fk_okf_concepts_parent_user").columns
    } == {
        "parent_concept_id",
        "user_id",
    }
    assert {
        column.name for column in _foreign_key(assertion, "fk_okf_assertions_concept_user").columns
    } == {
        "concept_id",
        "user_id",
    }
    assert {
        column.name for column in _foreign_key(version, "fk_okf_versions_assertion_user").columns
    } == {
        "assertion_id",
        "user_id",
    }
    assert {
        column.name for column in _foreign_key(source, "fk_okf_sources_version_user").columns
    } == {
        "concept_version_id",
        "user_id",
    }
    assert {
        column.name for column in _foreign_key(source, "fk_okf_sources_memory_user").columns
    } == {
        "memory_id",
        "user_id",
    }
    assert [column.name for column in source.primary_key.columns] == [
        "user_id",
        "concept_version_id",
        "memory_id",
        "evidence_role",
    ]


def test_current_assertion_version_is_a_deferred_database_invariant() -> None:
    constraint = _foreign_key(OkfConceptAssertion.__table__, "fk_okf_assertions_current_version")

    assert [column.name for column in constraint.columns] == [
        "user_id",
        "id",
        "current_version",
    ]
    assert constraint.deferrable is True
    assert constraint.initially == "DEFERRED"
    assert any(
        isinstance(item, UniqueConstraint)
        and [column.name for column in item.columns] == ["user_id", "assertion_id", "version"]
        for item in OkfConceptVersion.__table__.constraints
    )


def test_sync_job_memory_id_is_correlation_only_and_constrained() -> None:
    table = OkfSyncJob.__table__
    memory_foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and "memory_id" in {column.name for column in constraint.columns}
    ]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert memory_foreign_keys == []
    assert "ck_okf_jobs_memory_scope" in check_names
    assert "ck_okf_jobs_event_type" in check_names
    assert "ck_okf_jobs_status" in check_names
    assert "ck_okf_jobs_memory_generation" in check_names
    assert table.c.memory_generation.nullable is False
