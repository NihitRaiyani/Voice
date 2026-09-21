"""PostgreSQL transaction boundary for durable repository adapters."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any

from asyncpg import exceptions as asyncpg_exceptions
from sqlalchemy.exc import (
    DatabaseError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    TimeoutError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from roma.domain.persistence import PersistenceConflict, PersistenceUnavailable

SessionFactory = Callable[[], AsyncSession]
AsyncpgAvailabilityError = (
    asyncpg_exceptions.CannotConnectNowError,
    asyncpg_exceptions.ClientCannotConnectError,
    asyncpg_exceptions.ConnectionDoesNotExistError,
    asyncpg_exceptions.ConnectionFailureError,
    asyncpg_exceptions.ConnectionRejectionError,
    asyncpg_exceptions.FDWUnableToEstablishConnectionError,
    asyncpg_exceptions.IdleInTransactionSessionTimeoutError,
    asyncpg_exceptions.IdleSessionTimeoutError,
    asyncpg_exceptions.PostgresConnectionError,
    asyncpg_exceptions.TooManyConnectionsError,
    asyncpg_exceptions.TransactionTimeoutError,
)
SqlAlchemyAvailabilityError = (
    TimeoutError,
    OperationalError,
    InterfaceError,
    DisconnectionError,
)


class _Task6RepositoryPlaceholder:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, method_name: str) -> Any:
        raise NotImplementedError(
            f"PostgreSQL {self._name} repository adapters arrive in Task 6"
        )


class PostgresUnitOfWork:
    """Owns one async database session for one durable use case."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False
        self._rolled_back = False
        self._closed = False
        self._callers: object | None = None
        self._calls: object | None = None
        self._appointments: object | None = None
        self._evidence: object | None = None
        self._reference_data: object | None = None

    @property
    def callers(self) -> object:
        return self._require_repository(self._callers, "callers")

    @property
    def calls(self) -> object:
        return self._require_repository(self._calls, "calls")

    @property
    def appointments(self) -> object:
        return self._require_repository(self._appointments, "appointments")

    @property
    def evidence(self) -> object:
        return self._require_repository(self._evidence, "evidence")

    @property
    def reference_data(self) -> object:
        return self._require_repository(self._reference_data, "reference data")

    async def __aenter__(self) -> PostgresUnitOfWork:
        self._committed = False
        self._rolled_back = False
        self._closed = False
        self._session = self._session_factory()
        self._callers = _Task6RepositoryPlaceholder("callers")
        self._calls = _Task6RepositoryPlaceholder("calls")
        self._appointments = _Task6RepositoryPlaceholder("appointments")
        self._evidence = _Task6RepositoryPlaceholder("evidence")
        self._reference_data = _Task6RepositoryPlaceholder("reference data")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            if exc is None:
                if not self._committed and not self._rolled_back:
                    await self.rollback()
            elif not self._rolled_back:
                await self.rollback()
        except BaseException as rollback_error:
            translated = self._translate(rollback_error)
            if translated is rollback_error:
                raise
            raise translated from rollback_error
        finally:
            await self._close_once()

        if exc is not None:
            translated = self._translate(exc)
            if translated is exc:
                return None
            raise translated from exc
        return None

    async def commit(self) -> None:
        session = self._require_session()
        try:
            await session.commit()
        except BaseException as exc:
            await self._rollback_after_failed_commit()
            translated = self._translate(exc)
            if translated is exc:
                raise
            raise translated from exc
        self._committed = True

    async def rollback(self) -> None:
        session = self._require_session()
        await session.rollback()
        self._rolled_back = True

    def _require_repository(self, repository: object | None, name: str) -> object:
        if repository is None:
            raise RuntimeError(f"PostgreSQL {name} repository requested outside a unit of work")
        return repository

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("PostgreSQL unit of work is not active")
        return self._session

    async def _rollback_after_failed_commit(self) -> None:
        if self._rolled_back:
            return
        try:
            await self.rollback()
        except BaseException:
            self._rolled_back = True
            raise

    async def _close_once(self) -> None:
        if self._closed or self._session is None:
            return
        await self._session.close()
        self._closed = True

    def _translate(self, exc: BaseException) -> BaseException:
        if isinstance(exc, (PersistenceConflict, PersistenceUnavailable)):
            return exc
        if isinstance(exc, IntegrityError):
            return PersistenceConflict("durable write conflicts with existing data")
        if isinstance(exc, SqlAlchemyAvailabilityError + AsyncpgAvailabilityError):
            return PersistenceUnavailable("durable storage is unavailable")
        if isinstance(exc, DatabaseError) and isinstance(exc.orig, AsyncpgAvailabilityError):
            return PersistenceUnavailable("durable storage is unavailable")
        return exc


__all__ = ["PostgresUnitOfWork"]
