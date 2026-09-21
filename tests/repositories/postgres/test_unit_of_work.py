from __future__ import annotations

import asyncio

import pytest
from asyncpg import exceptions as asyncpg_exceptions
from roma.domain.persistence import PersistenceConflict, PersistenceUnavailable
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from sqlalchemy.exc import DatabaseError, IntegrityError, OperationalError, TimeoutError


class FakeAsyncSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.commit_error: Exception | None = None
        self.rollback_error: Exception | None = None

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    async def close(self) -> None:
        self.closes += 1


class FakeSessionFactory:
    def __init__(self, session: FakeAsyncSession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> FakeAsyncSession:
        self.calls += 1
        return self.session


class SequenceSessionFactory:
    def __init__(self, *sessions: FakeAsyncSession) -> None:
        self._sessions = list(sessions)
        self.calls = 0

    def __call__(self) -> FakeAsyncSession:
        session = self._sessions[self.calls]
        self.calls += 1
        return session


def test_uncommitted_unit_of_work_rolls_back_and_closes_once():
    session = FakeAsyncSession()
    factory = FakeSessionFactory(session)

    async def use_uncommitted_uow() -> None:
        async with PostgresUnitOfWork(factory):
            pass

    asyncio.run(use_uncommitted_uow())

    assert factory.calls == 1
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closes == 1


def test_explicit_commit_persists_without_exit_rollback():
    session = FakeAsyncSession()

    async def use_committed_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)) as uow:
            await uow.commit()

    asyncio.run(use_committed_uow())

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closes == 1


def test_reusing_unit_of_work_gets_a_fresh_session_lifecycle_per_context():
    first = FakeAsyncSession()
    second = FakeAsyncSession()
    factory = SequenceSessionFactory(first, second)
    uow = PostgresUnitOfWork(factory)

    async def reuse_uow() -> None:
        async with uow:
            await uow.commit()
        async with uow:
            pass

    asyncio.run(reuse_uow())

    assert factory.calls == 2
    assert first.commits == 1
    assert first.rollbacks == 0
    assert first.closes == 1
    assert second.commits == 0
    assert second.rollbacks == 1
    assert second.closes == 1


def test_exception_rolls_back_and_preserves_original_error():
    session = FakeAsyncSession()
    original = ValueError("boom")

    async def fail_inside_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)):
            raise original

    with pytest.raises(ValueError) as error:
        asyncio.run(fail_inside_uow())

    assert error.value is original
    assert session.rollbacks == 1
    assert session.closes == 1


def test_generic_database_errors_are_not_reported_as_unavailable():
    session = FakeAsyncSession()
    database_error = DatabaseError("select bad_column", {}, Exception("programming bug"))
    session.commit_error = database_error

    async def commit_buggy_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)) as uow:
            await uow.commit()

    with pytest.raises(DatabaseError) as error:
        asyncio.run(commit_buggy_uow())

    assert error.value is database_error
    assert session.rollbacks == 1
    assert session.closes == 1


def test_integrity_error_becomes_persistence_conflict_with_cause():
    session = FakeAsyncSession()
    integrity_error = IntegrityError("insert", {}, Exception("unique"))
    session.commit_error = integrity_error

    async def commit_conflicting_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)) as uow:
            await uow.commit()

    with pytest.raises(PersistenceConflict) as error:
        asyncio.run(commit_conflicting_uow())

    assert error.value.__cause__ is integrity_error
    assert session.rollbacks == 1
    assert session.closes == 1


def test_integrity_error_inside_context_becomes_persistence_conflict_with_cause():
    session = FakeAsyncSession()
    integrity_error = IntegrityError("insert", {}, Exception("unique"))

    async def fail_with_conflict_inside_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)):
            raise integrity_error

    with pytest.raises(PersistenceConflict) as error:
        asyncio.run(fail_with_conflict_inside_uow())

    assert error.value.__cause__ is integrity_error
    assert session.rollbacks == 1
    assert session.closes == 1


@pytest.mark.parametrize(
    "db_error",
    [
        OperationalError("select 1", {}, Exception("connection refused")),
        TimeoutError("pool timeout"),
        DatabaseError(
            "select 1",
            {},
            asyncpg_exceptions.ConnectionFailureError("connection dropped"),
        ),
        asyncpg_exceptions.CannotConnectNowError("starting up"),
        asyncpg_exceptions.TransactionTimeoutError("transaction timeout"),
    ],
)
def test_availability_errors_become_persistence_unavailable_with_cause(
    db_error: Exception,
) -> None:
    session = FakeAsyncSession()
    session.commit_error = db_error

    async def commit_unavailable_uow() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)) as uow:
            await uow.commit()

    with pytest.raises(PersistenceUnavailable) as error:
        asyncio.run(commit_unavailable_uow())

    assert error.value.__cause__ is db_error
    assert session.rollbacks == 1
    assert session.closes == 1


def test_repository_boundaries_are_exposed_as_placeholders_until_adapters_exist():
    session = FakeAsyncSession()

    async def access_placeholder_repository() -> None:
        async with PostgresUnitOfWork(FakeSessionFactory(session)) as uow:
            assert uow.callers is uow.callers
            assert uow.calls is uow.calls
            assert uow.appointments is uow.appointments
            assert uow.evidence is uow.evidence
            assert uow.reference_data is uow.reference_data
            assert uow.calls is not session
            with pytest.raises(NotImplementedError, match="Task 6"):
                _ = uow.calls.add

    asyncio.run(access_placeholder_repository())
