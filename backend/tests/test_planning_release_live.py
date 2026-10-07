"""Opt-in live model plus disposable PostgreSQL flow; no Android/push evidence."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.llm.service import LLMService
from app.llm.tool_loop import ToolExecutor, create_default_tool_registry
from app.models import Plan, PlanningSession, Task
from app.planning.executor import PlanningExecutor
from app.planning.observer import PlanningObserver
from app.planning.runtime import execute_turn
from app.planning.service import change_state
from app.services.tool_idempotency import PostgresToolIdempotencyStore

from . import test_planning_release_postgres as release_tests
from .test_planning_release_postgres import new_turn

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_PLAN_LIVE_ACCEPTANCE") != "1",
        reason="Set RUN_PLAN_LIVE_ACCEPTANCE=1 to send synthetic fixture text to the model.",
    ),
]


@pytest.fixture
async def release_database():
    async for data in release_tests.release_database.__wrapped__():
        yield data


async def test_live_provider_persists_multi_action_plan_correction_duplicate_and_disable(
    release_database,
):
    factory, principal, voices, _ = release_database
    settings = Settings(
        plan_mode_enabled=True,
        plan_extraction_mode="on",
        plan_auto_actions_enabled=True,
        plan_test_user_ids=(principal.user_id,),
        plan_extraction_timeout_ms=3000,
        memory_retrieval_mode="off",
        memory_write_enabled=False,
        okf_shadow_reads=False,
    )
    llm = LLMService(settings)
    report = {
        "kind": "live_model_disposable_postgres_final_transcripts",
        "android_voice_evidence": False,
        "push_delivery_evidence": False,
        "deadline_ms": settings.plan_extraction_timeout_ms,
        "steps": [],
        "passed": False,
    }
    try:
        await llm.initialize()
        report["provider"] = llm.provider_info.provider
        report["model"] = llm.provider_info.configured_model
        observer = PlanningObserver(settings, llm, factory)
        async with factory() as db:
            executor = PlanningExecutor(
                settings,
                ToolExecutor(
                    create_default_tool_registry(),
                    idempotency_store=PostgresToolIdempotencyStore(db),
                ),
            )
        now = datetime(2026, 10, 6, 19, tzinfo=UTC)

        async def run(text):
            turn = await new_turn(factory, principal, voices[0], text)
            started = time.perf_counter()
            receipt = await execute_turn(
                observer,
                executor,
                principal=principal,
                session_id=voices[0],
                turn_id=turn,
                response_id=uuid.uuid4(),
                transcript=text,
                now_utc=now,
                timezone="America/Los_Angeles",
            )
            report["steps"].append(
                {
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "saved": len(receipt.saved_actions) if receipt else 0,
                    "duplicates": len(receipt.duplicate_actions) if receipt else 0,
                    "failures": len(receipt.failed_actions) if receipt else 0,
                }
            )
            return receipt

        first = await run(
            "We're starting PM6Review. I need the report Friday. I need the presentation Thursday."
        )
        assert first is not None and not first.failed_actions
        assert {item["operation"] for item in first.saved_actions} == {"CREATE_PLAN", "CREATE_TASK"}
        assert len(first.saved_actions) == 3
        async with factory() as db:
            tasks = list(await db.scalars(select(Task)))
            assert len(tasks) == 2 and all(t.plan_id == first.plan_id for t in tasks)
            assert (await db.get(PlanningSession, voices[0])).active_plan_id == first.plan_id
            original_report = next(t for t in tasks if "report" in t.title.casefold())
            report_id = original_report.id
        correction = await run("Actually, make the report Monday.")
        assert correction and len(correction.saved_actions) == 1 and not correction.failed_actions
        async with factory() as db:
            task = await db.get(Task, report_id)
            assert task.revision == 2
            assert task.due_at == datetime(2026, 10, 13, 6, 59, tzinfo=UTC)
        duplicate = await run("I need the report Monday.")
        assert duplicate and len(duplicate.duplicate_actions) == 1 and not duplicate.saved_actions
        negative = await run("I don't need to build a database.")
        assert negative is None or not negative.saved_actions
        async with factory() as db:
            state = await db.get(PlanningSession, voices[0])
            await change_state(
                db,
                principal,
                voices[0],
                expected_version=state.state_version,
                enabled=True,
                mode="normal",
            )
            await db.commit()
        normal = await run("I need another report Friday.")
        assert normal is None
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Task)) == 2
            assert await db.scalar(select(func.count()).select_from(Plan)) == 1
            assert (await db.get(PlanningSession, voices[0])).mode == "normal"
        report["passed"] = True
    finally:
        await llm.close()
        root = Path(__file__).resolve().parents[2]
        (root / "docs" / "pm6_live_persistence.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
