"""Shared auth-attempt limits with opaque keyed peer identities and bounded expiry."""

import asyncio
import math
from hashlib import sha256
from hmac import digest


class AuthRateLimiter:
    def __init__(self, client, key: bytes, *, limit=10, window=60, timeout=1.0):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Authentication rate-limit key must contain at least 32 bytes")
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(window) is not int
            or not 1 <= window <= 3600
        ):
            raise ValueError("Invalid authentication rate-limit bounds")
        if not math.isfinite(timeout) or not 0 < timeout <= 3:
            raise ValueError("Invalid authentication rate-limit timeout")
        self.client, self.key, self.limit, self.window, self.timeout = (
            client,
            key,
            limit,
            window,
            timeout,
        )

    async def take(self, peer: str):
        if not isinstance(peer, str) or not 1 <= len(peer) <= 255:
            raise ValueError("Invalid peer identity")
        identity = digest(self.key, b"roma:auth-rate:" + peer.encode("utf-8"), sha256).hex()
        redis_key = f"roma:auth-rate:{identity}"
        async with asyncio.timeout(self.timeout):
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, self.window, nx=True)
                count, _ = await pipe.execute()
        return count <= self.limit
