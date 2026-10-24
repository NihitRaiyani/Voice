import asyncio
from hashlib import sha256

import fakeredis.aioredis
from roma.repositories.redis.auth_rate_limit import AuthRateLimiter


def test_auth_limit_is_atomic_expiring_and_never_stores_raw_peer_identifiers():
    async def run():
        client = fakeredis.aioredis.FakeRedis()
        limiter = AuthRateLimiter(client, b"synthetic-only-rate-key-" * 2, limit=2)
        assert await limiter.take("192.0.2.24")
        assert await limiter.take("192.0.2.24")
        assert not await limiter.take("192.0.2.24")
        assert await limiter.take("192.0.2.25")
        keys = await client.keys("roma:auth-rate:*")
        assert len(keys) == 2
        assert all(b"192.0.2." not in key for key in keys)
        assert all([0 < await client.ttl(key) <= 60 for key in keys])
        assert sha256(b"192.0.2.24").hexdigest().encode() not in b"".join(keys)
        await client.aclose()

    asyncio.run(run())
