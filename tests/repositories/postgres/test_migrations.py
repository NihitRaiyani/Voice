"""Integration coverage for the Alembic PostgreSQL schema lifecycle."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from alembic import command
from alembic.config import Config
from roma.repositories.postgres.models import Base
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.postgres


APP_TABLES = set(Base.metadata.tables)


def _postgres_bin(name: str) -> str | None:
    configured = os.environ.get(f"POSTGRES_{name.upper()}_BIN")
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    bundled = Path(f"/Library/PostgreSQL/18/bin/{name}")
    return str(bundled) if bundled.exists() else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def disposable_postgres_url() -> Iterator[str]:
    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("PostgreSQL initdb/pg_ctl binaries are unavailable")

    with TemporaryDirectory(prefix="roma-pg-") as directory:
        data_dir = Path(directory) / "data"
        port = _free_port()
        subprocess.run(
            [
                initdb,
                "-D",
                str(data_dir),
                "--username=roma",
                "--auth=trust",
                "--no-instructions",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data_dir),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = f"postgresql+asyncpg://roma@127.0.0.1:{port}/postgres"
            yield url
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


@pytest.fixture()
def alembic_config(disposable_postgres_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_url)
    return config


def _constraint_names(table_name: str) -> set[str]:
    return {constraint.name for constraint in Base.metadata.tables[table_name].constraints}


def _render_constraint_name(sync_connection, name: str) -> str:
    return sync_connection.dialect.identifier_preparer.truncate_and_render_constraint_name(name)


def _render_index_name(sync_connection, name: str) -> str:
    return sync_connection.dialect.identifier_preparer.truncate_and_render_index_name(name)


async def _assert_schema_matches_metadata(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_assert_tables_constraints_and_indexes)
    finally:
        await engine.dispose()


def _assert_tables_constraints_and_indexes(sync_connection) -> None:
    inspector = inspect(sync_connection)
    assert set(inspector.get_table_names()) == APP_TABLES | {"alembic_version"}

    for table_name, table in Base.metadata.tables.items():
        pk = inspector.get_pk_constraint(table_name)
        assert pk["name"] == _render_constraint_name(sync_connection, table.primary_key.name)
        assert pk["constrained_columns"] == list(table.primary_key.columns.keys())

        unique_constraints = {
            tuple(constraint["column_names"]): constraint["name"]
            for constraint in inspector.get_unique_constraints(table_name)
        }
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                columns = tuple(constraint.columns.keys())
                assert unique_constraints[columns] == _render_constraint_name(
                    sync_connection, constraint.name
                )

        foreign_keys = {
            (
                tuple(constraint["constrained_columns"]),
                tuple(
                    f"{constraint['referred_table']}.{column}"
                    for column in constraint["referred_columns"]
                ),
            ): constraint
            for constraint in inspector.get_foreign_keys(table_name)
        }
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                columns = tuple(constraint.columns.keys())
                targets = tuple(element.target_fullname for element in constraint.elements)
                inspected = foreign_keys[(columns, targets)]
                assert inspected["name"] == _render_constraint_name(
                    sync_connection, constraint.name
                )
                assert inspected["options"].get("ondelete") == constraint.ondelete

        check_names = {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}
        expected_check_names = {
            _render_constraint_name(sync_connection, name)
            for name in _constraint_names(table_name)
            if name.startswith(f"ck_{table_name}_")
        }
        assert expected_check_names <= check_names

        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        for index in table.indexes:
            inspected = indexes[_render_index_name(sync_connection, index.name)]
            assert inspected["column_names"] == list(index.columns.keys())
            assert inspected["unique"] == index.unique

    active_indexes = {index["name"]: index for index in inspector.get_indexes("appointments")}
    active_index = active_indexes["uq_appointments_active_slot"]
    assert active_index["unique"]
    assert active_index["column_names"] == ["branch_id", "appointment_date", "start_time"]
    assert active_index["dialect_options"]["postgresql_where"] == (
        "((status)::text = ANY ((ARRAY['booked'::character varying, "
        "'confirmed'::character varying])::text[]))"
    )


async def _assert_application_tables_are_gone(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert not APP_TABLES & tables
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        await engine.dispose()


def test_initial_migration_upgrades_to_metadata_and_downgrades_to_base(
    alembic_config: Config, disposable_postgres_url: str
) -> None:
    command.upgrade(alembic_config, "head")

    asyncio.run(_assert_schema_matches_metadata(disposable_postgres_url))

    command.downgrade(alembic_config, "base")

    asyncio.run(_assert_application_tables_are_gone(disposable_postgres_url))


def test_alembic_has_no_pending_metadata_operations(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
