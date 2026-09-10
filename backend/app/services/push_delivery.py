"""Provider-neutral reminder push delivery contracts.

The project has no configured Android push provider yet.  This module keeps the
durable reminder worker independent of FCM and supplies a deterministic fake
for acceptance tests without persisting or logging token values.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True)
class PushDeliveryResult:
    delivered: bool
    retryable: bool = False
    failure_code: str | None = None
    failure_reason: str | None = None


class PushDeliveryProvider(Protocol):
    async def send(
        self,
        *,
        token: str,
        delivery_id: str,
        title: str,
        body: str | None,
    ) -> PushDeliveryResult: ...


class UnavailablePushDeliveryProvider:
    """Safe production default until a real provider is configured."""

    async def send(
        self, *, token: str, delivery_id: str, title: str, body: str | None
    ) -> PushDeliveryResult:
        del token, delivery_id, title, body
        return PushDeliveryResult(
            delivered=False,
            retryable=False,
            failure_code="push_provider_unconfigured",
            failure_reason="push delivery provider is not configured",
        )


class FakePushDeliveryProvider:
    """Deterministic provider used by unit and integration acceptance tests."""

    def __init__(self, *, outcomes: list[PushDeliveryResult] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.deliveries: list[dict[str, str | None]] = []
        self._delivered_ids: set[str] = set()

    async def send(
        self, *, token: str, delivery_id: str, title: str, body: str | None
    ) -> PushDeliveryResult:
        if delivery_id in self._delivered_ids:
            return PushDeliveryResult(delivered=True)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if not outcome.delivered:
                return outcome
        self._delivered_ids.add(delivery_id)
        self.deliveries.append(
            {
                "delivery_id": delivery_id,
                "token_digest": sha256(token.encode()).hexdigest() if token else None,
                "title": title,
                "body": body,
            }
        )
        return PushDeliveryResult(delivered=True)
