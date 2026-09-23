from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.clock import Clock

DEFAULT_TIMEZONE = "UTC"

_EXPLICIT_TIMEZONE_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:new\s+york|nyc)(?:\s+city)?\s+time\b|"
            r"\bin\s+(?:new\s+york|nyc)\b",
            re.I,
        ),
        "America/New_York",
    ),
    (
        re.compile(
            r"\b(?:los\s+angeles|la)(?:\s+time)?\b|"
            r"\bin\s+los\s+angeles\b",
            re.I,
        ),
        "America/Los_Angeles",
    ),
    (re.compile(r"\b(?:london|uk)(?:\s+time)?\b|\bin\s+london\b", re.I), "Europe/London"),
    (
        re.compile(
            r"\b(?:kolkata|calcutta|india)(?:\s+(?:standard\s+)?time)?\b|"
            r"\b(?:ist|isd)\b|"
            r"\bin\s+(?:kolkata|india)\b",
            re.I,
        ),
        "Asia/Kolkata",
    ),
    (re.compile(r"\b(?:tokyo)(?:\s+time)?\b|\bin\s+tokyo\b", re.I), "Asia/Tokyo"),
    (re.compile(r"\b(?:sydney)(?:\s+time)?\b|\bin\s+sydney\b", re.I), "Australia/Sydney"),
    (re.compile(r"\b(?:utc|gmt)\b", re.I), "UTC"),
)


@dataclass(frozen=True)
class DeviceTimeContext:
    """The server's validated snapshot of one authenticated device clock."""

    device_epoch_ms: int
    timezone_id: str
    utc_offset: str
    locale: str
    source: str = "device"
    monotonic_captured_at: float = field(
        default_factory=time.monotonic,
        compare=False,
        repr=False,
    )

    @property
    def instant_utc(self) -> datetime:
        return datetime.fromtimestamp(self.device_epoch_ms / 1000, tz=UTC)

    def current_instant_utc(self) -> datetime:
        """Advance the validated device snapshot by monotonic elapsed time."""

        elapsed = max(0.0, time.monotonic() - self.monotonic_captured_at)
        return self.instant_utc + timedelta(seconds=elapsed)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_id)

    def local_now(self) -> datetime:
        return self.current_instant_utc().astimezone(self.zone)


def valid_timezone(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return None
    return candidate


def resolve_explicit_timezone(text: str | None) -> str | None:
    """Resolve a small, deterministic set of spoken timezone aliases."""

    normalized = " ".join((text or "").casefold().split())
    for pattern, timezone_name in _EXPLICIT_TIMEZONE_ALIASES:
        if pattern.search(normalized):
            return timezone_name
    # Accept an IANA identifier when speech recognition returns it verbatim.
    match = re.search(r"\b([A-Za-z]+/[A-Za-z_]+(?:/[A-Za-z_]+)?)\b", text or "")
    return valid_timezone(match.group(1)) if match else None


def timezone_for_request(
    text: str | None,
    *,
    device_timezone: str,
) -> tuple[str, str]:
    explicit = resolve_explicit_timezone(text)
    if explicit is not None:
        return explicit, "explicit"
    return device_timezone, "device"


def build_device_time_context(
    payload: object | None,
    *,
    fallback_clock: Clock,
    fallback_timezone: str = DEFAULT_TIMEZONE,
    legacy_timezone: str | None = None,
) -> DeviceTimeContext:
    """Validate client time data and apply the documented backend fallback.

    A malformed epoch or IANA zone is never allowed to poison a session. The
    fallback is the server clock in UTC (or a configured valid fallback zone);
    the supplied UTC offset is diagnostic only and is never used for math.
    """

    value = payload if isinstance(payload, dict) else {}
    timezone_name = valid_timezone(value.get("timezone_id"))
    if timezone_name is None:
        timezone_name = (
            valid_timezone(legacy_timezone) or valid_timezone(fallback_timezone) or DEFAULT_TIMEZONE
        )

    epoch_value = value.get("device_epoch_ms")
    epoch_ms: int | None = None
    if isinstance(epoch_value, int) and not isinstance(epoch_value, bool) and epoch_value > 0:
        try:
            instant = datetime.fromtimestamp(epoch_value / 1000, tz=UTC)
            if 946684800 <= instant.timestamp() <= 4102444800:
                epoch_ms = epoch_value
        except (OverflowError, OSError, ValueError):
            epoch_ms = None

    source = (
        "device"
        if epoch_ms is not None and valid_timezone(value.get("timezone_id"))
        else "backend_fallback"
    )
    if epoch_ms is None:
        epoch_ms = int(fallback_clock.now_utc().timestamp() * 1000)
    zone = ZoneInfo(timezone_name)
    instant_utc = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    offset = value.get("utc_offset")
    if not isinstance(offset, str) or not offset.strip():
        offset = format_utc_offset(instant_utc.astimezone(zone).utcoffset())
    locale = value.get("locale") if isinstance(value.get("locale"), str) else "en"
    return DeviceTimeContext(
        device_epoch_ms=epoch_ms,
        timezone_id=timezone_name,
        utc_offset=offset[:16],
        locale=locale[:32],
        source=source,
    )


def format_local_time(context: DeviceTimeContext) -> dict[str, str]:
    local = context.local_now()
    hour = local.strftime("%I").lstrip("0") or "0"
    return {
        "local_date": f"{local.day} {local.strftime('%B %Y')}",
        "local_time": f"{hour}:{local.minute:02d} {local.strftime('%p')}",
        "timezone": context.timezone_id,
        "utc_offset": format_utc_offset(local.utcoffset()),
    }


def format_utc_offset(offset: timedelta | None) -> str:
    """Render a datetime UTC offset as the protocol's signed HH:MM value."""

    if offset is None:
        return "+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    absolute_minutes = abs(total_minutes)
    return f"{sign}{absolute_minutes // 60:02d}:{absolute_minutes % 60:02d}"
