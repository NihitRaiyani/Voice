"""Short booking transactions and candidate reads; never perform Redis/provider IO here."""

from datetime import date
from uuid import UUID

from sqlalchemy import func, select

from roma.domain.appointments.booking import SlotKey
from roma.domain.persistence import AppointmentRecord, PersistenceConflict, RecordNotFound
from roma.repositories.postgres.models import Appointment, AppointmentSlot, Branch, Call
from roma.repositories.postgres.repositories import (
    _appointment_record,
    _call_record,
    _slot_record,
)
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork


class PostgresBookingStore:
    def __init__(self, session_factory) -> None:
        self._factory = session_factory

    async def branch_id(self, name: str) -> UUID | None:
        async with self._factory() as session:
            ids = list(
                await session.scalars(
                    select(Branch.id)
                    .where(
                        func.lower(Branch.name) == name.casefold(),
                        Branch.is_active.is_(True),
                        Branch.timezone.in_(("Asia/Kolkata", "Asia/Calcutta")),
                    )
                    .limit(2)
                )
            )
        return ids[0] if len(ids) == 1 else None

    async def call(self, *, call_id: UUID | None = None, sid: str | None = None):
        async with self._factory() as session:
            query = select(Call).where(Call.deleted_at.is_(None))
            query = (
                query.where(Call.id == call_id)
                if call_id
                else query.where(Call.provider_call_id == sid)
            )
            row = await session.scalar(query)
            return _call_record(row) if row else None

    async def active_for_call(self, call_id: UUID) -> AppointmentRecord | None:
        async with self._factory() as session:
            row = await session.scalar(
                select(Appointment)
                .where(
                    Appointment.call_id == call_id,
                    Appointment.status.in_(("booked", "confirmed")),
                )
                .order_by(Appointment.created_at)
                .limit(1)
            )
            return _appointment_record(row) if row else None

    async def get(self, appointment_id: UUID) -> AppointmentRecord | None:
        async with PostgresUnitOfWork(self._factory) as uow:
            return await uow.appointments.get(appointment_id)

    async def candidates(self, branch_id: UUID, start: date, end: date):
        active = (
            select(Appointment.id)
            .where(
                Appointment.branch_id == AppointmentSlot.branch_id,
                Appointment.appointment_date == AppointmentSlot.appointment_date,
                Appointment.start_time == AppointmentSlot.start_time,
                Appointment.status.in_(("booked", "confirmed")),
            )
            .exists()
        )
        async with self._factory() as session:
            rows = await session.scalars(
                select(AppointmentSlot)
                .join(Branch)
                .where(
                    AppointmentSlot.branch_id == branch_id,
                    Branch.is_active.is_(True),
                    Branch.timezone.in_(("Asia/Kolkata", "Asia/Calcutta")),
                    AppointmentSlot.appointment_date.between(start, end),
                    AppointmentSlot.status == "available",
                    ~active,
                )
                .order_by(AppointmentSlot.appointment_date, AppointmentSlot.start_time)
                .limit(100)
            )
            return tuple(_slot_record(row) for row in rows)

    async def available(self, key: SlotKey) -> bool:
        active = (
            select(Appointment.id)
            .where(
                Appointment.branch_id == key.branch_id,
                Appointment.appointment_date == key.appointment_date,
                Appointment.start_time == key.start_time,
                Appointment.status.in_(("booked", "confirmed")),
            )
            .exists()
        )
        async with self._factory() as session:
            return (
                await session.scalar(
                    select(AppointmentSlot.id)
                    .join(Branch)
                    .where(
                        AppointmentSlot.branch_id == key.branch_id,
                        AppointmentSlot.appointment_date == key.appointment_date,
                        AppointmentSlot.start_time == key.start_time,
                        AppointmentSlot.status == "available",
                        Branch.is_active.is_(True),
                        Branch.timezone.in_(("Asia/Kolkata", "Asia/Calcutta")),
                        ~active,
                    )
                    .limit(1)
                )
                is not None
            )

    async def reserve(self, record: AppointmentRecord) -> AppointmentRecord:
        from sqlalchemy.exc import IntegrityError

        from roma.repositories.postgres.repositories import AppointmentPostgresRepository

        async with self._factory() as session:
            try:
                async with session.begin():
                    branch = await session.scalar(
                        select(Branch)
                        .where(
                            Branch.id == record.branch_id,
                            Branch.is_active.is_(True),
                            Branch.timezone.in_(("Asia/Kolkata", "Asia/Calcutta")),
                        )
                        .with_for_update(read=True)
                    )
                    if branch is None:
                        raise RecordNotFound("active IST branch does not exist")
                    call = None
                    if record.call_id is not None:
                        call = await session.scalar(
                            select(Call).where(Call.id == record.call_id).with_for_update()
                        )
                        if call is None or call.deleted_at is not None:
                            raise RecordNotFound("call does not exist")
                        if record.caller_id is not None and record.caller_id != call.caller_id:
                            raise PersistenceConflict("caller does not match call")
                        existing = await session.scalar(
                            select(Appointment).where(
                                Appointment.call_id == record.call_id,
                                Appointment.status.in_(("booked", "confirmed")),
                            )
                        )
                        if existing is not None and existing.id != record.id:
                            raise PersistenceConflict("call already has an active appointment")
                    saved = await AppointmentPostgresRepository(session).book(record)
                    if call is not None:
                        call.booking_status, call.updated_at = saved.status, record.updated_at
                return saved
            except IntegrityError as exc:
                raise PersistenceConflict("booking conflicts with existing data") from exc

    async def update(self, appointment_id: UUID, **values) -> AppointmentRecord:
        async with PostgresUnitOfWork(self._factory) as uow:
            saved = await uow.appointments.update_status(appointment_id, **values)
            await uow.commit()
            return saved
