"""Bounded, timezone-aware date windows for owner-scoped structured reads."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

ReadDateWindow = Literal["today", "tomorrow"]


def resolve_local_day_bounds(
    *,
    now_utc: datetime,
    timezone_name: str,
    window: ReadDateWindow,
) -> tuple[datetime, datetime]:
    """Resolve a local calendar day to a half-open UTC interval.

    Each local midnight is constructed independently so DST changes produce a
    23 or 25 hour UTC interval when appropriate.
    """

    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    zone = ZoneInfo(timezone_name)
    local_today = now_utc.astimezone(zone).date()
    target_date = local_today + (timedelta(days=1) if window == "tomorrow" else timedelta())
    local_start = datetime.combine(target_date, time.min, tzinfo=zone)
    local_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=zone)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def normalize_query_terms(values: list[str]) -> tuple[str, ...]:
    """Return bounded normalized terms for title/body matching."""

    return tuple(
        dict.fromkeys(" ".join(value.split()).strip() for value in values if value.strip())
    )


def format_local_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        return None
    hour = local.strftime("%I").lstrip("0") or "0"
    return (
        f"{local.day} {local.strftime('%B %Y')} at {hour}:{local.minute:02d} {local.strftime('%p')}"
    )
