from __future__ import annotations

import json
import math
import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.graph import (
    GraphInvalidTraversal,
    GraphOwnershipError,
    GraphService,
    GraphWriteConflict,
)
from app.graph.repository import normalize_graph_name
from app.models import Entity, EntityRelationship, MemoryItem, User

pytestmark = pytest.mark.integration


@pytest.fixture
async def graph_database():
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 on a disposable PostgreSQL database.")
    settings = Settings()
    engine = create_async_engine(settings.database_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_graph_repository_resolution_ownership_traversal_and_evidence(graph_database):
    settings = Settings(
        graph_max_depth=2,
        graph_max_edges_per_entity=2,
        graph_max_paths=2,
        graph_max_memories=5,
    )
    service = GraphService(settings)
    repository = service.repository
    user_a_id, user_b_id = uuid.uuid4(), uuid.uuid4()
    as_of = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    tomorrow = as_of + timedelta(days=1)
    yesterday = as_of - timedelta(days=1)

    async with graph_database() as session:
        transaction = await session.begin()
        try:
            session.add_all(
                [
                    User(
                        id=user_a_id,
                        email=f"graph-stage3-a-{user_a_id.hex}@example.invalid",
                        password_hash="synthetic-stage3-fixture",
                    ),
                    User(
                        id=user_b_id,
                        email=f"graph-stage3-b-{user_b_id.hex}@example.invalid",
                        password_hash="synthetic-stage3-fixture",
                    ),
                ]
            )
            await session.flush()

            names_a = {
                "rahul": ("Rahul", "person"),
                "alpha": ("Project Alpha", "project"),
                "priya": ("Priya", "person"),
                "api": ("API Migration", "project"),
                "release": ("Release Notes", "project"),
                "root_decoy": ("Discussion Room", "project"),
                "root_other": ("Project Gamma", "project"),
                "neha": ("Neha", "person"),
                "alex_smith": ("Alex Smith", "person"),
                "alex_patel": ("Alex Patel", "person"),
                "alias_claim": ("Alias Claim", "project"),
                "alias_owner": ("Alias Owner", "person"),
                "shared_person": ("Shared Name", "person"),
                "shared_project": ("Shared Name", "project"),
                "cycle_a": ("Cycle A", "project"),
                "cycle_b": ("Cycle B", "project"),
                "current": ("Current", "project"),
                "current_valid": ("Current Valid", "project"),
                "current_future": ("Current Future", "project"),
                "current_expired": ("Current Expired", "project"),
                "current_inactive": ("Current Inactive", "project"),
                "current_superseded": ("Current Superseded", "project"),
            }
            names_b = {
                "rahul": ("Rahul", "person"),
                "beta": ("Project Beta", "project"),
                "anita": ("Anita", "person"),
            }
            entities: dict[tuple[str, str], Entity] = {}
            for owner_key, owner_id, specs in (
                ("a", user_a_id, names_a),
                ("b", user_b_id, names_b),
            ):
                for key, (name, entity_type) in specs.items():
                    entities[(owner_key, key)] = Entity(
                        user_id=owner_id,
                        entity_type=entity_type,
                        canonical_name=name,
                        normalized_name=normalize_graph_name(name, max_length=512),
                    )

            benchmark_root = Entity(
                user_id=user_a_id,
                entity_type="project",
                canonical_name="Benchmark Root",
                normalized_name="benchmark root",
            )
            benchmark_sink = Entity(
                user_id=user_a_id,
                entity_type="project",
                canonical_name="Benchmark Sink",
                normalized_name="benchmark sink",
            )
            benchmark_nodes = [
                Entity(
                    user_id=user_a_id,
                    entity_type="project",
                    canonical_name=f"Benchmark Node {index:02d}",
                    normalized_name=f"benchmark node {index:02d}",
                )
                for index in range(20)
            ]
            entities.update({("a", f"bench_{i}"): row for i, row in enumerate(benchmark_nodes)})
            session.add_all([*entities.values(), benchmark_root, benchmark_sink])
            await session.flush()

            memory_keys = (
                "rahul_alpha",
                "alpha_priya",
                "alpha_api",
                "alpha_release",
                "rahul_discussion",
                "rahul_gamma",
                "neha_priya",
                "cycle_ab",
                "cycle_ba",
                "current_valid",
                "current_future",
                "current_expired",
                "current_inactive",
                "current_superseded",
                "alias_smith",
                "alias_patel",
                "alias_owner",
                "user_b_edge",
                "user_b_alias",
            )
            memories = {
                key: MemoryItem(
                    user_id=user_b_id if key.startswith("user_b") else user_a_id,
                    content=f"Synthetic graph evidence fixture: {key}.",
                    memory_type="relationship",
                    source_kind="manual_api",
                    status="active",
                )
                for key in memory_keys
            }
            for index in range(20):
                memories[f"bench_root_{index}"] = MemoryItem(
                    user_id=user_a_id,
                    content=f"Synthetic benchmark root evidence {index}.",
                    memory_type="relationship",
                    source_kind="manual_api",
                    status="active",
                )
                memories[f"bench_sink_{index}"] = MemoryItem(
                    user_id=user_a_id,
                    content=f"Synthetic benchmark path evidence {index}.",
                    memory_type="relationship",
                    source_kind="manual_api",
                    status="active",
                )
            session.add_all(memories.values())
            await session.flush()

            async def add_edge(
                owner_id: uuid.UUID,
                source: Entity,
                relationship_type: str,
                target: Entity,
                memory_key: str,
                *,
                confidence: float = 0.8,
                valid_from: datetime | None = None,
                valid_to: datetime | None = None,
            ):
                return await service.insert_relationship(
                    session,
                    user_id=owner_id,
                    source_entity_id=source.id,
                    relationship_type=relationship_type,
                    target_entity_id=target.id,
                    source_memory_id=memories[memory_key].id,
                    confidence=confidence,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    extraction_policy_version="stage3-fixture-v1",
                )

            root = entities[("a", "rahul")]
            alpha = entities[("a", "alpha")]
            priya = entities[("a", "priya")]
            api = entities[("a", "api")]
            release = entities[("a", "release")]
            discussion = entities[("a", "root_decoy")]
            gamma = entities[("a", "root_other")]
            await add_edge(user_a_id, root, "WORKS_ON", alpha, "rahul_alpha", confidence=0.99)
            await add_edge(
                user_a_id,
                root,
                "DISCUSSED_WITH",
                discussion,
                "rahul_discussion",
                confidence=0.65,
            )
            await add_edge(user_a_id, root, "WORKS_ON", gamma, "rahul_gamma", confidence=0.55)
            await add_edge(
                user_a_id,
                alpha,
                "TESTED_BY",
                priya,
                "alpha_priya",
                confidence=0.99,
            )
            await add_edge(
                user_a_id,
                alpha,
                "DEPENDS_ON",
                api,
                "alpha_api",
                confidence=0.9,
            )
            await add_edge(
                user_a_id,
                alpha,
                "SHIPS_WITH",
                release,
                "alpha_release",
                confidence=0.8,
            )
            await add_edge(
                user_a_id,
                entities[("a", "neha")],
                "MANAGES",
                priya,
                "neha_priya",
                confidence=0.88,
            )
            await add_edge(
                user_a_id,
                entities[("a", "cycle_a")],
                "CYCLE",
                entities[("a", "cycle_b")],
                "cycle_ab",
            )
            await add_edge(
                user_a_id,
                entities[("a", "cycle_b")],
                "CYCLE",
                entities[("a", "cycle_a")],
                "cycle_ba",
            )

            current = entities[("a", "current")]
            valid_edge = await add_edge(
                user_a_id,
                current,
                "CURRENT_FACT",
                entities[("a", "current_valid")],
                "current_valid",
                valid_from=as_of,
                valid_to=as_of,
            )
            await add_edge(
                user_a_id,
                current,
                "FUTURE_FACT",
                entities[("a", "current_future")],
                "current_future",
                valid_from=tomorrow,
            )
            await add_edge(
                user_a_id,
                current,
                "EXPIRED_FACT",
                entities[("a", "current_expired")],
                "current_expired",
                valid_to=yesterday,
            )
            inactive_edge = await add_edge(
                user_a_id,
                current,
                "INACTIVE_EVIDENCE",
                entities[("a", "current_inactive")],
                "current_inactive",
            )
            superseded_edge = await add_edge(
                user_a_id,
                current,
                "SUPERSEDED_EDGE",
                entities[("a", "current_superseded")],
                "current_superseded",
            )
            await session.execute(
                update(MemoryItem)
                .where(MemoryItem.id == memories["current_inactive"].id)
                .values(status="superseded")
            )
            await session.execute(
                update(EntityRelationship)
                .where(EntityRelationship.id == superseded_edge.edge.relationship_id)
                .values(status="superseded")
            )

            b_rahul = entities[("b", "rahul")]
            beta = entities[("b", "beta")]
            anita = entities[("b", "anita")]
            await add_edge(user_b_id, b_rahul, "WORKS_ON", beta, "user_b_edge", confidence=0.99)
            await add_edge(user_b_id, beta, "TESTED_BY", anita, "user_b_edge", confidence=0.99)

            # Seed only synthetic, already-validated rows for the repository microbenchmark.
            benchmark_edges = []
            for index, node in enumerate(benchmark_nodes):
                benchmark_edges.append(
                    EntityRelationship(
                        user_id=user_a_id,
                        source_entity_id=benchmark_root.id,
                        relationship_type="BENCHMARK_FIRST",
                        target_entity_id=node.id,
                        source_memory_id=memories[f"bench_root_{index}"].id,
                        confidence=1.0 - index / 100,
                        status="active",
                    )
                )
                benchmark_edges.append(
                    EntityRelationship(
                        user_id=user_a_id,
                        source_entity_id=node.id,
                        relationship_type="BENCHMARK_SECOND",
                        target_entity_id=benchmark_sink.id,
                        source_memory_id=memories[f"bench_sink_{index}"].id,
                        confidence=1.0 - index / 100,
                        status="active",
                    )
                )
            session.add_all(benchmark_edges)
            await session.flush()

            aliases = [
                (user_a_id, entities[("a", "alex_smith")], "Alex", "alias_smith", "memory"),
                (user_a_id, entities[("a", "alex_patel")], "Alex", "alias_patel", "memory"),
                (user_a_id, root, "R. Kumar", "alias_owner", "manual"),
                (
                    user_a_id,
                    entities[("a", "alias_owner")],
                    "Alias Claim",
                    None,
                    "manual",
                ),
                (user_b_id, b_rahul, "Secret Alias", "user_b_alias", "memory"),
            ]
            for owner_id, entity, alias, memory_key, source_kind in aliases:
                await service.insert_alias(
                    session,
                    user_id=owner_id,
                    entity_id=entity.id,
                    alias=alias,
                    source_kind=source_kind,
                    source_memory_id=memories[memory_key].id if memory_key else None,
                )

            # Canonical exact names win over another entity's same-text alias.
            await service.insert_alias(
                session,
                user_id=user_a_id,
                entity_id=entities[("a", "alex_smith")].id,
                alias="Alias Claim",
                source_kind="manual",
            )
            for entity_key, entity_type in (
                ("shared_person", "person"),
                ("shared_project", "project"),
            ):
                assert entities[("a", entity_key)].entity_type == entity_type

            resolution = await service.resolve_entity_exact(
                session, user_id=user_a_id, name="  ＲＡＨＵＬ  "
            )
            assert resolution.status == "resolved"
            assert resolution.matched_by == "canonical"
            assert resolution.entity is not None and resolution.entity.id == root.id
            assert len(resolution.candidates) == 1
            assert (
                await service.resolve_entity_exact(session, user_id=user_a_id, name="Shared Name")
            ).status == "ambiguous"
            project_resolution = await service.resolve_entity_exact(
                session,
                user_id=user_a_id,
                name="Shared Name",
                entity_type="project",
            )
            assert project_resolution.status == "resolved"
            alias_resolution = await service.resolve_entity_exact(
                session, user_id=user_a_id, name="r.   kumar"
            )
            assert alias_resolution.status == "resolved"
            assert alias_resolution.matched_by == "alias"
            ambiguous_alias = await service.resolve_entity_exact(
                session, user_id=user_a_id, name="Alex"
            )
            assert ambiguous_alias.status == "ambiguous"
            assert [candidate.canonical_name for candidate in ambiguous_alias.candidates] == [
                "Alex Patel",
                "Alex Smith",
            ]
            canonical_priority = await service.resolve_entity_exact(
                session, user_id=user_a_id, name="Alias Claim"
            )
            assert canonical_priority.status == "resolved"
            assert canonical_priority.entity is not None
            assert canonical_priority.entity.id == entities[("a", "alias_claim")].id
            assert (
                await service.resolve_entity_exact(session, user_id=user_a_id, name="Secret Alias")
            ).status == "not_found"
            assert (
                await service.resolve_entity_exact(
                    session, user_id=user_a_id, name="No Such Entity"
                )
            ).status == "not_found"

            alias_before, alias_created = await repository.insert_alias(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                alias="R. Kumar",
                source_kind="manual",
                source_memory_id=memories["alias_owner"].id,
            )
            alias_replay, alias_replay_created = await repository.insert_alias(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                alias="r.   kumar",
                source_kind="manual",
                source_memory_id=memories["alias_owner"].id,
            )
            assert alias_created is False  # the facade inserted this alias above
            assert alias_replay_created is False
            assert alias_before.id == alias_replay.id

            # Relationship replay returns the same evidence row without duplicates.
            replay_created = await service.insert_relationship(
                session,
                user_id=user_a_id,
                source_entity_id=root.id,
                relationship_type="WORKS_ON",
                target_entity_id=alpha.id,
                source_memory_id=memories["rahul_alpha"].id,
                confidence=0.99,
                extraction_policy_version="stage3-fixture-v1",
            )
            assert replay_created.created is False
            assert (
                replay_created.edge.relationship_id
                == (
                    await service.neighbors(
                        session,
                        user_id=user_a_id,
                        entity_id=root.id,
                        direction="outgoing",
                        as_of=as_of,
                        relationship_type="WORKS_ON",
                    )
                )
                .paths[0]
                .edges[0]
                .relationship_id
            )
            with pytest.raises(GraphWriteConflict):
                await service.insert_relationship(
                    session,
                    user_id=user_a_id,
                    source_entity_id=root.id,
                    relationship_type="WORKS_ON",
                    target_entity_id=alpha.id,
                    source_memory_id=memories["rahul_alpha"].id,
                    confidence=0.2,
                    extraction_policy_version="stage3-fixture-v1",
                )
            with pytest.raises(ValueError, match="cannot connect an entity to itself"):
                await service.insert_relationship(
                    session,
                    user_id=user_a_id,
                    source_entity_id=root.id,
                    relationship_type="SELF",
                    target_entity_id=root.id,
                    source_memory_id=memories["rahul_alpha"].id,
                    confidence=1.0,
                )

            with pytest.raises(GraphOwnershipError):
                await service.insert_relationship(
                    session,
                    user_id=user_a_id,
                    source_entity_id=root.id,
                    relationship_type="CROSS_USER",
                    target_entity_id=beta.id,
                    source_memory_id=memories["rahul_alpha"].id,
                    confidence=1.0,
                )
            with pytest.raises(GraphOwnershipError):
                await service.insert_relationship(
                    session,
                    user_id=user_a_id,
                    source_entity_id=root.id,
                    relationship_type="CROSS_MEMORY",
                    target_entity_id=alpha.id,
                    source_memory_id=memories["user_b_edge"].id,
                    confidence=1.0,
                )
            with pytest.raises(GraphOwnershipError):
                await service.insert_alias(
                    session,
                    user_id=user_a_id,
                    entity_id=root.id,
                    alias="Cross-user provenance",
                    source_kind="memory",
                    source_memory_id=memories["user_b_alias"].id,
                )
            assert (
                await repository.get_owned_entity(session, user_id=user_a_id, entity_id=b_rahul.id)
                is None
            )

            outgoing = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                direction="outgoing",
                as_of=as_of,
            )
            assert len(outgoing.paths) <= settings.graph_max_edges_per_entity
            assert outgoing.truncated is True
            assert outgoing.paths[0].edges[0].target_name == "Project Alpha"
            repeated_outgoing = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                direction="outgoing",
                as_of=as_of,
            )
            assert [path.edges[0].relationship_id for path in outgoing.paths] == [
                path.edges[0].relationship_id for path in repeated_outgoing.paths
            ]

            incoming = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=alpha.id,
                direction="incoming",
                as_of=as_of,
            )
            assert incoming.paths[0].entity_ids == (alpha.id, root.id)
            assert incoming.paths[0].edges[0].source_entity_id == root.id
            assert incoming.paths[0].edges[0].target_entity_id == alpha.id

            both = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=alpha.id,
                direction="both",
                as_of=as_of,
            )
            assert any(path.entity_ids == (alpha.id, root.id) for path in both.paths)
            assert any(path.entity_ids == (alpha.id, priya.id) for path in both.paths)

            filtered = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                direction="outgoing",
                relationship_type="WORKS_ON",
                as_of=as_of,
            )
            assert filtered.paths
            assert all(path.edges[0].relationship_type == "WORKS_ON" for path in filtered.paths)
            assert all(path.edges[0].target_name != "Discussion Room" for path in filtered.paths)

            two_hop = await service.paths(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                direction="outgoing",
                depth=2,
                as_of=as_of,
            )
            assert len(two_hop.paths) == 2
            assert two_hop.truncated is True
            assert two_hop.paths[0].entity_ids == (root.id, alpha.id, priya.id)
            assert two_hop.paths[0].edges[0].relationship_type == "WORKS_ON"
            assert two_hop.paths[0].edges[1].relationship_type == "TESTED_BY"
            one_path_only = await service.paths(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                direction="outgoing",
                depth=2,
                as_of=as_of,
                max_paths=1,
            )
            assert len(one_path_only.paths) == 1
            assert one_path_only.truncated is True
            with pytest.raises(GraphInvalidTraversal, match="Stage 3 limit"):
                await service.paths(
                    session,
                    user_id=user_a_id,
                    entity_id=root.id,
                    depth=3,
                    as_of=as_of,
                )

            cycle_result = await service.paths(
                session,
                user_id=user_a_id,
                entity_id=entities[("a", "cycle_a")].id,
                direction="outgoing",
                depth=2,
                as_of=as_of,
            )
            assert cycle_result.paths == ()

            current_result = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=current.id,
                direction="outgoing",
                as_of=as_of,
            )
            assert [path.edges[0].relationship_id for path in current_result.paths] == [
                valid_edge.edge.relationship_id
            ]
            later_result = await service.neighbors(
                session,
                user_id=user_a_id,
                entity_id=current.id,
                direction="outgoing",
                as_of=tomorrow,
            )
            assert any(path.edges[0].target_name == "Current Future" for path in later_result.paths)
            assert all(
                path.edges[0].relationship_id != inactive_edge.edge.relationship_id
                for path in later_result.paths
            )

            user_a_two_hop = await service.paths(
                session,
                user_id=user_a_id,
                entity_id=root.id,
                depth=2,
                as_of=as_of,
            )
            assert all(
                path.edges[0].target_name != "Project Beta"
                and all(edge.target_name != "Anita" for edge in path.edges)
                and all(edge.user_id == user_a_id for edge in path.edges)
                for path in user_a_two_hop.paths
            )
            user_b_two_hop = await service.paths(
                session,
                user_id=user_b_id,
                entity_id=b_rahul.id,
                depth=2,
                as_of=as_of,
            )
            assert user_b_two_hop.paths[0].entity_ids == (b_rahul.id, beta.id, anita.id)
            assert all(edge.user_id == user_b_id for edge in user_b_two_hop.paths[0].edges)
            user_b_incoming = await service.neighbors(
                session,
                user_id=user_b_id,
                entity_id=beta.id,
                direction="incoming",
                as_of=as_of,
            )
            assert user_b_incoming.paths[0].entity_ids == (beta.id, b_rahul.id)

            evidence = await service.source_memories(
                session,
                user_id=user_a_id,
                source_memory_ids=(
                    memories["rahul_alpha"].id,
                    memories["current_inactive"].id,
                    memories["user_b_edge"].id,
                ),
            )
            assert [memory.id for memory in evidence] == [memories["rahul_alpha"].id]
            path_evidence = await service.source_memories_for_paths(
                session,
                user_id=user_a_id,
                paths=two_hop.paths,
            )
            assert {memory.id for memory in path_evidence} == {
                edge.source_memory_id for path in two_hop.paths for edge in path.edges
            }
            assert all(memory.user_id == user_a_id for memory in path_evidence)
            assert (
                len(
                    await service.source_memories(
                        session,
                        user_id=user_a_id,
                        source_memory_ids=tuple(memory.id for memory in memories.values()),
                        limit=2,
                    )
                )
                <= 2
            )

            # The second-hop SQL has the same bounded shape with intermediate entities as roots.
            explain_statements = (
                (
                    "one_hop",
                    repository._build_neighbor_statement(
                        user_id=user_a_id,
                        entity_ids=(root.id,),
                        direction="outgoing",
                        as_of=as_of,
                        relationship_type=None,
                        max_edges_per_entity=2,
                    ),
                ),
                (
                    "two_hop_second_leg",
                    repository._build_neighbor_statement(
                        user_id=user_a_id,
                        entity_ids=(alpha.id,),
                        direction="outgoing",
                        as_of=as_of,
                        relationship_type=None,
                        max_edges_per_entity=2,
                    ),
                ),
            )
            for label, statement in explain_statements:
                compiled = statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
                plan_rows = await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}"))
                print(f"GRAPH_EXPLAIN_{label.upper()}_BEGIN")
                print("\n".join(row[0] for row in plan_rows))
                print(f"GRAPH_EXPLAIN_{label.upper()}_END")

            async def benchmark(entity_id: uuid.UUID, depth: int) -> dict[str, float]:
                samples_ms = []
                for _ in range(30):
                    started_ns = time.perf_counter_ns()
                    await service.traverse(
                        session,
                        user_id=user_a_id,
                        entity_id=entity_id,
                        direction="outgoing",
                        depth=depth,
                        as_of=as_of,
                        relationship_type="BENCHMARK_FIRST" if depth == 1 else None,
                        max_paths=2,
                    )
                    samples_ms.append((time.perf_counter_ns() - started_ns) / 1_000_000)
                ordered = sorted(samples_ms)
                return {
                    "min_ms": ordered[0],
                    "mean_ms": statistics.fmean(ordered),
                    "p50_ms": statistics.median(ordered),
                    "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
                    "max_ms": ordered[-1],
                }

            benchmark_results = {
                "classification": "repository microbenchmark; not production voice latency",
                "iterations": 30,
                "one_hop": await benchmark(benchmark_root.id, 1),
                "two_hop": await benchmark(benchmark_root.id, 2),
            }
            print("GRAPH_REPO_BENCHMARK=" + json.dumps(benchmark_results, sort_keys=True))
        finally:
            await transaction.rollback()
