"""Async SQLAlchemy database lifecycle for Roma's durable PostgreSQL store."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine, # engine type
    AsyncSession, # factory that CREATES sessions
    async_sessionmaker, # actual session used for DB work
    create_async_engine, # function that CREATES engine
)

from roma.core.config import Settings


@dataclass(repr=False)
class Database:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        engine = create_async_engine(
            settings.database_url.get_secret_value(),
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_secs,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(engine=engine, session_factory=session_factory)

    def __repr__(self) -> str:
        return "Database(engine=<AsyncEngine>, session_factory=<async_sessionmaker>)"

    async def close(self) -> None:
        await self.engine.dispose()
