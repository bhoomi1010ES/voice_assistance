import asyncio
import time
import uuid

from app.core.config import Settings
from app.services.infrastructure import Infrastructure
from app.services.voice_confirmation import PendingConfirmation, RedisVoiceConfirmationStore


async def main() -> None:
    infrastructure = Infrastructure(Settings())
    assert infrastructure.redis is not None
    store = RedisVoiceConfirmationStore(infrastructure.redis, ttl_seconds=30)
    pending = PendingConfirmation.new(
        authenticated_user_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        original_turn_id=uuid.uuid4(),
        original_response_id=uuid.uuid4(),
        tool_call_id="diagnostic-memory-save",
        tool_name="memory_save",
        validated_tool_arguments={
            "content": "diagnostic only",
            "memory_type": "fact",
        },
        idempotency_key=(uuid.uuid4(), uuid.uuid4(), "memory_save", "diagnostic"),
        ttl_seconds=30,
        user_timezone="Asia/Kolkata",
    )
    started = time.monotonic()
    stored = await asyncio.wait_for(store.create_or_get(pending), timeout=10)
    elapsed_ms = (time.monotonic() - started) * 1000
    scope = (stored.authenticated_user_id, stored.device_id, stored.session_id)
    removed = await infrastructure.redis.delete(store._key(scope))
    print(f"confirmation_store_ms={elapsed_ms:.1f}")
    print(f"confirmation_id_matches={stored.confirmation_id == pending.confirmation_id}")
    print(f"temporary_key_removed={removed == 1}")
    await infrastructure.close()


asyncio.run(main())
