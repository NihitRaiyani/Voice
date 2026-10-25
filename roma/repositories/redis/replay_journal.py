"""Explicit lab-only metadata Streams. Audio and business effects never enter Redis."""
import asyncio
import json
import re
from hashlib import sha256
from hmac import digest

from redis.exceptions import RedisError

from roma.domain.audio.replay import MetadataJournal, ReplayRecord

_APPEND = """
local old = redis.call('XRANGE', KEYS[1], ARGV[1], ARGV[1], 'COUNT', 1)
if #old > 0 then
  if old[1][2][2] == ARGV[2] then return 1 end
  return -1
end
local count = redis.call('XLEN', KEYS[1])
if count ~= tonumber(ARGV[3]) then return -2 end
if count >= tonumber(ARGV[4]) then return -3 end
local epoch, frames, last_time = 0, 0, 0
local tail = redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)
if #tail > 0 then
  local fields = {}
  for i = 1, #tail[1][2], 2 do fields[tail[1][2][i]] = tail[1][2][i + 1] end
  epoch, frames = tonumber(fields['epoch']), tonumber(fields['frames'])
  last_time = cjson.decode(fields['record']).at_ms
end
local value = cjson.decode(ARGV[2])
if value.at_ms < last_time then return -4 end
if value.kind == 'epoch' then
  if value.turn_epoch <= epoch then return -4 end
  epoch = value.turn_epoch
elseif value.turn_epoch > epoch or (value.kind ~= 'packet' and value.turn_epoch ~= epoch) then
  return -4
end
if value.kind == 'packet' then frames = frames + 1 end
if frames > 15000 then return -3 end
redis.call('XADD', KEYS[1], ARGV[1], 'record', ARGV[2], 'epoch', epoch, 'frames', frames)
redis.call('EXPIRE', KEYS[1], ARGV[5], 'NX')
return 0
"""


class ReplayJournalError(ValueError):
    """Fixed errors avoid Redis payloads, credentials and transport details."""


class RedisReplayJournal:
    def __init__(self, client, key: bytes, *, max_entries=4096, ttl_seconds=600, timeout=1.0):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("Replay signing key needs at least 32 bytes")
        if (type(max_entries) is not int or not 1 <= max_entries <= 50000
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600
                or type(timeout) not in (int, float) or not 0 < timeout <= 3):
            raise ValueError("Invalid replay journal bounds")
        self.client, self.key = client, key
        self.max_entries, self.ttl, self.timeout = max_entries, ttl_seconds, timeout

    def _key(self, lab_id):
        if not isinstance(lab_id, str) or not re.fullmatch(r"voice-lab-[0-9a-f]{32}", lab_id):
            raise ValueError("Use a unique isolated lab identity")
        return "roma:lab-replay:" + digest(self.key, lab_id.encode(), sha256).hex()

    async def append(self, lab_id, index, record):
        if type(index) is not int or not 0 <= index < self.max_entries:
            raise ReplayJournalError("Replay index exceeds its bound")
        # Re-parse the allowlisted shape rather than serializing arbitrary objects.
        clean = ReplayRecord.from_dict(record.to_dict())
        encoded = json.dumps(clean.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 1024:
            raise ReplayJournalError("Replay metadata exceeds its bound")
        try:
            async with asyncio.timeout(self.timeout):
                result = await self.client.eval(_APPEND, 1, self._key(lab_id),
                    f"{index + 1}-0", encoded, index, self.max_entries, self.ttl)
        except (RedisError, TimeoutError):
            raise ReplayJournalError("Replay journal unavailable") from None
        if result not in (0, 1):
            raise ReplayJournalError("Replay identity, order or capacity conflict")
        return result == 0

    async def read(self, lab_id):
        try:
            async with asyncio.timeout(self.timeout):
                rows = await self.client.xrange(self._key(lab_id), count=self.max_entries + 1)
        except (RedisError, TimeoutError):
            raise ReplayJournalError("Replay journal unavailable") from None
        if len(rows) > self.max_entries:
            raise ReplayJournalError("Replay metadata exceeds its bound")
        try:
            records = []
            epoch, frames = 0, 0
            for index, (identity, fields) in enumerate(rows):
                identity = identity.decode() if isinstance(identity, bytes) else identity
                fields = {k.decode() if isinstance(k, bytes) else k: v for k, v in fields.items()}
                if identity != f"{index + 1}-0" or set(fields) != {"record", "epoch", "frames"}:
                    raise ReplayJournalError("Replay metadata is corrupt")
                raw = fields["record"]
                if len(raw) > 1024:
                    raise ReplayJournalError("Replay metadata exceeds its bound")
                record = ReplayRecord.from_dict(json.loads(raw))
                frames += int(record.kind == "packet")
                if record.kind == "epoch":
                    epoch = record.turn_epoch
                stored_epoch, stored_frames = fields["epoch"], fields["frames"]
                if int(stored_epoch) != epoch or int(stored_frames) != frames:
                    raise ReplayJournalError("Replay metadata is corrupt")
                records.append(record)
            return tuple(MetadataJournal.from_records(records, max_entries=self.max_entries,
                         max_frames=15000, max_payload_bytes=9600000).snapshot())
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ReplayJournalError("Replay metadata is corrupt") from None

    async def delete(self, lab_id):
        try:
            async with asyncio.timeout(self.timeout):
                await self.client.delete(self._key(lab_id))
        except (RedisError, TimeoutError):
            raise ReplayJournalError("Replay journal unavailable") from None
