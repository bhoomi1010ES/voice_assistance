"""Optional real PostgreSQL lock test, isolated from application records.

Set PLAN_MODE_TEST_DATABASE_URL to a PostgreSQL asyncpg test connection with
CREATE SCHEMA permission. This creates/drops only a randomly named test schema.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import (
    AuthSession,
    ConversationTurn,
    Device,
    MemoryItem,
    Message,
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    PlanningSession,
    Reminder,
    Task,
    User,
    VoiceSession,
)
from app.planning.observer import PlanningObserver
from app.planning.service import PlanningError, change_state, execution_barrier
from app.services.auth import AuthPrincipal


@pytest.mark.integration
async def test_postgres_disable_commit_barrier():
    url = os.environ.get("PLAN_MODE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PLAN_MODE_TEST_DATABASE_URL for the isolated PostgreSQL barrier test.")
    schema = "pm1_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    metadata = MetaData()
    for model in [
        User,
        Device,
        AuthSession,
        VoiceSession,
        ConversationTurn,
        Message,
        MemoryItem,
        Plan,
        PlanContextItem,
        PlanningSession,
        PlanningBatch,
        PlanningAction,
        Task,
        Reminder,
    ]:
        model.__table__.to_metadata(metadata)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    running = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with factory() as db:
            user = User(email="barrier@test.local", password_hash="test")
            db.add(user)
            await db.flush()
            device = Device(user_id=user.id, device_identifier="barrier", platform="android")
            db.add(device)
            await db.flush()
            auth = AuthSession(
                user_id=user.id,
                device_id=device.id,
                refresh_token_hash="test",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            db.add(auth)
            await db.flush()
            voice = VoiceSession(
                user_id=user.id,
                device_id=device.id,
                auth_session_id=auth.id,
                protocol_version=1,
                status="active",
            )
            db.add(voice)
            await db.flush()
            principal = AuthPrincipal(user_id=user.id, device_id=device.id, session_id=auth.id)
            session_id = voice.id
            await change_state(
                db, principal, session_id, expected_version=1, enabled=True, mode="plan"
            )
            await db.commit()
            turn_id = uuid.uuid4()
            transcript = "I need the report Friday."
            db.add(
                ConversationTurn(
                    id=turn_id,
                    session_id=session_id,
                    user_id=principal.user_id,
                    turn_number=1,
                    status="committed",
                )
            )
            await db.flush()
            db.add(
                Message(turn_id=turn_id, user_id=principal.user_id, role="user", content=transcript)
            )
            await db.commit()

        class SyntheticLLM:
            enabled = True
            requests = 0

            async def stream(self, request):
                self.requests += 1
                assert request.allowed_tools == ()
                yield SimpleNamespace(
                    event_type="text_delta",
                    delta=json.dumps(
                        {
                            "actions": [
                                {
                                    "op": "task",
                                    "clause": 0,
                                    "title": "Report",
                                    "time": "Friday",
                                    "actor": "user",
                                    "plan": None,
                                }
                            ],
                            "overflow": False,
                        }
                    ),
                )
                yield SimpleNamespace(
                    event_type="response_completed", finish_reason="stop", text=None
                )

        llm = SyntheticLLM()
        observer = PlanningObserver(
            Settings(
                _env_file=None,
                plan_mode_enabled=True,
                plan_extraction_mode="shadow",
                plan_test_user_ids=(principal.user_id,),
                plan_extraction_timeout_ms=3000,
            ),
            llm,
            factory,
        )
        kwargs = dict(
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            transcript=transcript,
            now_utc=datetime(2026, 10, 6, 19, tzinfo=UTC),
            timezone="America/Los_Angeles",
        )
        await observer.observe(**kwargs)
        await observer.observe(**kwargs)
        assert llm.requests == 1
        async with factory() as db:
            for model in (
                Task,
                Reminder,
                Plan,
                PlanContextItem,
                PlanningBatch,
                PlanningAction,
                MemoryItem,
            ):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
            turn = await db.get(ConversationTurn, turn_id)
            marker = turn.metadata_json["planning_observation"]
            assert marker["status"] == "validated" and marker["outcomes"] == {"AUTO": 1}
            assert "Report" not in json.dumps(marker) and "Friday" not in json.dumps(marker)

        entered, release, acknowledged = asyncio.Event(), asyncio.Event(), asyncio.Event()
        order = []

        async def execution():
            async with factory() as db:
                async with execution_barrier(db, principal, session_id, 2):
                    entered.set()
                    await release.wait()
                    db.add(Task(user_id=principal.user_id, title="Synthetic barrier mutation"))
                    await db.commit()
                    order.append("mutation_commit")

        async def disable():
            async with factory() as db:
                await change_state(
                    db, principal, session_id, expected_version=2, enabled=True, mode="normal"
                )
                await db.commit()
                order.append("disable_acknowledged")
                acknowledged.set()

        running.append(asyncio.create_task(execution()))
        await asyncio.wait_for(entered.wait(), 5)
        running.append(asyncio.create_task(disable()))
        await asyncio.sleep(0.1)
        assert not acknowledged.is_set()
        release.set()
        await asyncio.wait_for(asyncio.gather(*running), 5)
        assert order == ["mutation_commit", "disable_acknowledged"]
        async with factory() as db:
            with pytest.raises(PlanningError, match="planning_consent_revoked"):
                async with execution_barrier(db, principal, session_id, 2):
                    pytest.fail("stale consent passed the PostgreSQL barrier")
    finally:
        for task in running:
            if not task.done():
                task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()
