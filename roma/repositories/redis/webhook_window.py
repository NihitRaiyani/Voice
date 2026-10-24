"""Advisory processing/done windows. Durable SQL receipts remain authoritative."""

from dataclasses import dataclass, field
from hashlib import sha256
from secrets import token_urlsafe

_BEGIN = """
local value = redis.call('GET', KEYS[1])
if not value then
  redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])
  return 1
end
if value == ARGV[3] then return 2 end
if string.sub(value, 1, string.len(ARGV[4])) == ARGV[4] then return 0 end
return -1
"""
_COMPLETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


@dataclass(frozen=True)
class WindowClaim:
    key: str
    digest: str = field(repr=False)
    owner: str | None = field(repr=False)
    status: str

    @property
    def processing_value(self):
        return f'p:{self.digest}:{self.owner}'


class RedisWebhookWindow:
    def __init__(self, client, *, processing_ttl=60, done_ttl=86400):
        if type(processing_ttl) is not int or not 1 <= processing_ttl <= 300:
            raise ValueError('Webhook processing TTL must be 1..300 seconds')
        if type(done_ttl) is not int or not max(3600, processing_ttl) <= done_ttl <= 604800:
            raise ValueError('Webhook done TTL must be 3600..604800 seconds')
        self.client, self.processing_ttl, self.done_ttl = client, processing_ttl, done_ttl

    async def begin(self, identity, digest):
        if not isinstance(identity, str) or not 0 < len(identity) <= 255:
            raise ValueError('Invalid webhook identity')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid webhook digest')
        key = 'roma:webhook:' + sha256(identity.encode()).hexdigest()
        owner = token_urlsafe(32)
        prefix = f'p:{digest}:'
        result = int(await self.client.eval(_BEGIN, 1, key, prefix + owner,
                                           self.processing_ttl, f'd:{digest}', prefix))
        if result < 0:
            return WindowClaim(key, digest, None, 'mismatch')  # SQL decides conflict.
        return WindowClaim(key, digest, owner if result == 1 else None,
                           {0: 'busy', 1: 'processing', 2: 'done'}[result])

    async def complete(self, claim):
        if claim.owner is None:
            return False
        return bool(await self.client.eval(_COMPLETE, 1, claim.key, claim.processing_value,
                                           f'd:{claim.digest}', self.done_ttl))

    async def release(self, claim):
        if claim.owner is None:
            return False
        return bool(await self.client.eval(_RELEASE, 1, claim.key, claim.processing_value))
