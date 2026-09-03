from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Application clock boundary used by time-sensitive server logic."""

    def now_utc(self) -> datetime:
        """Return the current timezone-aware UTC timestamp."""


@dataclass(frozen=True)
class SystemClock:
    """Production clock backed by the system wall clock."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FrozenClock:
    """Deterministic clock for temporal-resolution tests."""

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("FrozenClock value must be timezone-aware")
        object.__setattr__(self, "value", self.value.astimezone(UTC))

    def now_utc(self) -> datetime:
        return self.value
