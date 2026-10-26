"""Dedicated knowledge delivery carries a durable SQL job UUID only."""

import asyncio
from uuid import UUID

from dramatiq import Message

QUEUE_NAME = "roma_knowledge"
ACTOR_NAME = "process_knowledge_ingestion"


class KnowledgePublisher:
    def __init__(self, broker):
        self.broker = broker
        broker.declare_queue(QUEUE_NAME)

    async def publish(self, job_id: UUID):
        if not isinstance(job_id, UUID):
            raise ValueError("Knowledge delivery requires UUID identity")
        await asyncio.to_thread(
            self.broker.enqueue,
            Message(
                queue_name=QUEUE_NAME,
                actor_name=ACTOR_NAME,
                args=(str(job_id),),
                kwargs={},
                options={},
            ),
        )

    async def close(self):
        await asyncio.to_thread(self.broker.close)
