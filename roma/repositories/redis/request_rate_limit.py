"""Shared fixed-window admission with opaque keyed identities and atomic expiry."""

import asyncio
import math
from hashlib import sha256
from hmac import digest


class RequestRateLimiter:
    def __init__(self, client, key: bytes, *, timeout: float = 0.2):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Rate-limit key must contain at least 32 bytes")
        if not math.isfinite(timeout) or not 0 < timeout <= 3:
            raise ValueError("Invalid rate-limit timeout")
        self.client, self.key, self.timeout = client, key, timeout

    async def take(self, category: str, identity: str, *, limit: int, window: int) -> bool:
        if (
            type(limit) is not int
            or not 1 <= limit <= 10000
            or type(window) is not int
            or not 1 <= window <= 3600
            or not isinstance(category, str)
            or not 1 <= len(category) <= 64
            or not isinstance(identity, str)
            or not 1 <= len(identity) <= 512
        ):
            raise ValueError("Invalid rate-limit policy or identity")
        opaque = digest(self.key, (category + ":" + identity).encode(), sha256).hex()
        key = "roma:request-rate:" + opaque
        async with asyncio.timeout(self.timeout):
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window, nx=True)
                count, _ = await pipe.execute()
        return count <= limit
