"""Bounded per-call ownership; expired owners cannot release their successors."""

from hashlib import sha256
from secrets import token_urlsafe

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_OWNED = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  if redis.call('PTTL', KEYS[1]) > 5000 then return 1 end
end
return 0
"""


class RedisCallLease:
    """One owner token per call worker, with no automatic lease refresh."""

    def __init__(self, client, call_sid: str, ttl_seconds: int = 360) -> None:
        if type(ttl_seconds) is not int or not 6 <= ttl_seconds <= 600:
            raise ValueError("Call lease TTL must be an integer from 6 to 600 seconds")
        self._client = client
        self._key = f"roma:call-lease:{sha256(call_sid.encode()).hexdigest()}"
        self._owner = token_urlsafe(32)
        self._ttl_seconds = ttl_seconds

    async def acquire(self) -> bool:
        return bool(
            await self._client.set(self._key, self._owner, nx=True, ex=self._ttl_seconds)
        )

    async def owned(self) -> bool:
        """Atomically check ownership with time for a bounded checkpoint write."""
        return bool(await self._client.eval(_OWNED, 1, self._key, self._owner))

    async def release(self) -> bool:
        return bool(await self._client.eval(_RELEASE, 1, self._key, self._owner))
