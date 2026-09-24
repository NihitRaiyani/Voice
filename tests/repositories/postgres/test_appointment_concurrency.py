from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from roma.domain.persistence import (
    AppointmentRecord,
    AppointmentSlotRecord,
    PersistenceConflict,
)
from roma.repositories.postgres.models import Appointment, AppointmentSlot
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url

__all__ = ["alembic_config", "disposable_postgres_url"]

pytestmark = pytest.mark.postgres


@pytest.fixture()
def session_factory(alembic_config, disposable_postgres_url):
    assert disposable_postgres_url.startswith("postgresql+asyncpg://")
    command.upgrade(alembic_config, "head")
    engine = create_async_engine(disposable_postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    yield factory

    asyncio.run(engine.dispose())
    command.downgrade(alembic_config, "base")


def _now(offset: int = 0) -> datetime:
    return datetime(2026, 9, 21, 8, 0, tzinfo=UTC) + timedelta(minutes=offset)


async def _seed_branch_and_slot(session_factory) -> tuple[UUID, AppointmentSlotRecord]:
    async with PostgresUnitOfWork(session_factory) as uow:
        institute_id = await uow.reference_data.upsert_institute(
            code=f"weltec-{uuid4()}",
            name="Weltec Institute",
            is_active=True,
        )
        branch_id = await uow.reference_data.upsert_branch(
            institute_id=institute_id,
            code=f"ahm-{uuid4()}",
            name="Ahmedabad",
            city="Ahmedabad",
            timezone="Asia/Kolkata",
            is_active=True,
        )
        slot = await uow.appointments.add_slot(
            AppointmentSlotRecord(
                id=uuid4(),
                branch_id=branch_id,
                appointment_date=date(2026, 9, 22),
                start_time=time(10, 0),
                end_time=time(10, 30),
                capacity=1,
                status="available",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        await uow.commit()
        return branch_id, slot


async def _try_book(
    session_factory,
    *,
    branch_id: UUID,
    slot: AppointmentSlotRecord,
    attempt: int,
) -> AppointmentRecord:
    async with PostgresUnitOfWork(session_factory) as uow:
        appointment = await uow.appointments.book(
            AppointmentRecord(
                id=uuid4(),
                branch_id=branch_id,
                appointment_date=slot.appointment_date,
                start_time=slot.start_time,
                status="booked",
                created_at=_now(attempt),
                updated_at=_now(attempt),
            )
        )
        await uow.commit()
        return appointment


async def _count_active_appointments(
    session_factory,
    *,
    branch_id: UUID,
    slot: AppointmentSlotRecord,
) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.branch_id == branch_id,
                Appointment.appointment_date == slot.appointment_date,
                Appointment.start_time == slot.start_time,
                Appointment.status.in_(("booked", "confirmed")),
            )
        )
    return int(count or 0)


async def _slot_status(
    session_factory,
    *,
    branch_id: UUID,
    slot: AppointmentSlotRecord,
) -> str:
    async with session_factory() as session:
        status = await session.scalar(
            select(AppointmentSlot.status).where(
                AppointmentSlot.branch_id == branch_id,
                AppointmentSlot.appointment_date == slot.appointment_date,
                AppointmentSlot.start_time == slot.start_time,
            )
        )
    assert status is not None
    return status


def test_concurrent_booking_allows_exactly_one_active_appointment(session_factory):
    async def run() -> None:
        branch_id, slot = await _seed_branch_and_slot(session_factory)

        results = await asyncio.gather(
            *(
                _try_book(session_factory, branch_id=branch_id, slot=slot, attempt=attempt)
                for attempt in range(100)
            ),
            return_exceptions=True,
        )

        appointments = [result for result in results if isinstance(result, AppointmentRecord)]
        conflicts = [result for result in results if isinstance(result, PersistenceConflict)]
        unexpected = [
            result
            for result in results
            if not isinstance(result, AppointmentRecord | PersistenceConflict)
        ]

        assert unexpected == []
        assert len(appointments) == 1
        assert len(conflicts) == 99
        assert await _count_active_appointments(session_factory, branch_id=branch_id, slot=slot) == 1
        assert await _slot_status(session_factory, branch_id=branch_id, slot=slot) == "booked"

    asyncio.run(run())
