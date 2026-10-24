"""Publish durable job IDs; failed delivery never acknowledges the database intent."""

from datetime import UTC, datetime


async def dispatch_due_jobs(store, publisher, *, limit: int = 100) -> int:
    jobs = await store.dispatchable_jobs(now=datetime.now(UTC), limit=limit)
    for job_id, job_type in jobs:
        await publisher.publish(job_id, job_type=job_type)
    return len(jobs)
