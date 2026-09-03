from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
WEEKDAYS = {
    name: index
    for index, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}
TEMPORAL_WORDS = frozenset(
    {
        "today",
        "tomorrow",
        "tonight",
        "evening",
        "morning",
        "afternoon",
        "next",
        *MONTHS,
        *WEEKDAYS,
    }
)

_TIME_PATTERN = re.compile(
    r"\b(?:at|around|by)\s+(?P<hour>\d{1,2})"
    r"(?::(?P<minute>\d{2}))?\s*(?P<meridiem>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
_MONTH_DATE_PATTERN = re.compile(
    r"\b(?P<month>january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_WEEKDAY_PATTERN = re.compile(
    r"\b(?P<next>next\s+)?(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_RELATIVE_DURATION_PATTERN = re.compile(
    r"\bin\s+(?P<amount>\d+)\s+(?P<unit>minute|minutes|hour|hours)\b",
    re.IGNORECASE,
)


class TaskDueDateResolutionError(ValueError):
    """Raised when a task due date cannot be safely resolved."""


def has_temporal_expression(value: str) -> bool:
    normalized = _normalize(value)
    if _RELATIVE_DURATION_PATTERN.search(normalized):
        return True
    if _MONTH_DATE_PATTERN.search(normalized) or _ISO_DATE_PATTERN.search(normalized):
        return True
    if _WEEKDAY_PATTERN.search(normalized):
        return True
    return any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in TEMPORAL_WORDS)


def resolve_task_due_at(
    *,
    due_at: datetime | None,
    due_expression: str | None,
    source_transcript: str | None,
    now_utc: datetime,
    timezone_name: str,
) -> datetime | None:
    """Resolve model task arguments into one future, aware UTC timestamp.

    A relative expression in the trusted server transcript takes precedence over
    any absolute timestamp emitted by the model. This prevents a provider from
    replaying or hallucinating a stale date while retaining explicit absolute
    datetimes for callers that already supplied one.
    """

    now = _require_aware_utc(now_utc, "clock timestamp")
    transcript = (source_transcript or "").strip()
    model_expression = (due_expression or "").strip()
    expression = transcript if has_temporal_expression(transcript) else model_expression

    if expression:
        resolved = _parse_expression(expression, now, timezone_name)
        if resolved <= now:
            raise TaskDueDateResolutionError("resolved task due date must be in the future")
        return resolved

    if due_at is None:
        return None
    normalized_due_at = _require_aware_utc(due_at, "task due_at")
    if normalized_due_at <= now:
        raise TaskDueDateResolutionError("task due date must be in the future")
    return normalized_due_at


def format_local_due_at(due_at: datetime, timezone_name: str) -> str:
    """Return a canonical local ISO timestamp for confirmation display."""

    zone = _load_timezone(timezone_name)
    return _require_aware_utc(due_at, "task due_at").astimezone(zone).isoformat()


def _parse_expression(expression: str, now_utc: datetime, timezone_name: str) -> datetime:
    normalized = _normalize(expression)
    zone = _load_timezone(timezone_name)
    local_now = now_utc.astimezone(zone)

    duration_match = _RELATIVE_DURATION_PATTERN.search(normalized)
    if duration_match:
        amount = int(duration_match.group("amount"))
        if amount < 1:
            raise TaskDueDateResolutionError("relative duration must be positive")
        unit = duration_match.group("unit")
        delta = timedelta(minutes=amount) if unit.startswith("minute") else timedelta(hours=amount)
        return (now_utc + delta).astimezone(UTC)

    local_date = _resolve_calendar_date(normalized, local_now.date())
    if local_date is None:
        raise TaskDueDateResolutionError("task due date expression is not supported")
    local_time = _parse_clock_time(normalized)
    if local_time is None:
        raise TaskDueDateResolutionError(
            "a clock time is required for a calendar-relative task due date"
        )
    return datetime.combine(local_date, local_time, tzinfo=zone).astimezone(UTC)


def _resolve_calendar_date(value: str, local_today: date) -> date | None:
    if re.search(r"\btomorrow\b", value):
        return local_today + timedelta(days=1)
    if re.search(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b", value):
        return local_today
    if re.search(r"\bnext\s+week\b", value):
        return local_today + timedelta(days=7)

    weekday_match = _WEEKDAY_PATTERN.search(value)
    if weekday_match:
        target = WEEKDAYS[weekday_match.group("weekday").casefold()]
        days_ahead = (target - local_today.weekday()) % 7
        if days_ahead == 0 or weekday_match.group("next"):
            days_ahead = days_ahead or 7
        return local_today + timedelta(days=days_ahead)

    month_match = _MONTH_DATE_PATTERN.search(value)
    if month_match:
        year = int(month_match.group("year") or local_today.year)
        return _validated_date(
            year,
            MONTHS[month_match.group("month").casefold()],
            int(month_match.group("day")),
        )

    iso_match = _ISO_DATE_PATTERN.search(value)
    if iso_match:
        return _validated_date(
            int(iso_match.group("year")),
            int(iso_match.group("month")),
            int(iso_match.group("day")),
        )
    return None


def _parse_clock_time(value: str) -> time | None:
    match = _TIME_PATTERN.search(value)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").replace(".", "").casefold()
    if minute > 59:
        raise TaskDueDateResolutionError("task due time has an invalid minute")
    if meridiem:
        if hour < 1 or hour > 12:
            raise TaskDueDateResolutionError("12-hour task due time has an invalid hour")
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        raise TaskDueDateResolutionError("24-hour task due time has an invalid hour")
    return time(hour=hour, minute=minute)


def _normalize(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\ba\s*\.?\s*m\.?\b", "am", normalized)
    normalized = re.sub(r"\bp\s*\.?\s*m\.?\b", "pm", normalized)
    return " ".join(normalized.split())


def _load_timezone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise TaskDueDateResolutionError("user timezone is missing")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as error:
        raise TaskDueDateResolutionError("user timezone is invalid") from error


def _validated_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as error:
        raise TaskDueDateResolutionError("task due date is invalid") from error


def _require_aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TaskDueDateResolutionError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)
