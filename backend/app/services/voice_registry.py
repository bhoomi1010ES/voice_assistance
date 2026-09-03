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
        redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[3])
        for i = 2, #KEYS do
            redis.call('set', KEYS[i], ARGV[2], 'EX', ARGV[4])
        end
        return 1
    end
    return 0
    """

    def __init__(
        self,
        redis: Redis | None,
        *,
        ttl_seconds: int,
        lease_ttl_seconds: int | None = None,
    ) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.lease_ttl_seconds = lease_ttl_seconds or ttl_seconds

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

    @classmethod
    def _device_value(cls, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> str:
        return f"{cls._owner_value(owner)}:{session_id}"

    @classmethod
    def _device_owner_prefix(cls, user_id: uuid.UUID, device_id: uuid.UUID) -> str:
        return f"{user_id}:{device_id}:"

    async def _device_owned_by(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
    ) -> tuple[Redis, str]:
        redis = self._require_redis()
        current = await redis.get(self._device_key(owner.device_id))
        if current not in {
            self._owner_value(owner),
            self._device_value(owner, session_id),
        }:
            raise VoiceSessionConflict("voice connection ownership changed")
        return redis, current

    async def acquire(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> bool:
        redis = self._require_redis()
        value = self._owner_value(owner)
        device_value = self._device_value(owner, session_id)
        result = await redis.eval(
            self._SET_IF_EMPTY,
            3,
            self._device_key(owner.device_id),
            self._session_key(session_id),
            self._user_key(owner.user_id, session_id),
            device_value,
            value,
            self.lease_ttl_seconds,
            self.ttl_seconds,
        )
        if int(result) != 1:
            return False
        await redis.set(self._response_key(session_id), "", ex=self.ttl_seconds)
        return True

    async def refresh(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis, _ = await self._device_owned_by(owner, session_id)
        for key in (
            self._device_key(owner.device_id),
            self._session_key(session_id),
            self._user_key(owner.user_id, session_id),
            self._turn_key(session_id),
            self._response_key(session_id),
        ):
            await redis.expire(
                key,
                (
                    self.lease_ttl_seconds
                    if key == self._device_key(owner.device_id)
                    else self.ttl_seconds
                ),
            )

    async def set_turn(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> None:
        redis, _ = await self._device_owned_by(owner, session_id)
        await redis.set(self._turn_key(session_id), str(turn_id), ex=self.ttl_seconds)
        await redis.set(self._response_key(session_id), str(response_id), ex=self.ttl_seconds)

    async def cancel_response(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> bool:
        redis, _ = await self._device_owned_by(owner, session_id)
        active = await redis.get(self._response_key(session_id))
        if active != str(response_id):
            return False
        await redis.set(self._cancel_key(session_id, response_id), "1", ex=self.ttl_seconds)
        await redis.set(self._response_key(session_id), "", ex=self.ttl_seconds)
        return True

    async def clear_turn(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis, _ = await self._device_owned_by(owner, session_id)
        await redis.delete(self._turn_key(session_id))

    async def clear_response(
        self,
        owner: VoiceRegistryOwner,
        session_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> bool:
        """Clear only the matching active response for this connection owner."""

        redis, _ = await self._device_owned_by(owner, session_id)
        key = self._response_key(session_id)
        if await redis.get(key) != str(response_id):
            return False
        await redis.set(key, "", ex=self.ttl_seconds)
        return True

    async def is_cancelled(self, session_id: uuid.UUID, response_id: uuid.UUID) -> bool:
        redis = self._require_redis()
        return bool(await redis.exists(self._cancel_key(session_id, response_id)))

    async def release(self, owner: VoiceRegistryOwner, session_id: uuid.UUID) -> None:
        redis = self._require_redis()
        device_value = await redis.get(self._device_key(owner.device_id))
        if device_value not in {
            self._owner_value(owner),
            self._device_value(owner, session_id),
        }:
            return
        # Keep marker cleanup owner-scoped and outside the Lua scan. Some
        # managed Redis deployments do not reliably return pattern matches
        # from SCAN invoked inside a script, while the owner check below still
        # prevents a stale connection from deleting a replacement owner.
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
            device_value,
            f"voice:session:{session_id}:cancelled:*",
        )

    async def release_stale_device_session(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> bool:
        """Release one stale device session without touching other owners.

        This is used only after durable session state has been identified as
        stale. The Redis-side owner check remains exact and race-safe, so a
        newly acquired replacement lock cannot be deleted by this cleanup.
        """

        redis = self._require_redis()
        device_key = self._device_key(device_id)
        session_key = self._session_key(session_id)
        current_device_value = await redis.get(device_key)
        session_owner_value = await redis.get(session_key)
        owner_prefix = self._device_owner_prefix(user_id, device_id)
        if not isinstance(current_device_value, str) or not current_device_value.startswith(
            owner_prefix
        ):
            return False
        if not isinstance(session_owner_value, str) or not session_owner_value.startswith(
            owner_prefix
        ):
            return False
        if current_device_value != session_owner_value and not current_device_value.endswith(
            f":{session_id}"
        ):
            return False

        marker_keys = [
            key async for key in redis.scan_iter(match=f"voice:session:{session_id}:cancelled:*")
        ]
        if marker_keys:
            await redis.delete(*marker_keys)
        result = await redis.eval(
            self._DELETE_IF_OWNER,
            5,
            device_key,
            session_key,
            self._user_key(user_id, session_id),
            self._turn_key(session_id),
            self._response_key(session_id),
            current_device_value,
            f"voice:session:{session_id}:cancelled:*",
        )
        return int(result) == 1
