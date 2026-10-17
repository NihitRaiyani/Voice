"""Atomic advisory leases. Owner retries do not extend the caller's decision window."""

from roma.domain.appointments.booking import HoldOwner, SlotKey

_ACQUIRE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then return redis.call('PTTL', KEYS[1]) end
if not current then
  local ok = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])
  if ok then return tonumber(ARGV[2]) * 1000 end
end
return -1
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RedisAppointmentHolds:
    def __init__(self, client, *, ttl_seconds: int = 90) -> None:
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("Hold TTL must be 1–300 seconds")
        self._client, self.ttl_seconds = client, ttl_seconds

    async def acquire(self, key: SlotKey, owner: HoldOwner) -> int:
        remaining = await self._client.eval(
            _ACQUIRE, 1, key.redis_key, owner.value, self.ttl_seconds
        )
        return max(0, int(remaining))

    async def owner(self, key: SlotKey) -> str | None:
        value = await self._client.get(key.redis_key)
        return value.decode() if isinstance(value, bytes) else value

    async def release(self, key: SlotKey, owner: HoldOwner) -> bool:
        return bool(await self._client.eval(_RELEASE, 1, key.redis_key, owner.value))
