"""Owner-token call coordination against an isolated disposable Redis."""

import asyncio
from hashlib import sha256

import pytest
from redis.asyncio import Redis
from roma.repositories.redis.call_lease import RedisCallLease
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["redis_url"]


def test_call_has_one_owner_until_owner_releases(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        try:
            first = RedisCallLease(client, "CA-test-lease")
            competing = RedisCallLease(client, "CA-test-lease")
            assert not await first.owned()
            assert await first.acquire()
            assert await first.owned()
            assert not await competing.acquire()
            assert not await competing.owned()
            assert not await competing.release()
            assert await first.owned()
            assert await first.release()
            assert not await first.release()
            assert await competing.acquire()
            assert await competing.owned()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_ownership_requires_more_than_five_seconds_without_refreshing(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        try:
            lease = RedisCallLease(client, "CA-margin", ttl_seconds=6)
            assert await lease.acquire()
            (key,) = await client.keys("*")
            await client.pexpire(key, 5000)
            assert not await lease.owned()
            assert not await lease.acquire()
            assert 0 < await client.pttl(key) <= 5000
            await client.pexpire(key, 5500)
            assert await lease.owned()
            assert 5000 < await client.pttl(key) <= 5500
            await client.persist(key)
            assert not await lease.owned()
            assert await lease.release()
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("ttl", [None, True, False, 6.0, "6", -1, 0, 5, 601])
def test_ttl_must_be_an_exact_integer_between_six_and_six_hundred(ttl):
    with pytest.raises(ValueError, match="TTL"):
        RedisCallLease(object(), "CA-invalid-ttl", ttl_seconds=ttl)


@pytest.mark.parametrize("ttl", [6, 600])
def test_ttl_accepts_inclusive_bounds(ttl):
    RedisCallLease(object(), "CA-bounded-ttl", ttl_seconds=ttl)


@pytest.mark.parametrize("decode_responses", [False, True])
def test_crash_expiry_allows_successor_and_stale_release_is_harmless(
    redis_url, decode_responses
):
    async def run():
        client = Redis.from_url(redis_url, decode_responses=decode_responses)
        try:
            crashed = RedisCallLease(client, "CA-crashed")
            successor = RedisCallLease(client, "CA-crashed")
            assert await crashed.acquire()
            (key,) = await client.keys("*")
            await client.pexpire(key, 10)
            await asyncio.sleep(0.05)
            assert not await crashed.owned()
            assert await successor.acquire()
            assert not await crashed.release()
            assert not await crashed.owned()
            assert await successor.owned()
            assert await successor.release()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_concurrent_workers_have_one_winner_and_independent_calls(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        try:
            workers = [RedisCallLease(client, "CA-contended") for _ in range(100)]
            acquired = await asyncio.gather(*(worker.acquire() for worker in workers))
            assert sum(acquired) == 1
            ownership = await asyncio.gather(*(worker.owned() for worker in workers))
            assert ownership == acquired
            independent = RedisCallLease(client, "CA-independent")
            assert await independent.acquire()
            winner = workers[acquired.index(True)]
            assert await winner.release()
            assert await independent.owned()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_key_hashes_call_identity_and_defaults_to_six_minute_expiry(redis_url):
    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            call_sid = "CA-private-call-identity"
            lease = RedisCallLease(client, call_sid)
            assert await lease.acquire()
            (key,) = await client.keys("*")
            assert call_sid not in key
            assert key.endswith(sha256(call_sid.encode()).hexdigest())
            assert 355_000 < await client.pttl(key) <= 360_000
            owner = await client.get(key)
            assert owner and call_sid not in owner
            assert not await lease.acquire()
            assert 355_000 < await client.pttl(key) <= 360_000
        finally:
            await client.aclose()

    asyncio.run(run())
