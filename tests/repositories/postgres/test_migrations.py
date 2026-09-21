"""Integration coverage for the Alembic PostgreSQL schema lifecycle."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from roma.repositories.postgres.models import Base
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.exceptions import ContainerStartException

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


@contextmanager
def _testcontainer_postgres_url() -> Iterator[str]:
    image = os.environ.get("POSTGRES_TEST_IMAGE", "postgres:18")
    container = PostgresContainer(
        image=image,
        username="roma",
        password="roma",
        dbname="roma",
        driver="asyncpg",
    )
    with container:
        yield container.get_connection_url()


@contextmanager
def _local_postgres_url() -> Iterator[str]:
    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    if not initdb or not pg_ctl:
        raise RuntimeError("PostgreSQL initdb/pg_ctl binaries are unavailable")

    with TemporaryDirectory(prefix="roma-pg-") as directory:
        data_dir = Path(directory) / "data"
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
        started_port: int | None = None
        for _ in range(5):
            port = _free_port()
            startup = subprocess.run(
                [
                    pg_ctl,
                    "-D",
                    str(data_dir),
                    "-o",
                    f"-h 127.0.0.1 -p {port}",
                    "-w",
                    "start",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if startup.returncode == 0:
                started_port = port
                break

        if started_port is None:
            raise RuntimeError("Could not start local disposable PostgreSQL")

        try:
            url = f"postgresql+asyncpg://roma@127.0.0.1:{started_port}/postgres"
            yield url
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


@pytest.fixture(scope="session")
def disposable_postgres_url() -> Iterator[str]:
    testcontainers_error: DockerException | ContainerStartException | None = None
    try:
        with _testcontainer_postgres_url() as url:
            yield url
            return
    except (DockerException, ContainerStartException) as error:
        testcontainers_error = error

    try:
        with _local_postgres_url() as url:
            yield url
            return
    except RuntimeError as error:
        pytest.skip(
            "Testcontainers PostgreSQL unavailable "
            f"({testcontainers_error!r}); local PostgreSQL fallback unavailable ({error!r})"
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


async def _assert_application_tables_are_gone(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert not APP_TABLES & tables
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        await engine.dispose()


async def _assert_active_appointment_index_behavior(database_url: str) -> None:
    engine = create_async_engine(database_url)
    branch_id = uuid4()
    institute_id = uuid4()
    booked_appointment_id = uuid4()
    slot_id = uuid4()
    slot_date = date(2026, 9, 21)
    slot_start = time(10, 0)
    slot_end = time(10, 30)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO institutes (id, code, name) "
                    "VALUES (:id, :code, :name)"
                ),
                {"id": institute_id, "code": "weltec", "name": "Weltec"},
            )
            await connection.execute(
                text(
                    "INSERT INTO branches (id, institute_id, code, name, city, timezone) "
                    "VALUES (:id, :institute_id, :code, :name, :city, :timezone)"
                ),
                {
                    "id": branch_id,
                    "institute_id": institute_id,
                    "code": "ahm",
                    "name": "Ahmedabad",
                    "city": "Ahmedabad",
                    "timezone": "Asia/Kolkata",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO appointment_slots "
                    "(id, branch_id, appointment_date, start_time, end_time, capacity, status) "
                    "VALUES (:id, :branch_id, :appointment_date, :start_time, :end_time, "
                    ":capacity, :status)"
                ),
                {
                    "id": slot_id,
                    "branch_id": branch_id,
                    "appointment_date": slot_date,
                    "start_time": slot_start,
                    "end_time": slot_end,
                    "capacity": 2,
                    "status": "available",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO appointments "
                    "(id, branch_id, appointment_date, start_time, status) "
                    "VALUES (:id, :branch_id, :appointment_date, :start_time, :status)"
                ),
                {
                    "id": booked_appointment_id,
                    "branch_id": branch_id,
                    "appointment_date": slot_date,
                    "start_time": slot_start,
                    "status": "booked",
                },
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO appointments "
                        "(id, branch_id, appointment_date, start_time, status) "
                        "VALUES (:id, :branch_id, :appointment_date, :start_time, :status)"
                    ),
                    {
                        "id": uuid4(),
                        "branch_id": branch_id,
                        "appointment_date": slot_date,
                        "start_time": slot_start,
                        "status": "confirmed",
                    },
                )

        async with engine.begin() as connection:
            for status in ["cancelled", "no_show"]:
                await connection.execute(
                    text(
                        "INSERT INTO appointments "
                        "(id, branch_id, appointment_date, start_time, status) "
                        "VALUES (:id, :branch_id, :appointment_date, :start_time, :status)"
                    ),
                    {
                        "id": uuid4(),
                        "branch_id": branch_id,
                        "appointment_date": slot_date,
                        "start_time": slot_start,
                        "status": status,
                    },
                )
    finally:
        await engine.dispose()


def test_initial_migration_upgrades_to_metadata_and_downgrades_to_base(
    alembic_config: Config, disposable_postgres_url: str
) -> None:
    command.upgrade(alembic_config, "head")

    asyncio.run(_assert_schema_matches_metadata(disposable_postgres_url))
    asyncio.run(_assert_active_appointment_index_behavior(disposable_postgres_url))

    command.downgrade(alembic_config, "base")

    asyncio.run(_assert_application_tables_are_gone(disposable_postgres_url))


def test_alembic_has_no_pending_metadata_operations(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
