import asyncio
from hashlib import sha256

import pytest
from redis.asyncio import Redis
from roma.repositories.postgres.webhooks import WebhookRepository
from roma.repositories.redis.webhook_window import RedisWebhookWindow
from roma.services.webhook_service import WebhookService
from tests.repositories.postgres.test_webhooks import (
    alembic_config,
    counts,
    disposable_postgres_url,
    event,
    session_factory,
)
from tests.workers.test_dramatiq_worker import redis_url

__all__ = ['alembic_config', 'disposable_postgres_url', 'session_factory', 'redis_url']


def test_crash_after_sql_commit_before_done_recovers_one_business_effect(session_factory, redis_url):
    async def run():
        client = Redis.from_url(redis_url)
        window = RedisWebhookWindow(client)
        item = event()
        try:
            claim = await window.begin(item.provider_event_id, item.payload_digest)
            assert claim.owner
            assert await WebhookRepository(session_factory).accept_answer(item)
            # Process dies here: no complete/release. Its short lease expires.
            key = 'roma:webhook:' + sha256(item.provider_event_id.encode()).hexdigest()
            await client.pexpire(key, 1)
            await asyncio.sleep(.02)
            service = WebhookService(WebhookRepository(session_factory), window=window)
            assert await service.accept_answer(item) is False
            assert await counts(session_factory) == (1, 1, 1)
            assert (await window.begin(item.provider_event_id, item.payload_digest)).status == 'done'
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("cached_digest", ["a" * 64, "b" * 64])
def test_cached_done_never_substitutes_for_sql_receipt(session_factory, redis_url, cached_digest):
    async def run():
        client = Redis.from_url(redis_url)
        window = RedisWebhookWindow(client)
        item = event()
        try:
            claim = await window.begin(item.provider_event_id, cached_digest)
            assert await window.complete(claim)  # stale/incorrect cache, SQL still empty
            service = WebhookService(WebhookRepository(session_factory), window=window)
            assert await service.accept_answer(item) is True
            assert await service.accept_answer(item) is False
            assert await counts(session_factory) == (1, 1, 1)
        finally:
            await client.aclose()
    asyncio.run(run())
