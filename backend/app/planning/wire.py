"""Compact model output; server-owned clause coordinates preserve exact evidence."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from app.planning.types import Envelope, Proposal, Span

OPERATIONS = {
    "plan": "CREATE_PLAN",
    "context": "ADD_PLAN_CONTEXT",
    "task": "CREATE_TASK",
    "reminder": "CREATE_REMINDER",
    "update_plan": "UPDATE_PLAN",
    "update_task": "UPDATE_TASK",
    "complete": "COMPLETE_TASK",
    "update_reminder": "UPDATE_REMINDER",
    "delete_plan": "DELETE_PLAN",
    "archive": "ARCHIVE_PLAN",
    "delete_task": "DELETE_TASK",
    "delete_reminder": "DELETE_REMINDER",
    "cancel": "CANCEL",
    "undo": "UNDO",
    "reassign": "REASSIGN",
}


class Details(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: str | None = Field(default=None, min_length=1, max_length=255)
    target: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=2048)


def output_schema(max_actions: int) -> dict:
    def nullable(limit):
        return {"anyOf": [{"type": "string", "minLength": 1, "maxLength": limit}, {"type": "null"}]}

    properties = {
        "op": {"type": "string", "enum": list(OPERATIONS)},
        "clause": {"type": "integer", "minimum": 0, "maximum": 31},
        "title": {"type": "string", "minLength": 1, "maxLength": 255},
        "time": nullable(512),
        "actor": {"type": "string", "enum": ["user", "team", "third_party"]},
        "plan": nullable(255),
    }
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "maxItems": max_actions,
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
            "overflow": {"type": "boolean"},
        },
        "required": ["actions", "overflow"],
        "additionalProperties": False,
    }


def source_clauses(transcript: str) -> tuple[Span, ...]:
    clauses = []
    boundary = re.compile(
        r";\s*|,\s*(?=(?:and\s+)?(?:I\b|we\b|by\b|on\b|monday\b|tuesday\b|"
        r"wednesday\b|thursday\b|friday\b|saturday\b|sunday\b))",
        re.I,
    )
    for sentence in re.finditer(r"[^.!?\n]+[.!?]?", transcript):
        start = sentence.start()
        for separator in (*boundary.finditer(sentence[0]), None):
            end = sentence.start() + separator.start() if separator else sentence.end()
            raw = transcript[start:end]
            text = raw.strip()
            if text:
                clauses.append(
                    Span(start=start + len(raw) - len(raw.lstrip()), length=len(text), text=text)
                )
            if separator:
                start = sentence.start() + separator.end()
    return tuple(clauses)


def _temporal_span(source: Span, text: str | None) -> Span | None:
    if text is None:
        return None
    if not isinstance(text, str) or not text or len(text) > 512:
        raise ValueError("invalid temporal field")
    occurrences = [m.start() for m in re.finditer(re.escape(text), source.text)]
    if len(occurrences) != 1:
        raise ValueError("temporal evidence must occur uniquely in its source")
    return Span(start=source.start + occurrences[0], length=len(text), text=text)


def expand(raw: dict, clauses: tuple[Span, ...]) -> Envelope:
    if set(raw) - {"actions", "overflow"} or type(raw.get("overflow", False)) is not bool:
        raise ValueError("unexpected envelope field")
    if not isinstance(raw.get("actions"), list):
        raise ValueError("missing actions")
    actions = []
    for row in raw["actions"]:
        if isinstance(row, dict):
            required = {"op", "clause", "title", "time", "actor"}
            if (
                not required <= set(row)
                or set(row) - required - {"details", "plan"}
                or "details" in row
                and "plan" in row
            ):
                raise ValueError("unexpected action field")
            row = [
                row["op"],
                row["clause"],
                row["title"],
                row["time"],
                row["actor"],
                {"plan": row["plan"]}
                if "plan" in row
                else row["details"]
                if row.get("details") is not None
                else {},
            ]
        if not isinstance(row, list) or len(row) not in {5, 6}:
            raise ValueError("invalid action row")
        op, index, title, time, actor = row[:5]
        if (
            not isinstance(op, str)
            or op not in OPERATIONS
            or type(index) is not int
            or not 0 <= index < len(clauses)
        ):
            raise ValueError("invalid operation or clause")
        details = Details.model_validate(row[5] if len(row) == 6 else {})
        source = clauses[index]
        temporal = _temporal_span(source, time)
        recurrence = None
        if temporal:
            if re.search(r"\bevery day\b|\bdaily\b", temporal.text, re.I):
                recurrence = "FREQ=DAILY"
            else:
                weekday = re.search(
                    r"\bevery (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                    temporal.text,
                    re.I,
                )
                if weekday:
                    recurrence = (
                        "FREQ=WEEKLY;BYDAY="
                        + {
                            "monday": "MO",
                            "tuesday": "TU",
                            "wednesday": "WE",
                            "thursday": "TH",
                            "friday": "FR",
                            "saturday": "SA",
                            "sunday": "SU",
                        }[weekday[1].casefold()]
                    )
        classification = (
            "INFORMATION"
            if op == "context"
            else "PROJECT"
            if op == "plan"
            else "PROGRESS"
            if op == "complete"
            else "CORRECTION"
            if op.startswith("update_")
            else "REMINDER"
            if "reminder" in op
            else "COMMITMENT"
        )
        actions.append(
            Proposal(
                operation=OPERATIONS[op],
                classification=classification,
                title=title,
                # Context is a factual annotation, not a personal/team assignment.
                # Preserve third-party suppression; other actor mismatches still
                # reach the source validator unchanged for every action operation.
                actor="context" if op == "context" and actor in {"user", "team"} else actor,
                source=source,
                temporal=temporal,
                recurrence=recurrence,
                recurrence_source=temporal if recurrence else None,
                plan_mention=details.plan,
                target_mention=details.target,
                content=details.content or (source.text if op == "context" else None),
            )
        )
    return Envelope(actions=actions, overflow=raw.get("overflow", False))
