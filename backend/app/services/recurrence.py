from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.task_due_dates import TaskDueDateResolutionError, _localize_strict

_WEEKDAY_NAMES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_UNTIL_PATTERN = re.compile(r"^\d{8}(?:T\d{6}Z)?$")


class RecurrenceResolutionError(ValueError):
    """Raised when a recurrence definition is malformed or unsafe."""


@dataclass(frozen=True)
class RecurrenceRule:
    frequency: str
    interval: int = 1
    by_weekday: tuple[int, ...] = ()
    count: int | None = None
    until: datetime | date | None = None


def parse_recurrence_rule(value: str) -> RecurrenceRule:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise RecurrenceResolutionError("recurrence rule is required and bounded")
    parts: dict[str, str] = {}
    for item in value.strip().upper().split(";"):
        if "=" not in item:
            raise RecurrenceResolutionError("recurrence rule contains an invalid part")
        key, raw = item.split("=", 1)
        if key in parts or not raw:
            raise RecurrenceResolutionError("recurrence rule contains a duplicate or empty part")
        parts[key] = raw
    frequency = parts.get("FREQ")
    if frequency not in {"DAILY", "WEEKLY"}:
        raise RecurrenceResolutionError("only DAILY and WEEKLY recurrence is supported")
    try:
        interval = int(parts.get("INTERVAL", "1"))
    except ValueError as error:
        raise RecurrenceResolutionError("recurrence interval is invalid") from error
    if not 1 <= interval <= 366:
        raise RecurrenceResolutionError("recurrence interval is outside the safe bound")
    count = None
    if "COUNT" in parts:
        try:
            count = int(parts["COUNT"])
        except ValueError as error:
            raise RecurrenceResolutionError("recurrence count is invalid") from error
        if not 1 <= count <= 366:
            raise RecurrenceResolutionError("recurrence count is outside the safe bound")
    until: datetime | date | None = None
    if "UNTIL" in parts:
        raw_until = parts["UNTIL"]
        if not _UNTIL_PATTERN.fullmatch(raw_until):
            raise RecurrenceResolutionError("recurrence UNTIL is invalid")
        if "T" in raw_until:
            until = datetime.strptime(raw_until, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        else:
            until = datetime.strptime(raw_until, "%Y%m%d").date()
    by_weekday: tuple[int, ...] = ()
    if "BYDAY" in parts:
        if frequency != "WEEKLY":
            raise RecurrenceResolutionError("BYDAY is only supported for WEEKLY recurrence")
        values = parts["BYDAY"].split(",")
        if len(values) > 7 or any(item not in _WEEKDAY_NAMES for item in values):
            raise RecurrenceResolutionError("recurrence BYDAY is invalid")
        by_weekday = tuple(sorted({_WEEKDAY_NAMES.index(item) for item in values}))
    unknown = set(parts) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY"}
    if unknown:
        raise RecurrenceResolutionError("recurrence rule contains an unsupported part")
    return RecurrenceRule(frequency, interval, by_weekday, count, until)


def validate_recurrence_rule(value: str | None) -> str | None:
    if value is None:
        return None
    parse_recurrence_rule(value)
    return value.strip().upper()


def next_occurrence(
    current_utc: datetime,
    *,
    timezone_name: str,
    rule: RecurrenceRule,
) -> datetime:
    if current_utc.tzinfo is None or current_utc.utcoffset() is None:
        raise RecurrenceResolutionError("recurrence occurrence must be timezone-aware")
    try:
        zone = ZoneInfo(timezone_name)
    except Exception as error:  # noqa: BLE001 - normalize zoneinfo errors
        raise RecurrenceResolutionError("recurrence timezone is invalid") from error
    current_local = current_utc.astimezone(zone)
    if rule.frequency == "DAILY":
        candidate_date = current_local.date() + timedelta(days=rule.interval)
    elif not rule.by_weekday:
        candidate_date = current_local.date() + timedelta(days=7 * rule.interval)
    else:
        candidate_date = _next_weekday(current_local.date(), rule)
    try:
        return _localize_strict(
            datetime.combine(candidate_date, current_local.timetz().replace(tzinfo=None)),
            zone,
            label="recurrence occurrence",
        )
    except TaskDueDateResolutionError as error:
        raise RecurrenceResolutionError(str(error)) from error


def recurrence_finished(next_at: datetime, *, rule: RecurrenceRule, occurrences_sent: int) -> bool:
    if rule.count is not None and occurrences_sent >= rule.count:
        return True
    if rule.until is None:
        return False
    if isinstance(rule.until, datetime):
        return next_at > rule.until
    return next_at.date() > rule.until


def _next_weekday(current_date: date, rule: RecurrenceRule) -> date:
    weekdays = rule.by_weekday or (current_date.weekday(),)
    for offset in range(1, 8 * rule.interval + 1):
        candidate = current_date + timedelta(days=offset)
        if candidate.weekday() in weekdays:
            if rule.interval == 1:
                return candidate
    start_of_next_period = current_date - timedelta(days=current_date.weekday())
    start_of_next_period += timedelta(days=7 * rule.interval)
    return start_of_next_period + timedelta(days=min(weekdays))
