"""Bound durable webhook acceptance before returning a successful provider response."""

import asyncio

from roma.domain.webhooks import AnswerEvent, WebhookStore, WebhookUnavailable

ACCEPT_TIMEOUT_SECS = 3.0


class WebhookService:
    def __init__(self, store: WebhookStore) -> None:
        self._store = store

    async def accept_answer(self, event: AnswerEvent) -> bool:
        try:
            async with asyncio.timeout(ACCEPT_TIMEOUT_SECS):
                return await self._store.accept_answer(event)
        except TimeoutError as exc:
            # Even if the commit succeeded but its reply was lost, retrying the same
            # key will discover the committed receipt instead of repeating effects.
            raise WebhookUnavailable("webhook persistence timed out") from exc
