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

    def is_cancelled(self, response_id: uuid.UUID) -> bool:
        return response_id in self._cancelled

    def clear(self, response_id: uuid.UUID | None = None) -> None:
        """Clear only the expected generation when one is supplied.

        A cancelled response can finish after a replacement response has been
        activated.  In that case stale cleanup must not clear the replacement
        generation.
        """

        expected = self._active if response_id is None else response_id
        self._cancelled.discard(expected)
        if response_id is None or self._active == response_id:
            self._active = None
