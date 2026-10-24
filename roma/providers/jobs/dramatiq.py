"""Dramatiq's Redis broker carries IDs, not sensitive business payloads."""

import asyncio
from uuid import UUID

from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO, Retries

QUEUE_NAME = "roma_background"
ACTOR_NAME = "process_background_job"
SUMMARY_QUEUE_NAME = "roma_background_summary"
SUMMARY_ACTOR_NAME = "summarize_background_call"
BROKER_NAMESPACE = "roma:dramatiq:v1"


def build_broker(redis_url: str) -> RedisBroker:
    return RedisBroker(
        url=redis_url,
        namespace=BROKER_NAMESPACE,
        socket_connect_timeout=2,
        socket_timeout=2,
        # PostgreSQL owns business retries. These cover delivery/database outages.
        middleware=[AsyncIO(), Retries(max_retries=3, min_backoff=1000, max_backoff=30000)],
    )


class DramatiqPublisher:
    def __init__(self, broker) -> None:
        self._broker = broker
        broker.declare_queue(QUEUE_NAME)
        broker.declare_queue(SUMMARY_QUEUE_NAME)

    async def publish(self, job_id: UUID, *, job_type: str | None = None) -> None:
        message: Message = Message(
            queue_name=SUMMARY_QUEUE_NAME if job_type == "summarize_call" else QUEUE_NAME,
            actor_name=SUMMARY_ACTOR_NAME if job_type == "summarize_call" else ACTOR_NAME,
            args=(str(job_id),),
            kwargs={},
            options={},
        )
        # The producer is synchronous. Keep its Redis I/O off the async loop.
        await asyncio.to_thread(self._broker.enqueue, message)

    async def close(self) -> None:
        await asyncio.to_thread(self._broker.close)
