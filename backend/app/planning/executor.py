"""Validated execution of planning actions through the tool executor.

PM-3 guarantees:
1. Every write is routed through server-owned registered tools and ToolExecutor.
2. Authorization is bound per action via PlanningAuthorization grants.
3. Mutations commit sequentially in transaction groups before any success event or speech.
4. Coupled operations (e.g. task + linked reminder) commit or roll back together.
5. Partial success across independent groups is preserved with exact per-action receipts.
6. Replays reuse stable server-generated tool-call IDs and the idempotency store.
7. Consequential proposals cannot execute automatically; they are queued for confirmation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import SystemClock
from app.core.config import Settings
from app.llm.errors import LLMToolError
from app.llm.tool_loop import (
    PlannerBudget,
    PlanningAuthorization,
    ToolExecutionContext,
    ToolExecutor,
    compute_argument_digest,
)
from app.llm.types import LLMToolCall
from app.models import (
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    Reminder,
    Task,
)
from app.planning.policy import (
    AUTOMATIC_OPERATION_FIELDS,
    CONFIRMATION_OPERATIONS,
    automatic_operation_allowed,
    planning_capabilities,
)
from app.planning.resolution import identity
from app.planning.service import PlanningError, execution_barrier
from app.planning.types import (
    EXTRACTOR_VERSION,
    Decision,
    PlanningReceipt,
    PlanningSnapshot,
)
from app.services.auth import AuthPrincipal
from app.services.device_time import DeviceTimeContext
from app.services.voice_confirmation import IdempotencyKey, PendingConfirmation

LOGGER = logging.getLogger("voice-assistance-backend")


class PlanningExecutor:
    """Executes validated planning decisions under strict policy and transaction isolation."""

    def __init__(
        self,
        settings: Settings,
        tool_executor: ToolExecutor,
        confirmation_service: Any | None = None,
    ) -> None:
        self.settings = settings
        self.tool_executor = tool_executor
        self.confirmation_service = confirmation_service

    async def execute_batch(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        transcript: str,
        snapshot: PlanningSnapshot,
        decisions: tuple[Decision, ...],
        user_timezone: str,
        device_time_context: DeviceTimeContext | None = None,
        cancel_guard: Callable[[], bool] | None = None,
    ) -> PlanningReceipt:
        capabilities = planning_capabilities(self.settings, snapshot.consent)
        if not decisions:
            return PlanningReceipt(
                batch_id=uuid.uuid4(),
                plan_id=snapshot.active_plan_id,
                plan_name=self._active_plan_name(snapshot),
                has_changes=False,
            )

        batch_id = uuid.uuid4()
        source_digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        batch = PlanningBatch(
            id=batch_id,
            user_id=principal.user_id,
            session_id=session_id,
            turn_id=turn_id,
            state_version=snapshot.consent.state_version,
            extractor_version=EXTRACTOR_VERSION,
            policy_version=snapshot.consent.policy_version,
            source_digest=source_digest,
            status="validated",
        )
        db.add(batch)
        await db.flush()

        actions_by_ordinal: dict[int, PlanningAction] = {}
        for ordinal, decision in enumerate(decisions):
            action_id = uuid.uuid4()
            tool_call_id = f"plan_act_{action_id}"
            initial_status = "pending"
            if decision.disposition == "NO_ACTION":
                initial_status = "duplicate" if decision.reason == "duplicate" else "skipped"
            elif decision.disposition in {"CLARIFY", "DENY"}:
                initial_status = "skipped"

            payload = {
                "proposal": decision.proposal.model_dump(mode="json"),
                "scheduled_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
                "timezone": decision.timezone,
                "plan_ordinal": decision.plan_ordinal,
                "resolved_at": snapshot.now_utc.isoformat(),
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            action = PlanningAction(
                id=action_id,
                user_id=principal.user_id,
                batch_id=batch_id,
                plan_id=decision.plan_id,
                ordinal=ordinal,
                action_type=decision.proposal.operation,
                payload_json=payload,
                payload_digest=hashlib.sha256(canonical.encode()).hexdigest(),
                source_spans_json=[
                    span.model_dump()
                    for span in (
                        decision.proposal.source,
                        decision.proposal.temporal,
                        decision.proposal.recurrence_source,
                    )
                    if span
                ],
                disposition=decision.disposition,
                reason=decision.reason,
                target_id=decision.target_id,
                target_revision=decision.target_revision,
                confidence=decision.proposal.confidence,
                tool_call_id=tool_call_id,
                status=initial_status,
            )
            db.add(action)
            actions_by_ordinal[ordinal] = action

        await db.commit()

        saved_actions: list[dict[str, Any]] = []
        duplicate_actions: list[dict[str, Any]] = []
        pending_confirmations: list[dict[str, Any]] = []
        failed_actions: list[dict[str, Any]] = []
        clarifications: list[dict[str, Any]] = []

        for ordinal, decision in enumerate(decisions):
            action = actions_by_ordinal[ordinal]
            if decision.disposition == "NO_ACTION":
                duplicate_actions.append(
                    {
                        "action_id": str(action.id),
                        "operation": decision.proposal.operation,
                        "title": decision.proposal.title,
                        "reason": decision.reason,
                    }
                )
            elif decision.disposition == "CLARIFY":
                clarifications.append(
                    {
                        "action_id": str(action.id),
                        "operation": decision.proposal.operation,
                        "title": decision.proposal.title,
                        "reason": decision.reason,
                    }
                )
            elif decision.disposition == "CONFIRM":
                # Consequential actions CANNOT execute automatically
                consequential_info = await self._queue_consequential_confirmation(
                    db=db,
                    principal=principal,
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    action=action,
                    decision=decision,
                    user_timezone=user_timezone,
                )
                pending_confirmations.append(consequential_info)

        created_plan_id: uuid.UUID | None = snapshot.active_plan_id
        created_plan_name: str | None = self._active_plan_name(snapshot)
        budget = PlannerBudget(max_total_actions=self.settings.plan_max_actions_per_turn)

        if capabilities.automatic_writes:
            auto_items = [
                (ordinal, decision, actions_by_ordinal[ordinal])
                for ordinal, decision in enumerate(decisions)
                if decision.disposition == "AUTO"
            ]
            transaction_groups = self._partition_transaction_groups(auto_items)

            for group in transaction_groups:
                if cancel_guard is not None and cancel_guard():
                    LOGGER.info("Planning execution cancelled by cancel guard")
                    break

                group_success = False
                group_results: list[tuple[PlanningAction, Decision, dict[str, Any]]] = []
                coupled_task_id: uuid.UUID | None = None

                try:
                    async with execution_barrier(
                        db, principal, session_id, snapshot.consent.state_version
                    ):
                        for ordinal, decision, action in group:
                            effective_plan_id = (
                                created_plan_id
                                if (decision.plan_ordinal is not None or decision.plan_id is None)
                                and created_plan_id is not None
                                else (decision.plan_id or created_plan_id)
                            )
                            if decision.proposal.operation == "CREATE_PLAN":
                                effective_plan_id = None

                            # Check target revision for updates
                            if decision.target_id is not None and decision.target_revision is not None:
                                rev_ok = await self._verify_target_revision(
                                    db, principal.user_id, decision
                                )
                                if not rev_ok:
                                    raise LLMToolError("revision_conflict")

                            # Check semantic duplicate in db
                            if decision.proposal.operation in {"CREATE_TASK", "CREATE_REMINDER"}:
                                is_dup = await self._check_runtime_duplicate(
                                    db, principal.user_id, effective_plan_id, decision
                                )
                                if is_dup:
                                    action.status = "duplicate"
                                    action.reason = "duplicate"
                                    await db.flush()
                                    duplicate_actions.append(
                                        {
                                            "action_id": str(action.id),
                                            "operation": decision.proposal.operation,
                                            "title": decision.proposal.title,
                                            "reason": "duplicate",
                                        }
                                    )
                                    continue

                            tool_name, tool_args = self._build_tool_call_args(
                                decision=decision,
                                action=action,
                                effective_plan_id=effective_plan_id,
                                coupled_task_id=coupled_task_id,
                                user_timezone=user_timezone,
                            )
                            tool = self.tool_executor.registry.get(tool_name)
                            if tool is not None:
                                validated_tool_args = tool.arguments_model.model_validate(tool_args)
                                if tool.argument_normalizer is not None:
                                    norm_context = ToolExecutionContext(
                                        user_id=principal.user_id,
                                        session_id=session_id,
                                        turn_id=turn_id,
                                        response_id=response_id,
                                        clock=SystemClock(),
                                        user_timezone=user_timezone,
                                        device_time_context=device_time_context,
                                    )
                                    validated_tool_args = tool.argument_normalizer(
                                        norm_context, validated_tool_args
                                    )
                                arg_digest = compute_argument_digest(validated_tool_args)
                            else:
                                arg_digest = compute_argument_digest(tool_args)

                            grant = PlanningAuthorization(
                                user_id=principal.user_id,
                                session_id=session_id,
                                turn_id=turn_id,
                                action_id=action.id,
                                tool_name=tool_name,
                                argument_digest=arg_digest,
                                target_id=decision.target_id,
                                target_revision=decision.target_revision,
                                mode_state_version=snapshot.consent.state_version,
                                policy_version=snapshot.consent.policy_version,
                                plan_id=effective_plan_id,
                            )

                            exec_context = ToolExecutionContext(
                                user_id=principal.user_id,
                                session_id=session_id,
                                turn_id=turn_id,
                                response_id=response_id,
                                scopes=frozenset(
                                    {
                                        "tasks:write",
                                        "tasks:read",
                                        "reminders:write",
                                        "reminders:read",
                                        "plans:write",
                                        "plans:read",
                                    }
                                ),
                                db=db,
                                clock=SystemClock(),
                                user_timezone=user_timezone,
                                device_time_context=device_time_context,
                                cancellation_check=cancel_guard,
                                planning_grants=(grant,),
                                planner_budget=budget,
                            )

                            call = LLMToolCall(
                                tool_call_id=action.tool_call_id or f"plan_act_{action.id}",
                                name=tool_name,
                                arguments=tool_args,
                            )

                            res = await self.tool_executor.execute(call, context=exec_context)
                            if not res.success:
                                LOGGER.warning("Tool execution failed [%s]: error_code=%s, content=%s", tool_name, res.error_code, res.content)
                                raise LLMToolError(res.error_code or "execution_failed")

                            res_data = json.loads(res.content)
                            if tool_name == "create_plan" and "result" in res_data:
                                p_id_str = res_data["result"].get("plan_id")
                                if p_id_str:
                                    created_plan_id = uuid.UUID(p_id_str)
                                    created_plan_name = res_data["result"].get("name")
                            elif tool_name == "create_task" and "result" in res_data:
                                t_id_str = res_data["result"].get("task_id")
                                if t_id_str:
                                    coupled_task_id = uuid.UUID(t_id_str)

                            action.status = "completed"
                            action.committed_at = datetime.now(UTC)
                            action.result_json = res_data
                            action.plan_id = effective_plan_id
                            group_results.append((action, decision, res_data))

                        # Commit this transaction group before emitting any success
                        await db.commit()
                        group_success = True

                except PlanningError as error:
                    await db.rollback()
                    LOGGER.warning("Planning barrier aborted group: %s", error)
                    await self._mark_group_failed(db, [a for _, _, a in group], str(error))
                    for _, d, a in group:
                        failed_actions.append(
                            {
                                "action_id": str(a.id),
                                "operation": d.proposal.operation,
                                "title": d.proposal.title,
                                "error": str(error),
                            }
                        )
                except Exception as error:  # noqa: BLE001
                    await db.rollback()
                    err_msg = str(error) if str(error) else getattr(error, "code", "execution_failed")
                    LOGGER.warning("Transaction group execution failed: %s", err_msg)
                    await self._mark_group_failed(db, [a for _, _, a in group], err_msg)
                    for _, d, a in group:
                        failed_actions.append(
                            {
                                "action_id": str(a.id),
                                "operation": d.proposal.operation,
                                "title": d.proposal.title,
                                "error": err_msg,
                            }
                        )

                if group_success:
                    for a, d, res_data in group_results:
                        item_id = None
                        if res_data and isinstance(res_data.get("result"), dict):
                            item_id = (
                                res_data["result"].get("task_id")
                                or res_data["result"].get("reminder_id")
                                or res_data["result"].get("context_id")
                                or res_data["result"].get("item_id")
                                or res_data["result"].get("plan_id")
                            )
                        if not item_id and a.target_id:
                            item_id = str(a.target_id)

                        saved_actions.append(
                            {
                                "id": uuid.UUID(str(item_id)) if item_id else a.id,
                                "action_id": str(a.id),
                                "action": d.proposal.operation,
                                "operation": d.proposal.operation,
                                "title": d.proposal.title,
                                "scheduled_at": d.scheduled_at.isoformat() if d.scheduled_at else None,
                                "plan_id": str(a.plan_id) if a.plan_id else None,
                                "result": res_data.get("result") if res_data else None,
                            }
                        )

        # Update batch status
        all_failed = bool(failed_actions and not saved_actions and not duplicate_actions)
        batch.status = "failed" if all_failed else "completed"
        await db.commit()

        has_changes = bool(saved_actions or pending_confirmations)
        text_summary = self._generate_summary(
            saved_actions=saved_actions,
            duplicate_actions=duplicate_actions,
            pending_confirmations=pending_confirmations,
            failed_actions=failed_actions,
            clarifications=clarifications,
            plan_name=created_plan_name,
        )

        return PlanningReceipt(
            batch_id=batch_id,
            plan_id=created_plan_id,
            plan_name=created_plan_name,
            saved_actions=tuple(saved_actions),
            duplicate_actions=tuple(duplicate_actions),
            pending_confirmations=tuple(pending_confirmations),
            failed_actions=tuple(failed_actions),
            clarifications=tuple(clarifications),
            has_changes=has_changes,
            text_summary=text_summary,
        )

    def _partition_transaction_groups(
        self,
        items: list[tuple[int, Decision, PlanningAction]],
    ) -> list[list[tuple[int, Decision, PlanningAction]]]:
        """Group coupled actions together; independent actions each form their own group."""
        groups: list[list[tuple[int, Decision, PlanningAction]]] = []
        i = 0
        while i < len(items):
            ordinal, decision, action = items[i]
            # Check if this is a CREATE_TASK coupled with a following CREATE_REMINDER
            if (
                decision.proposal.operation == "CREATE_TASK"
                and i + 1 < len(items)
                and items[i + 1][1].proposal.operation == "CREATE_REMINDER"
            ):
                next_dec = items[i + 1][1]
                # Couple if titles/mentions match or recurrence/schedule aligns
                if (
                    identity(decision.proposal.title) == identity(next_dec.proposal.title)
                    or (next_dec.proposal.target_mention and next_dec.proposal.target_mention.casefold() in decision.proposal.title.casefold())
                    or (decision.scheduled_at and next_dec.scheduled_at and decision.scheduled_at.date() == next_dec.scheduled_at.date())
                ):
                    groups.append([items[i], items[i + 1]])
                    i += 2
                    continue
            groups.append([items[i]])
            i += 1
        return groups

    async def _verify_target_revision(
        self, db: AsyncSession, user_id: uuid.UUID, decision: Decision
    ) -> bool:
        if decision.target_id is None or decision.target_revision is None:
            return True
        op = decision.proposal.operation
        if "TASK" in op:
            row = await db.scalar(
                select(Task.revision)
                .where(Task.id == decision.target_id, Task.user_id == user_id)
                .with_for_update()
            )
            return row == decision.target_revision
        if "REMINDER" in op:
            row = await db.scalar(
                select(Reminder.revision)
                .where(Reminder.id == decision.target_id, Reminder.user_id == user_id)
                .with_for_update()
            )
            return row == decision.target_revision
        if "PLAN" in op:
            row = await db.scalar(
                select(Plan.revision)
                .where(Plan.id == decision.target_id, Plan.user_id == user_id)
                .with_for_update()
            )
            return row == decision.target_revision
        return True

    async def _check_runtime_duplicate(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        plan_id: uuid.UUID | None,
        decision: Decision,
    ) -> bool:
        if decision.proposal.operation == "CREATE_TASK":
            existing_tasks = (
                await db.scalars(
                    select(Task).where(
                        Task.user_id == user_id,
                        Task.plan_id == plan_id,
                        Task.status == "pending",
                    )
                )
            ).all()
            target_ident = identity(decision.proposal.title)
            for t in existing_tasks:
                if identity(t.title) == target_ident:
                    if decision.scheduled_at is None or t.due_at == decision.scheduled_at:
                        return True
        elif decision.proposal.operation == "CREATE_REMINDER":
            existing_reminders = (
                await db.scalars(
                    select(Reminder).where(
                        Reminder.user_id == user_id,
                        Reminder.plan_id == plan_id,
                        Reminder.status == "scheduled",
                    )
                )
            ).all()
            target_ident = identity(decision.proposal.title)
            for r in existing_reminders:
                if identity(r.title) == target_ident and r.trigger_at == decision.scheduled_at:
                    return True
        return False

    def _build_tool_call_args(
        self,
        decision: Decision,
        action: PlanningAction,
        effective_plan_id: uuid.UUID | None,
        coupled_task_id: uuid.UUID | None,
        user_timezone: str,
    ) -> tuple[str, dict[str, Any]]:
        op = decision.proposal.operation
        if op == "CREATE_PLAN":
            return "create_plan", {
                "name": decision.proposal.title,
                "goal": decision.proposal.content,
                "deadline_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
                "timezone": decision.timezone or user_timezone,
            }
        if op == "UPDATE_PLAN":
            return "update_plan", {
                "plan_id": str(decision.target_id),
                "name": decision.proposal.title,
                "goal": decision.proposal.content,
                "deadline_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
                "timezone": decision.timezone or user_timezone,
            }
        if op == "ADD_PLAN_CONTEXT":
            return "add_plan_context", {
                "plan_id": str(effective_plan_id),
                "content": decision.proposal.content or decision.proposal.title,
                "kind": "note",
            }
        if op == "CREATE_TASK":
            args: dict[str, Any] = {
                "title": decision.proposal.title,
                "notes": decision.proposal.content,
                "due_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
            }
            if effective_plan_id:
                args["plan_id"] = str(effective_plan_id)
            args["planning_action_id"] = str(action.id)
            return "create_task", args
        if op == "UPDATE_TASK":
            args = {
                "task_id": str(decision.target_id),
                "title": decision.proposal.title,
                "notes": decision.proposal.content,
                "due_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
            }
            if decision.target_revision is not None:
                args["expected_revision"] = decision.target_revision
            if effective_plan_id:
                args["plan_id"] = str(effective_plan_id)
            args["planning_action_id"] = str(action.id)
            return "update_task", args
        if op == "COMPLETE_TASK":
            args = {"task_id": str(decision.target_id)}
            if decision.target_revision is not None:
                args["expected_revision"] = decision.target_revision
            return "complete_task", args
        if op == "CREATE_REMINDER":
            args = {
                "title": decision.proposal.title,
                "body": decision.proposal.content,
                "trigger_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
                "recurrence_rule": decision.proposal.recurrence,
            }
            if coupled_task_id:
                args["task_id"] = str(coupled_task_id)
            elif decision.target_id:
                args["task_id"] = str(decision.target_id)
            if effective_plan_id:
                args["plan_id"] = str(effective_plan_id)
            args["planning_action_id"] = str(action.id)
            return "create_reminder", args
        if op == "UPDATE_REMINDER":
            args = {
                "reminder_id": str(decision.target_id),
                "title": decision.proposal.title,
                "body": decision.proposal.content,
                "trigger_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
                "recurrence_rule": decision.proposal.recurrence,
            }
            if decision.target_revision is not None:
                args["expected_revision"] = decision.target_revision
            if effective_plan_id:
                args["plan_id"] = str(effective_plan_id)
            args["planning_action_id"] = str(action.id)
            return "update_reminder", args
        raise LLMToolError(f"unsupported_operation_{op}")

    async def _mark_group_failed(
        self, db: AsyncSession, actions: list[PlanningAction], reason: str
    ) -> None:
        for a in actions:
            a.status = "failed"
            a.reason = reason[:128]
        await db.commit()

    async def _queue_consequential_confirmation(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        action: PlanningAction,
        decision: Decision,
        user_timezone: str,
    ) -> dict[str, Any]:
        """Consequential actions (e.g. deletion, cancellation) queue into voice confirmation."""
        tool_name = {
            "DELETE_TASK": "delete_task",
            "DELETE_REMINDER": "delete_reminder",
            "DELETE_PLAN": "update_plan",
            "ARCHIVE_PLAN": "update_plan",
            "CANCEL": "update_task",
            "UNDO": "update_task",
            "REASSIGN": "update_task",
        }.get(decision.proposal.operation, "confirm_action")

        tool_args: dict[str, Any] = {
            "target_id": str(decision.target_id) if decision.target_id else None,
            "title": decision.proposal.title,
        }
        if decision.target_revision is not None:
            tool_args["expected_revision"] = decision.target_revision

        if decision.proposal.operation in {"ARCHIVE_PLAN", "DELETE_PLAN"} and decision.target_id:
            tool_args = {
                "plan_id": str(decision.target_id),
                "status": "archived" if decision.proposal.operation == "ARCHIVE_PLAN" else "deleted",
            }
            if decision.target_revision is not None:
                tool_args["expected_revision"] = decision.target_revision
        elif decision.proposal.operation == "DELETE_REMINDER" and decision.target_id:
            tool_args = {"reminder_id": str(decision.target_id)}
            if decision.target_revision is not None:
                tool_args["expected_revision"] = decision.target_revision
        elif (
            decision.proposal.operation in {"DELETE_TASK", "CANCEL", "UNDO", "REASSIGN"}
            and decision.target_id
        ):
            tool_args = {"task_id": str(decision.target_id)}
            if decision.proposal.operation in {"DELETE_TASK", "CANCEL"}:
                tool_args["status"] = "cancelled"
            if decision.target_revision is not None:
                tool_args["expected_revision"] = decision.target_revision

        tool_call_id = action.tool_call_id or f"plan_act_{action.id}"
        idempotency_key: IdempotencyKey = (
            principal.user_id,
            turn_id,
            tool_name,
            tool_call_id,
        )

        if self.confirmation_service is not None:
            pending = PendingConfirmation.new(
                authenticated_user_id=principal.user_id,
                device_id=principal.device_id,
                session_id=session_id,
                original_turn_id=turn_id,
                original_response_id=response_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                validated_tool_arguments=tool_args,
                idempotency_key=idempotency_key,
                ttl_seconds=self.settings.voice_confirmation_ttl_seconds,
                user_timezone=user_timezone,
            )
            try:
                if hasattr(self.confirmation_service, "create_or_get"):
                    await self.confirmation_service.create_or_get(pending)
                elif hasattr(self.confirmation_service, "save"):
                    await self.confirmation_service.save(pending)
            except Exception as ex:  # noqa: BLE001
                LOGGER.warning("Could not persist confirmation request to redis: %s", ex)

        action.status = "pending"
        action.disposition = "CONFIRM"
        action.reason = "confirmation_required"
        await db.flush()

        return {
            "action_id": str(action.id),
            "operation": decision.proposal.operation,
            "title": decision.proposal.title,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "target_id": str(decision.target_id) if decision.target_id else None,
        }

    def _active_plan_name(self, snapshot: PlanningSnapshot) -> str | None:
        if not snapshot.active_plan_id:
            return None
        for p in snapshot.plans:
            if p.id == snapshot.active_plan_id:
                return getattr(p, "name", getattr(p, "title", None))
        return None

    def _generate_summary(
        self,
        saved_actions: list[dict[str, Any]],
        duplicate_actions: list[dict[str, Any]],
        pending_confirmations: list[dict[str, Any]],
        failed_actions: list[dict[str, Any]],
        clarifications: list[dict[str, Any]],
        plan_name: str | None,
    ) -> str:
        parts: list[str] = []
        tasks_saved = [a for a in saved_actions if "TASK" in a.get("operation", "")]
        reminders_saved = [a for a in saved_actions if "REMINDER" in a.get("operation", "")]
        plans_saved = [a for a in saved_actions if a.get("operation") == "CREATE_PLAN"]
        context_saved = [a for a in saved_actions if a.get("operation") == "ADD_PLAN_CONTEXT"]

        if plans_saved:
            parts.append(f"Created the '{plans_saved[0]['title']}' plan.")
        elif plan_name:
            # Plan already active
            pass

        if context_saved:
            parts.append(f"Attached {len(context_saved)} context note(s).")

        if tasks_saved:
            if len(tasks_saved) == 1:
                t = tasks_saved[0]
                when = f" for {t['scheduled_at'][:10]}" if t.get("scheduled_at") else ""
                parts.append(f"Added task '{t['title']}'{when}.")
            else:
                parts.append(f"Added {len(tasks_saved)} tasks.")

        if reminders_saved:
            if len(reminders_saved) == 1:
                r = reminders_saved[0]
                parts.append(f"Scheduled reminder '{r['title']}'.")
            else:
                parts.append(f"Scheduled {len(reminders_saved)} reminders.")

        if duplicate_actions:
            parts.append(f"{len(duplicate_actions)} item(s) were already on your list.")

        if pending_confirmations:
            parts.append(
                f"{len(pending_confirmations)} action(s) require confirmation before proceeding."
            )

        if failed_actions:
            parts.append(f"{len(failed_actions)} action(s) could not be completed.")

        if clarifications:
            parts.append("Some details need clarification.")

        return " ".join(parts) if parts else "No changes were made."
