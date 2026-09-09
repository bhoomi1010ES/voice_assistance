"""Validate that a production-shaped pgvector HNSW index is planner-usable."""

from __future__ import annotations

# The validator is intentionally runnable from the repository root.
# ruff: noqa: E402
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings

EVIDENCE_DIR = ROOT / "docs" / "evidence" / "phase6"
TABLE = "phase6_hnsw_eval_chunks"
INDEX = "phase6_hnsw_eval_embedding_idx"
CORPUS_SIZE = 5_000


def vector_text(row_id: int, dimension: int) -> str:
    values = ["0"] * dimension
    values[0] = "1"
    values[1] = f"{row_id / CORPUS_SIZE:.8f}"
    return "[" + ",".join(values) + "]"


async def validate() -> dict[str, Any]:
    settings = Settings()
    engine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
    dimension = settings.memory_embedding_dimension
    query_vector = vector_text(123, dimension)
    started = time.perf_counter()
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    f"CREATE TEMP TABLE {TABLE} ("
                    "id integer PRIMARY KEY, "
                    f"embedding vector({dimension}) NOT NULL"
                    ")"
                )
            )
            rows = [
                {"id": row_id, "embedding": vector_text(row_id, dimension)}
                for row_id in range(CORPUS_SIZE)
            ]
            for offset in range(0, CORPUS_SIZE, 250):
                await connection.execute(
                    text(
                        f"INSERT INTO {TABLE} (id, embedding) "
                        "VALUES (:id, CAST(:embedding AS vector) )"
                    ),
                    rows[offset : offset + 250],
                )
            await connection.execute(text(f"ANALYZE {TABLE}"))
            await connection.execute(
                text(f"CREATE INDEX {INDEX} ON {TABLE} USING hnsw (embedding vector_cosine_ops)")
            )
            await connection.execute(text(f"ANALYZE {TABLE}"))
            index_row = await connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :index_name"),
                {"index_name": INDEX},
            )
            index_definition = index_row.scalar_one_or_none()
            plan_rows = await connection.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                    f"SELECT id FROM {TABLE} "
                    "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT 10"
                ),
                {"embedding": query_vector},
            )
            plan = "\n".join(str(row[0]) for row in plan_rows)
            result_rows = await connection.execute(
                text(
                    f"SELECT id FROM {TABLE} "
                    "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT 10"
                ),
                {"embedding": query_vector},
            )
            returned_ids = [int(row[0]) for row in result_rows]
            await connection.commit()

            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            forced_rows = await connection.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                    f"SELECT id FROM {TABLE} "
                    "ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT 10"
                ),
                {"embedding": query_vector},
            )
            forced_plan = "\n".join(str(row[0]) for row in forced_rows)
            await connection.commit()
    finally:
        await engine.dispose()

    return {
        "validation": "isolated_temp_table",
        "corpus_size": CORPUS_SIZE,
        "dimension": dimension,
        "index_name": INDEX,
        "index_definition": index_definition,
        "planner_plan": plan,
        "planner_selected_hnsw": INDEX in plan,
        "secondary_forced_plan": forced_plan,
        "secondary_forced_hnsw": INDEX in forced_plan,
        "returned_relevant_ids": returned_ids,
        "expected_relevant_id": 123,
        "relevant_returned": 123 in returned_ids,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


if __name__ == "__main__":
    outcome = asyncio.run(validate())
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / "phase6_hnsw_plan_validation.json"
    path.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(path), **outcome}, indent=2))
    if not outcome["planner_selected_hnsw"] or not outcome["relevant_returned"]:
        raise SystemExit(1)
