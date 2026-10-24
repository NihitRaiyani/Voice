import asyncio
from hashlib import sha256

from redis.asyncio import Redis
from roma.repositories.redis.webhook_window import RedisWebhookWindow
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ["redis_url"]


def test_processing_done_ttls_and_expiry_never_release_a_successor(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        windows = RedisWebhookWindow(client)
        identity, digest = 'synthetic-provider-event', 'a' * 64
        try:
            first = await windows.begin(identity, digest)
            assert first.status == 'processing' and first.owner
            duplicate = await windows.begin(identity, digest)
            assert duplicate.status == 'busy' and not duplicate.owner
            key = 'roma:webhook:' + sha256(identity.encode()).hexdigest()
            assert 0 < await client.ttl(key) <= 60
            assert await windows.complete(first)
            assert (await windows.begin(identity, digest)).status == 'done'
            assert 360 < await client.ttl(key) <= 86400
            assert not await windows.release(first)
            await client.pexpire(key, 1)
            await asyncio.sleep(.02)
            successor = await windows.begin(identity, digest)
            assert successor.owner != first.owner
            assert not await windows.complete(first) and not await windows.release(first)
            assert await windows.release(successor)
        finally:
            await client.aclose()
    asyncio.run(run())


def test_failed_processing_never_creates_a_done_marker(redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        windows = RedisWebhookWindow(client)
        try:
            first = await windows.begin('synthetic-failed', 'b' * 64)
            assert await windows.release(first)
            assert (await windows.begin('synthetic-failed', 'b' * 64)).status == 'processing'
        finally:
            await client.aclose()
    asyncio.run(run())
