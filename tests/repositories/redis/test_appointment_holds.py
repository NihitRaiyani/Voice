import asyncio
from datetime import date, time
from uuid import uuid4

from redis.asyncio import Redis
from roma.domain.appointments.booking import HoldOwner, SlotKey
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["redis_url"]


def test_atomic_ownership_expiry_and_stale_cleanup(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        try:
            holds = RedisAppointmentHolds(client, ttl_seconds=90)
            key = SlotKey(uuid4(), date(2026, 10, 18), time(11))
            owners = [HoldOwner(uuid4(), uuid4()) for _ in range(100)]
            results = await asyncio.gather(*(holds.acquire(key, o) for o in owners))
            assert sum(value > 0 for value in results) == 1
            winner = owners[next(i for i, value in enumerate(results) if value > 0)]
            assert await holds.owner(key) == winner.value
            await client.pexpire(key.redis_key, 30000)
            assert 0 < await holds.acquire(key, winner) <= 30000  # retry cannot refresh TTL
            await client.pexpire(key.redis_key, 10)
            await asyncio.sleep(0.05)
            assert await holds.owner(key) is None
            successor = HoldOwner(winner.call_id, uuid4())
            assert await holds.acquire(key, successor) > 0
            assert not await holds.release(key, winner)
            assert await holds.owner(key) == successor.value
            assert await holds.release(key, successor)
            assert not await holds.release(key, successor)
        finally:
            await client.aclose()

    asyncio.run(run())
