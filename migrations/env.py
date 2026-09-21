"""Async Alembic environment for Roma's PostgreSQL system of record."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from roma.core.config import get_settings
from roma.repositories.postgres.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configured_database_url() -> str:
    injected = config.get_main_option("sqlalchemy.url")
    if injected:
        return _asyncpg_url(injected)

    from_environment = os.environ.get("DATABASE_URL")
    if from_environment:
        return _asyncpg_url(from_environment)

    from_settings = get_settings().database_url.get_secret_value()
    if from_settings:
        return _asyncpg_url(from_settings)

    raise RuntimeError("DATABASE_URL is required for Alembic migrations")


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_configured_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _configured_database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
