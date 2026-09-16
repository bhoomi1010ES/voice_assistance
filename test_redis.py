import os
import asyncio
from urllib.parse import urlparse

import redis.asyncio as redis
from dotenv import load_dotenv


async def main():
    load_dotenv()

    redis_url = os.getenv("REDIS_URL")

    if not redis_url:
        print("ERROR: REDIS_URL is missing from .env")
        return

    # Make sure we are using plain Redis
    if redis_url.startswith("rediss://"):
        redis_url = redis_url.replace("rediss://", "redis://", 1)

    parsed = urlparse(redis_url)

    print("=" * 60)
    print("Redis Connection Test")
    print("=" * 60)

    print("Redis host :", parsed.hostname)
    print("Redis port :", parsed.port)
    print("Redis user :", parsed.username)
    print("Protocol   : redis://")
    print("")

    client = redis.from_url(
        redis_url,
        decode_responses=True,

        # Connection timeout
        socket_connect_timeout=15,

        # Command timeout
        socket_timeout=15,

        # Keep connection alive
        socket_keepalive=True,

        # Do not retry automatically
        retry_on_timeout=False,
    )

    try:
        print("Connecting to Redis...")
        print("")

        # --------------------------------------------------
        # PING
        # --------------------------------------------------
        print("[1/3] Testing PING...")

        result = await client.ping()

        print("Redis PING:", result)
        print("SUCCESS")
        print("")

        # --------------------------------------------------
        # SET
        # --------------------------------------------------
        print("[2/3] Testing SET...")

        await client.set(
            "voice_assistance_test",
            "redis_connection_ok",
            ex=30,
        )

        print("Redis SET: SUCCESS")
        print("")

        # --------------------------------------------------
        # GET
        # --------------------------------------------------
        print("[3/3] Testing GET...")

        value = await client.get(
            "voice_assistance_test"
        )

        print("Redis GET:", value)

        print("")

        if value == "redis_connection_ok":
            print("=" * 60)
            print("REDIS CONNECTION TEST PASSED")
            print("=" * 60)
        else:
            print("WARNING: Redis returned unexpected value.")

    except asyncio.TimeoutError:
        print("=" * 60)
        print("REDIS ERROR: TIMEOUT")
        print("=" * 60)
        print("")
        print("The TCP endpoint is reachable, but Redis is not")
        print("completing the Redis connection/handshake.")
        print("")

    except redis.TimeoutError as exc:
        print("=" * 60)
        print("REDIS ERROR: REDIS TIMEOUT")
        print("=" * 60)
        print("")
        print("Details:", repr(exc))

    except redis.AuthenticationError as exc:
        print("=" * 60)
        print("REDIS ERROR: AUTHENTICATION FAILED")
        print("=" * 60)
        print("")
        print("Redis was reached, but authentication failed.")
        print("")
        print("Details:", repr(exc))

    except redis.ConnectionError as exc:
        print("=" * 60)
        print("REDIS ERROR: CONNECTION FAILED")
        print("=" * 60)
        print("")
        print("Details:", repr(exc))

    except Exception as exc:
        print("=" * 60)
        print("REDIS ERROR")
        print("=" * 60)
        print("")
        print("Error type:", type(exc).__name__)
        print("Details   :", repr(exc))

    finally:
        try:
            await client.aclose()
        except Exception:
            pass

        print("")
        print("Redis connection closed.")


if __name__ == "__main__":
    asyncio.run(main())