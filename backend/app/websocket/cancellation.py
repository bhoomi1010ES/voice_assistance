from __future__ import annotations

import uuid


class CancellationGuard:
    """Tracks response generations and rejects stale outbound work."""

    def __init__(self) -> None:
        self._cancelled: set[uuid.UUID] = set()
        self._active: uuid.UUID | None = None

    def activate(self, response_id: uuid.UUID) -> None:
        self._active = response_id

    def cancel(self, response_id: uuid.UUID) -> bool:
        if response_id != self._active:
            return False
        self._cancelled.add(response_id)
        return True

    def can_emit(self, response_id: uuid.UUID) -> bool:
        return response_id == self._active and response_id not in self._cancelled

    def clear(self) -> None:
        if self._active is not None:
            self._cancelled.discard(self._active)
        self._active = None
