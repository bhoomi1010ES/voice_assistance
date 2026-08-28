from __future__ import annotations

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis


class VoiceRegistryError(RuntimeError):
    """Raised when the active voice registry is unavailable."""


class VoiceSessionConflict(RuntimeError):
    """Raised when a device already owns an active voice connection."""


@dataclass(frozen=True)
class VoiceRegistryOwner:
    user_id: uuid.UUID
    device_id: uuid.UUID
    auth_session_id: uuid.UUID
    connection_id: uuid.UUID


class VoiceRegistry:
    """Redis-backed ownership and TTL registry for ephemeral voice state."""

    _DELETE_IF_OWNER = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        local cursor = '0'
        repeat
            local result = redis.call('scan', cursor, 'match', ARGV[2])
            cursor = result[1]
            for _, key in ipairs(result[2]) do
                redis.call('del', key)
            end
        until cursor == '0'
        return redis.call('del', unpack(KEYS))
    end
    return 0
    """

    _SET_IF_EMPTY = """
    if redis.call('exists', KEYS[1]) == 0 then
        for i = 1, #KEYS do
            redis.call('set', KEYS[i], ARGV[1], 'EX', ARGV[2])
        end
        return 1
    end
    return 0
    """

    def __init__(self, redis: Redis | None, *, ttl_seconds: int) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _session_key(session_id: uuid.UUID) -> str:
        return f"voice:session:{session_id}"

    @staticmethod
    def _device_key(device_id: uuid.UUID) -> str:
        return f"voice:device:{device_id}:active_session"

    @staticmethod
    def _user_key(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        return f"voice:user:{user_id}:active_sessions:{session_id}"

    @staticmethod
    def _turn_key(session_id: uuid.UUID) -> str:
        return f"voice:session:{session_id}:active_turn"

    @staticmethod
    def _response_key(session_id: uuid.UUID) -> str:
        return f"voice:session:{session_id}:active_response"

    @staticmethod
    def _cancel_key(session_id: uuid.UUID, response_id: uuid.UUID) -> str:
        return f"voice:session:{session_id}:cancelled:{response_id}"

    def _require_redis(self) -> Redis:
        if self.redis is None:
            raise VoiceRegistryError("REDIS_URL is not configured")
        return self.redis

    @staticmethod
    def _owner_value(owner: VoiceRegistryOwner) -> str:
        return ":".join(
            (
                str(owner.user_id),
                str(owner.device_id),
                str(owner.auth_session_id),
                str(owner.connection_id),
            )
        )

    async def acquire(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> bool:
        redis = self._require_redis()
        value = self._owner_value(owner)
        result = await redis.eval(
            self._SET_IF_EMPTY,
            3,
            self._device_key(owner.device_id),
            self._session_key(session_id),
            self._user_key(owner.user_id, session_id),
            value,
            self.ttl_seconds,
        )
        if int(result) != 1:
            return False
        await redis.set(self._response_key(session_id), "", ex=self.ttl_seconds)
        return True

    async def refresh(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis = self._require_redis()
        value = self._owner_value(owner)
        if await redis.get(self._device_key(owner.device_id)) != value:
            raise VoiceSessionConflict("voice connection ownership changed")
        for key in (
            self._device_key(owner.device_id),
            self._session_key(session_id),
            self._user_key(owner.user_id, session_id),
            self._turn_key(session_id),
            self._response_key(session_id),
        ):
            await redis.expire(key, self.ttl_seconds)

    async def set_turn(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> None:
        redis = self._require_redis()
        value = self._owner_value(owner)
        if await redis.get(self._device_key(owner.device_id)) != value:
            raise VoiceSessionConflict("voice connection ownership changed")
        await redis.set(self._turn_key(session_id), str(turn_id), ex=self.ttl_seconds)
        await redis.set(self._response_key(session_id), str(response_id), ex=self.ttl_seconds)

    async def cancel_response(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> bool:
        redis = self._require_redis()
        value = self._owner_value(owner)
        if await redis.get(self._device_key(owner.device_id)) != value:
            raise VoiceSessionConflict("voice connection ownership changed")
        active = await redis.get(self._response_key(session_id))
        if active != str(response_id):
            return False
        await redis.set(self._cancel_key(session_id, response_id), "1", ex=self.ttl_seconds)
        await redis.set(self._response_key(session_id), "", ex=self.ttl_seconds)
        return True

    async def clear_turn(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis = self._require_redis()
        value = self._owner_value(owner)
        if await redis.get(self._device_key(owner.device_id)) != value:
            raise VoiceSessionConflict("voice connection ownership changed")
        await redis.delete(self._turn_key(session_id))

    async def is_cancelled(self, session_id: uuid.UUID, response_id: uuid.UUID) -> bool:
        redis = self._require_redis()
        return bool(await redis.exists(self._cancel_key(session_id, response_id)))

    async def release(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis = self._require_redis()
        value = self._owner_value(owner)
        # Keep marker cleanup owner-scoped and outside the Lua scan.  Some
        # managed Redis deployments do not reliably return pattern matches
        # from SCAN invoked inside a script, while the owner check below still
        # prevents a stale connection from deleting a replacement owner.
        if await redis.get(self._device_key(owner.device_id)) != value:
            return
        marker_keys = [
            key async for key in redis.scan_iter(match=f"voice:session:{session_id}:cancelled:*")
        ]
        if marker_keys:
            await redis.delete(*marker_keys)
        await redis.eval(
            self._DELETE_IF_OWNER,
            5,
            self._device_key(owner.device_id),
            self._session_key(session_id),
            self._user_key(owner.user_id, session_id),
            self._turn_key(session_id),
            self._response_key(session_id),
            value,
            f"voice:session:{session_id}:cancelled:*",
        )
