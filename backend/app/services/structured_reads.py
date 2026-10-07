"""Bounded, timezone-aware date windows for owner-scoped structured reads."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def format_stored_datetime(value: datetime | None, timezone_name: str) -> str | None:
    """Serialize a stored UTC instant in a zone, including SQLite's offset-free reloads.

    Only database values use this UTC convention; untrusted scheduling arguments
    must still pass the strict scheduling resolver.
    """

    if value is None:
        return None
    instant = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return instant.astimezone(ZoneInfo(timezone_name)).isoformat()


def format_local_datetime(value: str | None, timezone_name: str | None = None) -> str | None:
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
        if timezone_name:
            if local.tzinfo is None or local.utcoffset() is None:
                return None
            local = local.astimezone(ZoneInfo(timezone_name))
    except (ValueError, ZoneInfoNotFoundError):
        return None
    hour = local.strftime("%I").lstrip("0") or "0"
    return (
        f"{local.day} {local.strftime('%B %Y')} at {hour}:{local.minute:02d} {local.strftime('%p')}"
    )
