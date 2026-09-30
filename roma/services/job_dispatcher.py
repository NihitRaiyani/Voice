"""Publish durable job IDs; failed delivery never acknowledges the database intent."""

from datetime import UTC, datetime


async def dispatch_due_jobs(store, publisher, *, limit: int = 100) -> int:
    job_ids = await store.dispatchable_ids(now=datetime.now(UTC), limit=limit)
    for job_id in job_ids:
        await publisher.publish(job_id)
    return len(job_ids)
