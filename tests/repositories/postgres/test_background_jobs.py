"""Transactional handoff, leased claims, idempotent effects, and scheduled follow-up."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from roma.repositories.postgres.background_jobs import (
    LEASE_SECS,
    MAX_ATTEMPTS,
    BackgroundJobStore,
)
from roma.repositories.postgres.models import Call, Caller, CallEvent, FollowupJob
from roma.services.postcall_service import PostcallService
from roma.workers.background import run_background_worker
from roma.workers.postcall.job import PostcallJob
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url

__all__ = ["alembic_config", "disposable_postgres_url"]

pytestmark = pytest.mark.postgres


@pytest.fixture()
def session_factory(alembic_config, disposable_postgres_url):
    command.upgrade(alembic_config, "head")
    engine = create_async_engine(disposable_postgres_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())
    command.downgrade(alembic_config, "base")


def _message(*, locked: bool = False) -> PostcallJob:
    now = datetime.now(UTC)
    return PostcallJob(
        call_sid="CA_background_1",
        recording_ref=None,
        locked_slot="2026-10-01T18:00:00+05:30" if locked else None,
        started_at=(now - timedelta(minutes=4)).isoformat(),
        ended_at=now.isoformat(),
        duration_secs=240,
        audio_secs=120,
        outcome="locked" if locked else "no_lock",
        direction="outbound",
        final_stage="close",
        turn_count=8,
    )


def test_call_and_jobs_commit_once_on_redelivery(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        service = PostcallService(store)
        message = _message(locked=True)
        await service.persist(message)
        await service.persist(message)
        async with session_factory() as session:
            calls = (await session.scalars(select(Call))).all()
            jobs = (await session.scalars(select(FollowupJob))).all()
            return calls, jobs

    calls, jobs = asyncio.run(run())
    assert len(calls) == 1
    assert calls[0].provider_call_id == "CA_background_1"
    assert calls[0].final_stage == "close"
    assert {job.job_type for job in jobs} == {
        "compute_statistics",
        "summarize_call",
        "update_lead",
        "send_followup",
    }
    assert all(job.status == "pending" for job in jobs)
    assert next(
        job for job in jobs if job.job_type == "send_followup"
    ).available_at > datetime.now(UTC)


def test_retry_backoff_and_worker_commit_effects(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        first = await store.claim(now=now, owner="worker-1")
        assert first is not None
        status = await store.fail(first, owner="worker-1", now=now, error_type="TimeoutError")
        assert status == "failed"
        async with session_factory() as session:
            failed = await session.get(FollowupJob, first.id)
            assert failed.available_at > now
            assert failed.last_error == "TimeoutError"
        processed = await run_background_worker(store, drain=True)
        async with session_factory() as session:
            events = (await session.scalars(select(CallEvent))).all()
            jobs = (await session.scalars(select(FollowupJob))).all()
            return first, processed, events, jobs

    first, processed, events, jobs = asyncio.run(run())
    assert processed == 2
    assert len(events) == 2
    assert any(job.id == first.id and job.status == "failed" for job in jobs)
    assert all(job.status in {"failed", "succeeded", "dead_letter"} for job in jobs)
    assert any(job.job_type == "update_lead" and job.status == "dead_letter" for job in jobs)


def test_due_retry_and_duplicate_completion_have_one_effect(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        first = await store.claim(now=now, owner="worker-1")
        await store.fail(first, owner="worker-1", now=now, error_type="TimeoutError")
        for _ in range(2):
            other = await store.claim(now=now, owner="worker-2")
            assert other is not None and other.id != first.id
            await store.complete(
                other,
                owner="worker-2",
                now=now,
                event_type=other.job_type,
                event_payload={},
            )
        retry = await store.claim(now=now + timedelta(seconds=3), owner="worker-2")
        assert retry.id == first.id
        assert retry.attempts == 2
        effect_type = first.job_type
        result = await store.complete(
            retry,
            owner="worker-2",
            now=now,
            event_type=effect_type,
            event_payload={"duration_secs": 240},
        )
        duplicate = await store.complete(
            retry,
            owner="worker-2",
            now=now,
            event_type=effect_type,
            event_payload={"duration_secs": 240},
        )
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(CallEvent)
                .where(CallEvent.event_type == effect_type)
            )
        return result, duplicate, count

    assert asyncio.run(run()) == (True, False, 1)


def test_claims_are_distinct_between_workers(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        first, second = await asyncio.gather(
            store.claim(now=now, owner="a"), store.claim(now=now, owner="b")
        )
        return first, second

    first, second = asyncio.run(run())
    assert first is not None and second is not None
    assert first.id != second.id


def test_linked_caller_is_updated_by_the_lead_job(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        message = replace(_message(), caller_digest="a" * 64)
        await PostcallService(store).persist(message)
        await run_background_worker(store, drain=True)
        async with session_factory() as session:
            caller = (await session.scalars(select(Caller))).one()
            call = (await session.scalars(select(Call))).one()
            lead_job = (
                await session.scalars(
                    select(FollowupJob).where(FollowupJob.job_type == "update_lead")
                )
            ).one()
            return caller, call, lead_job

    caller, call, lead_job = asyncio.run(run())
    assert call.caller_id == caller.id
    assert caller.current_status == "contacted"
    assert lead_job.status == "succeeded"


def test_expired_lease_is_reclaimed_and_failure_eventually_dead_letters(session_factory):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        now = datetime.now(UTC)
        first = await store.claim(now=now, owner="crashed")
        for _ in range(2):
            other = await store.claim(now=now, owner="cleaner")
            await store.complete(
                other,
                owner="cleaner",
                now=now,
                event_type=other.job_type,
                event_payload={},
            )
        now += timedelta(seconds=LEASE_SECS + 1)
        current = await store.claim(now=now, owner="replacement")
        assert current.id == first.id and current.attempts == 2
        while True:
            status = await store.fail(
                current, owner="replacement", now=now, error_type="TimeoutError"
            )
            if status == "dead_letter":
                break
            now += timedelta(seconds=301)
            current = await store.claim(now=now, owner="replacement")
        async with session_factory() as session:
            row = await session.get(FollowupJob, first.id)
            return (
                row.status,
                row.attempts,
                row.last_error,
                await store.claim(now=now + timedelta(days=1), owner="another"),
            )

    status, attempts, error, next_job = asyncio.run(run())
    assert (status, attempts, error, next_job) == (
        "dead_letter",
        MAX_ATTEMPTS,
        "TimeoutError",
        None,
    )


def test_terminal_recording_preserves_reason_and_attempt_count(session_factory):
    async def run():
        message = replace(_message(), recording_ref="synthetic-capture.s16le", attempts=4)
        await PostcallService(BackgroundJobStore(session_factory)).persist(
            message, "processing_failed"
        )
        async with session_factory() as session:
            job = await session.scalar(
                select(FollowupJob).where(FollowupJob.job_type == "process_recording")
            )
            assert job.status == "dead_letter"
            assert job.attempts == 5
            assert job.last_error == "processing_failed"

    asyncio.run(run())


def test_due_followup_with_explicit_consent_and_sender_has_one_effect(session_factory):
    from unittest.mock import AsyncMock

    from roma.workers.background import BackgroundHandlers, process_background_job

    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message(locked=True), followup_consent=True)
        async with session_factory() as session, session.begin():
            job = await session.scalar(
                select(FollowupJob).where(FollowupJob.job_type == "send_followup")
            )
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            job_id, key = job.id, job.idempotency_key
        sender = AsyncMock()
        sender.send.return_value = True
        handlers = BackgroundHandlers(followup=sender)
        await asyncio.gather(
            *(process_background_job(store, job_id, handlers=handlers) for _ in range(20))
        )
        sender.send.assert_awaited_once_with(call_id=job.call_id, idempotency_key=key)
        async with session_factory() as session:
            settled = await session.get(FollowupJob, job_id)
            assert settled.status == "succeeded" and settled.attempts == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CallEvent)
                    .where(CallEvent.event_type == "followup_sent")
                )
                == 1
            )

    asyncio.run(run())


def test_due_followup_without_consent_is_visible_and_never_sent(session_factory):
    from unittest.mock import AsyncMock

    from roma.workers.background import BackgroundHandlers, process_background_job

    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message(locked=True))
        async with session_factory() as session, session.begin():
            job = await session.scalar(
                select(FollowupJob).where(FollowupJob.job_type == "send_followup")
            )
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            job_id = job.id
        sender = AsyncMock()
        await process_background_job(
            store, job_id, handlers=BackgroundHandlers(followup=sender)
        )
        sender.send.assert_not_awaited()
        async with session_factory() as session:
            settled = await session.get(FollowupJob, job_id)
            assert (settled.status, settled.attempts, settled.last_error) == (
                "dead_letter",
                1,
                "followup_consent_required",
            )

    asyncio.run(run())
