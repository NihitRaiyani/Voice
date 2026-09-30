"""Real framework execution with disposable Redis/PostgreSQL and no paid providers."""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

pytest.importorskip(
    "dramatiq", reason="install the workers extra for framework integration tests"
)

from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import AsyncIO, Retries
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from roma.providers.jobs.dramatiq import QUEUE_NAME, DramatiqPublisher, build_broker
from roma.repositories.postgres.background_jobs import BackgroundJobStore
from roma.repositories.postgres.models import CallEvent, FollowupJob
from roma.services.job_dispatcher import dispatch_due_jobs
from roma.services.postcall_service import PostcallService
from roma.workers.dramatiq_worker import create_background_actor
from tests.repositories.postgres.test_background_jobs import _message
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url
from tests.repositories.postgres.test_webhooks import session_factory

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]


@pytest.fixture()
def redis_url():
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server is needed for the disposable Redis integration test")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with TemporaryDirectory(prefix="roma-dramatiq-redis-") as directory:
        process = subprocess.Popen(
            [
                binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                directory,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"redis://127.0.0.1:{port}/0"
        client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        try:
            for _ in range(100):
                try:
                    if client.ping():
                        break
                except (RedisConnectionError, OSError):
                    pass
                time.sleep(0.02)
            else:
                pytest.fail("disposable Redis did not start")
            yield url
        finally:
            client.close()
            process.terminate()
            process.wait(timeout=5)


def test_publisher_carries_only_job_identity():
    async def run():
        broker = StubBroker()
        job_id = uuid4()
        await DramatiqPublisher(broker).publish(job_id)
        message = next(iter(broker.consume(QUEUE_NAME)))
        assert message.args == (str(job_id),)
        assert message.kwargs == {}
        assert b"transcript" not in message.encode()

    asyncio.run(run())


def test_dispatch_failure_can_be_retried_without_consuming_business_attempts():
    async def run():
        store = AsyncMock()
        job_id = uuid4()
        store.dispatchable_ids.return_value = [job_id]
        publisher = AsyncMock()
        publisher.publish.side_effect = ConnectionError("redis unavailable")
        with pytest.raises(ConnectionError):
            await dispatch_due_jobs(store, publisher)
        store.claim.assert_not_awaited()
        store.fail.assert_not_awaited()
        publisher.publish.side_effect = None
        assert await dispatch_due_jobs(store, publisher) == 1

    asyncio.run(run())


@pytest.mark.postgres
def test_redis_framework_duplicate_delivery_commits_one_effect(session_factory, redis_url):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        broker = build_broker(redis_url)
        create_background_actor(broker, lambda: store)
        publisher = DramatiqPublisher(broker)
        # Redelivery before consumers start models lost publish acknowledgements.
        assert await dispatch_due_jobs(store, publisher) == 3
        assert await dispatch_due_jobs(store, publisher) == 3
        # This Redis belongs only to the disposable test fixture. Losing broker
        # data must not lose PostgreSQL intents or consume business attempts.
        await asyncio.to_thread(broker.client.flushdb)
        assert await dispatch_due_jobs(store, publisher) == 3
        assert await dispatch_due_jobs(store, publisher) == 3
        worker = Worker(broker, worker_threads=4, worker_timeout=20)
        worker.start()
        try:
            await asyncio.to_thread(broker.join, QUEUE_NAME, timeout=10000)
            await asyncio.to_thread(worker.join)
            async with session_factory() as session:
                jobs = (await session.scalars(select(FollowupJob))).all()
                assert sorted(job.status for job in jobs) == [
                    "dead_letter",
                    "succeeded",
                    "succeeded",
                ]
                assert all(job.attempts == 1 for job in jobs)
                assert await session.scalar(select(func.count()).select_from(CallEvent)) == 3
            assert await dispatch_due_jobs(store, publisher) == 0
        finally:
            await asyncio.to_thread(worker.stop, timeout=2000)
            await publisher.close()

    asyncio.run(run())


def test_framework_retries_delivery_errors_without_leaking_secrets(caplog):
    broker = StubBroker(
        middleware=[AsyncIO(), Retries(max_retries=1, min_backoff=1, max_backoff=1)]
    )
    attempts = []

    def broken_store():
        attempts.append(1)
        raise ConnectionError("postgresql://user:private-password@host/db")

    actor = create_background_actor(broker, broken_store)
    actor.send(str(uuid4()))
    worker = Worker(broker, worker_threads=1, worker_timeout=20)
    worker.start()
    try:
        broker.join(QUEUE_NAME, timeout=5000, fail_fast=False)
        worker.join()
        assert len(attempts) == 2
        assert len(broker.dead_letters) == 1
        assert "private-password" not in caplog.text
    finally:
        worker.stop(timeout=2000)
        broker.close()


@pytest.mark.postgres
def test_production_cli_process_owns_and_closes_its_async_database(
    session_factory, disposable_postgres_url, redis_url
):
    async def run():
        store = BackgroundJobStore(session_factory)
        await PostcallService(store).persist(_message())
        publisher = DramatiqPublisher(build_broker(redis_url))
        await dispatch_due_jobs(store, publisher)
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "scripts/run_background_worker.py",
                "--use-spawn",
                "--worker-shutdown-timeout",
                "5000",
            ],
            env={
                **os.environ,
                "DATABASE_URL": disposable_postgres_url,
                "REDIS_URL": redis_url,
                "SARVAM_API_KEY": "offline-test",
                "OPENAI_API_KEY": "offline-test",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                async with session_factory() as session:
                    states = list(await session.scalars(select(FollowupJob.status)))
                if all(state in ("succeeded", "dead_letter") for state in states):
                    break
                if process.poll() is not None:
                    pytest.fail("production task worker exited before processing its jobs")
                await asyncio.sleep(0.05)
            else:
                pytest.fail("production task worker did not finish its queued jobs")
        finally:
            # This process group contains only the worker created by this test.
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = await asyncio.to_thread(process.communicate, timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = await asyncio.to_thread(process.communicate)
            await publisher.close()
        assert process.returncode == 0, output
        assert "Event loop is closed" not in output
        assert "Unexpected failure" not in output

    asyncio.run(run())
