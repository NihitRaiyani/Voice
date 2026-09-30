"""Queue delivery is separate from durable job claims and idempotent effects."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from roma.repositories.postgres.background_jobs import (
    LEASE_SECS,
    MAX_ATTEMPTS,
    BackgroundJobStore,
)
from roma.repositories.postgres.models import FollowupJob
from roma.services.postcall_service import PostcallService
from sqlalchemy import select, update
from tests.repositories.postgres.test_background_jobs import _message
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url
from tests.repositories.postgres.test_webhooks import session_factory

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]
pytestmark = pytest.mark.postgres


def test_dispatch_does_not_claim_and_excludes_scheduled_followup(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message(locked=True))
        now = datetime.now(UTC)
        ids = await store.dispatchable_ids(now=now)
        assert len(ids) == 3
        assert await store.dispatchable_ids(now=now) == ids
        async with session_factory() as session:
            jobs = (await session.scalars(select(FollowupJob))).all()
            assert all(job.status == "pending" and job.attempts == 0 for job in jobs)
        # Follow-up delivery remains inactive even after its scheduled time.
        assert await store.dispatchable_ids(now=now + timedelta(days=1)) == ids

    asyncio.run(run())


def test_same_job_id_has_one_winner_and_completed_delivery_is_noop(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        job_id = (await store.dispatchable_ids(now=now))[0]
        results = await asyncio.gather(
            *(store.claim(now=now, owner=f"worker-{i}", job_id=job_id) for i in range(20))
        )
        winner = next(job for job in results if job is not None)
        owner = f"worker-{results.index(winner)}"
        assert sum(job is not None for job in results) == 1
        assert winner.attempts == 1
        assert await store.complete(
            winner, owner=owner, now=now, event_type="test_effect", event_payload={}
        )
        assert await store.claim(now=now, owner="replay", job_id=job_id) is None
        assert await store.claim(now=now, owner="unknown", job_id=uuid4()) is None

    asyncio.run(run())


def test_retries_wait_until_due_and_crashed_worker_lease_is_recovered(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        job_id = (await store.dispatchable_ids(now=now))[0]
        first = await store.claim(now=now, owner="one", job_id=job_id)
        assert (
            await store.fail(first, owner="one", now=now, error_type="TimeoutError") == "failed"
        )
        assert job_id not in await store.dispatchable_ids(now=now)
        assert await store.claim(now=now, owner="too-early", job_id=job_id) is None
        due = now + timedelta(seconds=4)
        assert job_id in await store.dispatchable_ids(now=due)
        second = await store.claim(now=due, owner="crashed", job_id=job_id)
        recovered = await store.claim(
            now=due + timedelta(seconds=LEASE_SECS + 1), owner="replacement", job_id=job_id
        )
        assert recovered.attempts == 3
        assert not await store.complete(
            second, owner="crashed", now=due, event_type="stale_effect", event_payload={}
        )

    asyncio.run(run())


def test_exhausted_crash_retries_dead_letter_without_another_execution(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        job_id = (await store.dispatchable_ids(now=now))[0]
        async with session_factory() as session, session.begin():
            await session.execute(
                update(FollowupJob)
                .where(FollowupJob.id == job_id)
                .values(
                    status="running",
                    attempts=MAX_ATTEMPTS,
                    lock_owner="crashed",
                    locked_at=now - timedelta(seconds=LEASE_SECS + 1),
                )
            )
        assert await store.claim(now=now, owner="sixth", job_id=job_id) is None
        async with session_factory() as session:
            job = await session.get(FollowupJob, job_id)
            assert job.status == "dead_letter"
            assert job.attempts == MAX_ATTEMPTS
        assert job_id not in await store.dispatchable_ids(now=now)

    asyncio.run(run())
