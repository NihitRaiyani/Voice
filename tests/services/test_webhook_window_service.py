import asyncio
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from roma.domain.webhooks import AnswerEvent, WebhookUnavailable
from roma.services.webhook_service import WebhookService


def event():
    return AnswerEvent('synthetic-provider-event', 'CA_test', 'inbound', 'a' * 64)


class Window:
    def __init__(self, status='processing'):
        from types import SimpleNamespace
        self.claim = SimpleNamespace(status=status, owner='synthetic-owner' if status == 'processing' else None)
        self.complete = AsyncMock()
        self.release = AsyncMock()
    async def begin(self, *args):
        return self.claim


def test_done_window_still_verifies_durable_receipt_and_digest():
    async def run():
        window = Window('done')
        store = AsyncMock()
        store.accept_answer.return_value = False
        service = WebhookService(store, window=window)
        assert await service.accept_answer(event()) is False
        store.accept_answer.assert_awaited_once_with(event())
        window.complete.assert_not_awaited()
    asyncio.run(run())


def test_processing_window_is_completed_only_after_commit_and_released_on_failure():
    async def run():
        window = Window()
        store = AsyncMock()
        async def accept(item):
            window.complete.assert_not_awaited()
            return True
        store.accept_answer.side_effect = accept
        service = WebhookService(store, window=window)
        assert await service.accept_answer(event()) is True
        window.complete.assert_awaited_once_with(window.claim)
        window = Window()
        store.accept_answer.side_effect = WebhookUnavailable('synthetic database failure')
        with pytest.raises(WebhookUnavailable):
            await WebhookService(store, window=window).accept_answer(event())
        window.complete.assert_not_awaited()
        window.release.assert_awaited_once_with(window.claim)
    asyncio.run(run())


def test_busy_window_retries_later_and_redis_outage_falls_back_to_sql():
    async def run():
        store, window = AsyncMock(), Window('busy')
        with pytest.raises(WebhookUnavailable):
            await WebhookService(store, window=window).accept_answer(event())
        store.accept_answer.assert_not_awaited()
        window.begin = AsyncMock(side_effect=RedisConnectionError('private redis path'))
        store.accept_answer.return_value = True
        service = WebhookService(store, window=window)
        assert await service.accept_answer(event()) is True
        assert service.window_failures == 1
    asyncio.run(run())


def test_cancelled_processing_is_not_marked_done():
    async def run():
        window, store = Window(), AsyncMock()
        store.accept_answer.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await WebhookService(store, window=window).accept_answer(event())
        window.complete.assert_not_awaited()
        window.release.assert_awaited_once()
    asyncio.run(run())
