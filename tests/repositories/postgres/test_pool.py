"""Real PostgreSQL pressure, transaction lifetime and timeout recovery checks."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import SecretStr
from roma.core.config import Settings
from roma.core.database import Database
from roma.domain.persistence import PersistenceUnavailable
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from scripts.check_database_pool import check_capacity, run_load
from sqlalchemy import text
from tests.repositories.postgres.test_migrations import (
    alembic_config,
    disposable_postgres_url,
)
from tests.repositories.postgres.test_repositories import session_factory

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]
pytestmark = pytest.mark.postgres


def _settings(url: str, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        sarvam_api_key=SecretStr("unused"),
        openai_api_key=SecretStr("unused"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        database_url=SecretStr(url),
        **overrides,
    )


def test_capacity_checks_real_server_reservations_and_all_processes(disposable_postgres_url):
    async def run():
        settings = _settings(disposable_postgres_url, database_pool_processes=4)
        database = Database.from_settings(settings)
        try:
            report = await check_capacity(database, settings, headroom=10)
            assert report["planned_peak_connections"] == 60
            assert report["available_to_roma"] == (
                report["max_connections"] - report["reserved_connections"] - 10
            )
            assert report["safe"] == (60 <= report["available_to_roma"])
            unsafe = await check_capacity(
                database, settings, headroom=report["max_connections"]
            )
            assert unsafe["safe"] is False
            assert database.engine.sync_engine.pool.checkedout() == 0
        finally:
            await database.close()

    asyncio.run(run())


@pytest.mark.parametrize(("size", "overflow"), [(2, 0), (2, 1), (5, 10)])
def test_100_call_tasks_share_a_bounded_pool(disposable_postgres_url, size, overflow):
    async def run():
        settings = _settings(
            disposable_postgres_url,
            database_pool_size=size,
            database_max_overflow=overflow,
        )
        database = Database.from_settings(settings)
        try:
            report = await run_load(
                database, concurrency=100, units_per_call=3, audio_wait_ms=10
            )
            assert report["safe"] is True, report
            assert report["completed_units"] == 300
            assert 1 <= report["peak_checked_out"] <= size + overflow
            assert report["checked_out_after"] == 0
            assert report["unit_p50_ms"] <= report["unit_p95_ms"]
        finally:
            await database.close()

    asyncio.run(run())


def test_repository_units_release_connections_before_all_audio_waits(session_factory):
    async def run():
        resume_audio = asyncio.Event()
        all_waiting = asyncio.Event()
        count = 0

        async def call():
            nonlocal count
            async with PostgresUnitOfWork(session_factory) as uow:
                assert await uow.calls.get(uuid4()) is None
                await uow.commit()
            count += 1
            if count == 100:
                all_waiting.set()
            await resume_audio.wait()

        tasks = [asyncio.create_task(call()) for _ in range(100)]
        try:
            await asyncio.wait_for(all_waiting.wait(), timeout=10)
            assert session_factory.kw["bind"].sync_engine.pool.checkedout() == 0
        finally:
            resume_audio.set()
            await asyncio.gather(*tasks)

    asyncio.run(run())


def test_exhaustion_is_bounded_and_pool_recovers_after_release(session_factory):
    async def run():
        url = session_factory.kw["bind"].url.render_as_string(hide_password=False)
        database = Database.from_settings(
            _settings(
                url,
                database_pool_size=1,
                database_max_overflow=0,
                database_pool_timeout_secs=0.05,
            )
        )
        try:
            async with database.session_factory() as held:
                await held.execute(text("SELECT 1"))
                with pytest.raises(PersistenceUnavailable):
                    async with PostgresUnitOfWork(database.session_factory) as uow:
                        await uow.calls.get(uuid4())
                assert database.engine.sync_engine.pool.checkedout() == 1
            async with PostgresUnitOfWork(database.session_factory) as uow:
                assert await uow.calls.get(uuid4()) is None
            assert database.engine.sync_engine.pool.checkedout() == 0
        finally:
            await database.close()

    asyncio.run(run())


def test_cancelled_database_task_returns_its_connection(session_factory):
    async def run():
        acquired = asyncio.Event()
        never = asyncio.Event()

        async def interrupted_unit():
            async with PostgresUnitOfWork(session_factory) as uow:
                await uow.calls.get(uuid4())
                acquired.set()
                await never.wait()

        task = asyncio.create_task(interrupted_unit())
        try:
            await asyncio.wait_for(acquired.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert session_factory.kw["bind"].sync_engine.pool.checkedout() == 0
        async with PostgresUnitOfWork(session_factory) as uow:
            assert await uow.calls.get(uuid4()) is None

    asyncio.run(run())
