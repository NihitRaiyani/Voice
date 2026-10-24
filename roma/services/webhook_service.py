"""Bound durable acceptance; Redis windows coalesce work but never replace receipts."""

import asyncio

from redis.exceptions import RedisError

from roma.core.logging import LogEvent, log_event
from roma.core.voice_errors import VoiceErrorCode
from roma.domain.webhooks import AnswerEvent, WebhookStore, WebhookUnavailable

ACCEPT_TIMEOUT_SECS = 3.0
WINDOW_TIMEOUT_SECS = 0.2


class WebhookService:
    def __init__(self, store: WebhookStore, *, window=None) -> None:
        self._store, self.window = store, window
        self.window_failures = 0

    async def _window(self, operation):
        try:
            async with asyncio.timeout(WINDOW_TIMEOUT_SECS):
                return await operation
        except (RedisError, OSError, TimeoutError):
            self.window_failures += 1
            log_event(LogEvent.STATE_CACHE_FAILED, error_code=VoiceErrorCode.DB.value)
            return None  # Only the advisory cache failed; SQL is still mandatory.

    async def accept_answer(self, event: AnswerEvent) -> bool:
        claim = None
        if self.window is not None:
            claim = await self._window(self.window.begin(event.provider_event_id, event.payload_digest))
            if claim is not None and claim.status == 'busy':
                raise WebhookUnavailable('webhook processing lease is active')
        try:
            async with asyncio.timeout(ACCEPT_TIMEOUT_SECS):
                # Even a cached done marker must verify SQL/digest. A cache is not
                # durable authority and cannot grant a successful provider response.
                accepted = await self._store.accept_answer(event)
        except BaseException as error:
            if claim is not None and claim.owner is not None:
                await self._window(self.window.release(claim))
            if isinstance(error, TimeoutError):
                raise WebhookUnavailable('webhook persistence timed out') from None
            raise
        if claim is not None and claim.owner is not None:
            await self._window(self.window.complete(claim))
        return accepted
