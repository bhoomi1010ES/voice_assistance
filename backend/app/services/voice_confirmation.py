from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from redis.asyncio import Redis
from redis.exceptions import WatchError

ConfirmationStatus = Literal[
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "CONSUMED",
]
ConfirmationResolution = Literal["APPROVED", "REJECTED", "AMBIGUOUS"]
Scope = tuple[uuid.UUID, uuid.UUID, uuid.UUID]
IdempotencyKey = tuple[uuid.UUID, uuid.UUID, str, str]


class VoiceConfirmationError(RuntimeError):
    """Raised when the server-owned confirmation store is unavailable."""


def normalize_confirmation(text: str) -> str:
    """Normalize a short spoken confirmation without asking the LLM."""

    normalized = text.casefold().strip()
    normalized = re.sub(r"[^\w\s']", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def resolve_confirmation(text: str) -> ConfirmationResolution:
    normalized = normalize_confirmation(text)
    if normalized in {
        "yes",
        "yeah",
        "yep",
        "yes please",
        "confirm",
        "confirmed",
        "go ahead",
        "do it",
        "okay",
        "ok",
        "sure",
        "please do",
    }:
        return "APPROVED"
    if normalized in {
        "no",
        "no thanks",
        "cancel",
        "stop",
        "don't",
        "do not",
        "never mind",
        "nevermind",
    }:
        return "REJECTED"
    return "AMBIGUOUS"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class PendingConfirmation:
    """Server-owned, JSON-safe description of one pending mutation."""

    def __init__(
        self,
        *,
        confirmation_id: uuid.UUID,
        authenticated_user_id: uuid.UUID,
        device_id: uuid.UUID,
        session_id: uuid.UUID,
        original_turn_id: uuid.UUID,
        original_response_id: uuid.UUID,
        tool_call_id: str,
        tool_name: str,
        validated_tool_arguments: Mapping[str, Any],
        idempotency_key: IdempotencyKey,
        created_at: datetime,
        expires_at: datetime,
        status: ConfirmationStatus = "PENDING",
        result_content: str | None = None,
        user_timezone: str = "UTC",
    ) -> None:
        self.confirmation_id = confirmation_id
        self.authenticated_user_id = authenticated_user_id
        self.device_id = device_id
        self.session_id = session_id
        self.original_turn_id = original_turn_id
        self.original_response_id = original_response_id
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.validated_tool_arguments = dict(validated_tool_arguments)
        self.idempotency_key = idempotency_key
        self.created_at = created_at
        self.expires_at = expires_at
        self.status = status
        self.result_content = result_content
        self.user_timezone = user_timezone

    @classmethod
    def new(
        cls,
        *,
        authenticated_user_id: uuid.UUID,
        device_id: uuid.UUID,
        session_id: uuid.UUID,
        original_turn_id: uuid.UUID,
        original_response_id: uuid.UUID,
        tool_call_id: str,
        tool_name: str,
        validated_tool_arguments: Mapping[str, Any],
        idempotency_key: IdempotencyKey,
        ttl_seconds: int,
        user_timezone: str = "UTC",
    ) -> PendingConfirmation:
        created_at = _utc_now()
        return cls(
            confirmation_id=uuid.uuid4(),
            authenticated_user_id=authenticated_user_id,
            device_id=device_id,
            session_id=session_id,
            original_turn_id=original_turn_id,
            original_response_id=original_response_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            validated_tool_arguments=validated_tool_arguments,
            idempotency_key=idempotency_key,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ttl_seconds),
            user_timezone=user_timezone,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _utc_now()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": str(self.confirmation_id),
            "authenticated_user_id": str(self.authenticated_user_id),
            "device_id": str(self.device_id),
            "session_id": str(self.session_id),
            "original_turn_id": str(self.original_turn_id),
            "original_response_id": str(self.original_response_id),
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "validated_tool_arguments": self.validated_tool_arguments,
            "idempotency_key": [
                str(self.idempotency_key[0]),
                str(self.idempotency_key[1]),
                self.idempotency_key[2],
                self.idempotency_key[3],
            ],
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "result_content": self.result_content,
            "user_timezone": self.user_timezone,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PendingConfirmation:
        key = value.get("idempotency_key")
        if not isinstance(key, list) or len(key) != 4:
            raise ValueError("invalid confirmation idempotency key")
        status = value.get("status", "PENDING")
        if status not in {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "EXPIRED",
            "CANCELLED",
            "CONSUMED",
        }:
            raise ValueError("invalid confirmation status")
        return cls(
            confirmation_id=uuid.UUID(str(value["confirmation_id"])),
            authenticated_user_id=uuid.UUID(str(value["authenticated_user_id"])),
            device_id=uuid.UUID(str(value["device_id"])),
            session_id=uuid.UUID(str(value["session_id"])),
            original_turn_id=uuid.UUID(str(value["original_turn_id"])),
            original_response_id=uuid.UUID(str(value["original_response_id"])),
            tool_call_id=str(value["tool_call_id"]),
            tool_name=str(value["tool_name"]),
            validated_tool_arguments=dict(value["validated_tool_arguments"]),
            idempotency_key=(
                uuid.UUID(str(key[0])),
                uuid.UUID(str(key[1])),
                str(key[2]),
                str(key[3]),
            ),
            created_at=_as_utc(str(value["created_at"])),
            expires_at=_as_utc(str(value["expires_at"])),
            status=status,
            result_content=value.get("result_content"),
            user_timezone=str(value.get("user_timezone", "UTC")),
        )


class VoiceConfirmationStore(Protocol):
    async def create_or_get(self, pending: PendingConfirmation) -> PendingConfirmation: ...

    async def get(self, scope: Scope) -> PendingConfirmation | None: ...

    async def claim(
        self, scope: Scope, confirmation_id: uuid.UUID
    ) -> PendingConfirmation | None: ...

    async def transition(
        self,
        scope: Scope,
        confirmation_id: uuid.UUID,
        status: ConfirmationStatus,
        *,
        result_content: str | None = None,
    ) -> PendingConfirmation | None: ...

    async def cancel_scope(self, scope: Scope) -> bool: ...


class InMemoryVoiceConfirmationStore:
    """Deterministic store used by unit tests; production uses Redis."""

    def __init__(self) -> None:
        self._values: dict[Scope, PendingConfirmation] = {}
        self._lock = asyncio.Lock()

    async def create_or_get(self, pending: PendingConfirmation) -> PendingConfirmation:
        scope = (pending.authenticated_user_id, pending.device_id, pending.session_id)
        async with self._lock:
            current = self._values.get(scope)
            if current is not None and current.status in {"PENDING", "APPROVED"}:
                return current
            self._values[scope] = pending
            return pending

    async def get(self, scope: Scope) -> PendingConfirmation | None:
        async with self._lock:
            current = self._values.get(scope)
            if current is not None and current.status == "PENDING" and current.is_expired():
                current.status = "EXPIRED"
            return current

    async def claim(self, scope: Scope, confirmation_id: uuid.UUID) -> PendingConfirmation | None:
        async with self._lock:
            current = self._values.get(scope)
            if current is None or current.confirmation_id != confirmation_id:
                return None
            if current.status != "PENDING":
                return None
            if current.is_expired():
                current.status = "EXPIRED"
                return None
            current.status = "APPROVED"
            return current

    async def transition(
        self,
        scope: Scope,
        confirmation_id: uuid.UUID,
        status: ConfirmationStatus,
        *,
        result_content: str | None = None,
    ) -> PendingConfirmation | None:
        async with self._lock:
            current = self._values.get(scope)
            if current is None or current.confirmation_id != confirmation_id:
                return None
            if not self._can_transition(current.status, status):
                return None
            current.status = status
            current.result_content = result_content
            return current

    async def cancel_scope(self, scope: Scope) -> bool:
        async with self._lock:
            current = self._values.get(scope)
            if current is None or current.status not in {"PENDING", "APPROVED"}:
                return False
            current.status = "CANCELLED"
            return True

    @staticmethod
    def _can_transition(
        current: ConfirmationStatus,
        target: ConfirmationStatus,
    ) -> bool:
        return target in {
            "PENDING": {"APPROVED", "REJECTED", "EXPIRED", "CANCELLED"},
            "APPROVED": {"CONSUMED", "CANCELLED"},
        }.get(current, set())


class RedisVoiceConfirmationStore:
    """Redis-backed confirmation state scoped to one authenticated voice session."""

    def __init__(self, redis: Redis | None, *, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def _require_redis(self) -> Redis:
        if self.redis is None:
            raise VoiceConfirmationError("REDIS_URL is not configured")
        return self.redis

    @staticmethod
    def _key(scope: Scope) -> str:
        user_id, device_id, session_id = scope
        return f"voice:confirmation:{user_id}:{device_id}:{session_id}"

    @staticmethod
    def _decode(raw: str | bytes | None) -> PendingConfirmation | None:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("confirmation payload must be an object")
            return PendingConfirmation.from_dict(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise VoiceConfirmationError("invalid confirmation state") from error

    async def create_or_get(self, pending: PendingConfirmation) -> PendingConfirmation:
        redis = self._require_redis()
        key = self._key(
            (pending.authenticated_user_id, pending.device_id, pending.session_id)
        )
        payload = json.dumps(pending.to_dict(), separators=(",", ":"), ensure_ascii=False)
        ttl = max(1, int((pending.expires_at - _utc_now()).total_seconds()))
        if await redis.set(key, payload, ex=ttl, nx=True):
            return pending
        current = await self.get(
            (pending.authenticated_user_id, pending.device_id, pending.session_id)
        )
        if current is None or current.status in {
            "EXPIRED",
            "REJECTED",
            "CANCELLED",
            "CONSUMED",
        }:
            # A terminal record may still exist until its short Redis TTL
            # expires. Replace it only through the scoped key; no global
            # Redis cleanup is involved.
            await redis.set(key, payload, ex=ttl)
            return pending
        if current is None:
            raise VoiceConfirmationError("confirmation state could not be created")
        return current

    async def get(self, scope: Scope) -> PendingConfirmation | None:
        redis = self._require_redis()
        key = self._key(scope)
        current = self._decode(await redis.get(key))
        if current is not None and current.status == "PENDING" and current.is_expired():
            current.status = "EXPIRED"
            await redis.set(key, json.dumps(current.to_dict(), separators=(",", ":")), ex=1)
        return current

    async def claim(self, scope: Scope, confirmation_id: uuid.UUID) -> PendingConfirmation | None:
        redis = self._require_redis()
        key = self._key(scope)
        async with redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current = self._decode(await pipe.get(key))
                    if current is None or current.confirmation_id != confirmation_id:
                        await pipe.reset()
                        return None
                    if current.status != "PENDING":
                        await pipe.reset()
                        return None
                    if current.is_expired():
                        current.status = "EXPIRED"
                        pipe.multi()
                        pipe.set(key, json.dumps(current.to_dict(), separators=(",", ":")), ex=1)
                        await pipe.execute()
                        return None
                    current.status = "APPROVED"
                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(current.to_dict(), separators=(",", ":")),
                        ex=max(1, int((current.expires_at - _utc_now()).total_seconds())),
                    )
                    await pipe.execute()
                    return current
                except WatchError:
                    continue

    async def transition(
        self,
        scope: Scope,
        confirmation_id: uuid.UUID,
        status: ConfirmationStatus,
        *,
        result_content: str | None = None,
    ) -> PendingConfirmation | None:
        redis = self._require_redis()
        key = self._key(scope)
        async with redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current = self._decode(await pipe.get(key))
                    if current is None or current.confirmation_id != confirmation_id:
                        await pipe.reset()
                        return None
                    if not InMemoryVoiceConfirmationStore._can_transition(
                        current.status, status
                    ):
                        await pipe.reset()
                        return None
                    current.status = status
                    current.result_content = result_content
                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(current.to_dict(), separators=(",", ":")),
                        ex=max(1, int((current.expires_at - _utc_now()).total_seconds())),
                    )
                    await pipe.execute()
                    return current
                except WatchError:
                    continue

    async def cancel_scope(self, scope: Scope) -> bool:
        current = await self.get(scope)
        if current is None or current.status not in {"PENDING", "APPROVED"}:
            return False
        return (
            await self.transition(scope, current.confirmation_id, "CANCELLED")
        ) is not None
