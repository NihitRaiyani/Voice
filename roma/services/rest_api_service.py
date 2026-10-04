"""Application service for Level 8 versioned REST resources."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.domain.persistence import (
    AppointmentRecord,
    CallRecord,
    PersistenceConflict,
    RecordNotFound,
)
from roma.repositories.postgres.models import (
    Appointment,
    Call,
    CallCost,
    Caller,
    SafetyEvent,
)
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from roma.schemas.rest_api import (
    AppointmentsQuery,
    CallsQuery,
    CreateAppointmentRequest,
    CreateCallRequest,
    LeadsQuery,
    SafetyEventsQuery,
    UpdateAppointmentRequest,
)

SessionFactory = async_sessionmaker[AsyncSession]


class RestApiServiceError(RuntimeError):
    """HTTP-facing service error with a stable API code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _date(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _time(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _page_meta(*, query: Any, total: int, filters: dict[str, object | None]) -> dict[str, object]:
    return {
        "limit": query.limit,
        "offset": query.offset,
        "total": total,
        "sort": query.sort,
        "filters": {key: value for key, value in filters.items() if value is not None},
    }


def _call_data(record: CallRecord) -> dict[str, object | None]:
    return {
        "id": str(record.id),
        "caller_id": _uuid(record.caller_id),
        "provider_call_id": record.provider_call_id,
        "direction": record.direction,
        "status": record.status,
        "language": record.language,
        "started_at": _dt(record.started_at),
        "answered_at": _dt(record.answered_at),
        "ended_at": _dt(record.ended_at),
        "final_stage": record.final_stage,
        "booking_status": record.booking_status,
        "total_cost": _decimal(record.total_cost),
        "currency": record.currency,
        "recording_url": record.recording_url,
        "created_at": _dt(record.created_at),
        "updated_at": _dt(record.updated_at),
    }


def _call_row_data(row: Call) -> dict[str, object | None]:
    return _call_data(
        CallRecord(
            id=row.id,
            caller_id=row.caller_id,
            provider_call_id=row.provider_call_id,
            direction=row.direction,
            status=row.status,
            language=row.language,
            started_at=row.started_at,
            answered_at=row.answered_at,
            ended_at=row.ended_at,
            final_stage=row.final_stage,
            booking_status=row.booking_status,
            total_cost=row.total_cost,
            currency=row.currency,
            recording_url=row.recording_url,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    )


def _appointment_data(record: AppointmentRecord) -> dict[str, object | None]:
    return {
        "id": str(record.id),
        "caller_id": _uuid(record.caller_id),
        "call_id": _uuid(record.call_id),
        "branch_id": str(record.branch_id),
        "appointment_date": _date(record.appointment_date),
        "start_time": _time(record.start_time),
        "counsellor_id": _uuid(record.counsellor_id),
        "course_id": _uuid(record.course_id),
        "status": record.status,
        "created_at": _dt(record.created_at),
        "updated_at": _dt(record.updated_at),
    }


def _appointment_row_data(row: Appointment) -> dict[str, object | None]:
    return {
        "id": str(row.id),
        "caller_id": _uuid(row.caller_id),
        "call_id": _uuid(row.call_id),
        "branch_id": str(row.branch_id),
        "appointment_date": _date(row.appointment_date),
        "start_time": _time(row.start_time),
        "counsellor_id": _uuid(row.counsellor_id),
        "course_id": _uuid(row.course_id),
        "status": row.status,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _lead_row_data(row: Caller) -> dict[str, object | None]:
    return {
        "id": str(row.id),
        "name": row.name,
        "preferred_language": row.preferred_language,
        "city": row.city,
        "education": row.education,
        "current_status": row.current_status,
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _safety_event_row_data(row: SafetyEvent) -> dict[str, object | None]:
    return {
        "id": str(row.id),
        "call_id": _uuid(row.call_id),
        "turn_id": _uuid(row.turn_id),
        "rule": row.rule,
        "original_category": row.original_category,
        "replacement_type": row.replacement_type,
        "metadata": row.metadata_,
        "created_at": _dt(row.created_at),
    }


async def _count(session: AsyncSession, statement: Select[tuple[Any]]) -> int:
    count_statement = select(func.count()).select_from(statement.subquery())
    value = await session.scalar(count_statement)
    return int(value or 0)


class RestApiService:
    """Coordinates short database work for versioned REST resources."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def create_call(self, payload: CreateCallRequest) -> dict[str, object]:
        now = _now()
        record = CallRecord(
            id=uuid4(),
            caller_id=payload.caller_id,
            provider_call_id=payload.provider_call_id,
            direction=payload.direction,
            status=payload.status,
            language=payload.language,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        try:
            async with PostgresUnitOfWork(self._session_factory) as uow:
                created = await uow.calls.add(record)
                await uow.commit()
        except PersistenceConflict as exc:
            raise RestApiServiceError(
                409, "CALL_CONFLICT", "Call record conflicts with existing data."
            ) from exc
        return _call_data(created)

    async def list_calls(self, query: CallsQuery) -> tuple[list[dict[str, object]], dict[str, object]]:
        statement = select(Call).where(Call.deleted_at.is_(None))
        if query.status is not None:
            statement = statement.where(Call.status == query.status)
        if query.language is not None:
            statement = statement.where(Call.language == query.language)
        total_statement = statement
        order = {
            "created_at_desc": Call.created_at.desc(),
            "created_at_asc": Call.created_at.asc(),
            "started_at_desc": Call.started_at.desc(),
            "started_at_asc": Call.started_at.asc(),
        }[query.sort]
        statement = statement.order_by(order, Call.id).limit(query.limit).offset(query.offset)
        async with self._session_factory() as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={"status": query.status, "language": query.language},
        )
        return [_call_row_data(row) for row in rows], meta

    async def get_call(self, call_id: UUID) -> dict[str, object]:
        async with PostgresUnitOfWork(self._session_factory) as uow:
            record = await uow.calls.get(call_id)
        if record is None or record.deleted_at is not None:
            raise RestApiServiceError(404, "CALL_NOT_FOUND", "Call was not found.")
        return _call_data(record)

    async def list_leads(self, query: LeadsQuery) -> tuple[list[dict[str, object]], dict[str, object]]:
        statement = select(Caller).where(Caller.anonymized_at.is_(None))
        if query.city is not None:
            statement = statement.where(Caller.city == query.city)
        if query.preferred_language is not None:
            statement = statement.where(Caller.preferred_language == query.preferred_language)
        total_statement = statement
        order = {
            "created_at_desc": Caller.created_at.desc(),
            "created_at_asc": Caller.created_at.asc(),
        }[query.sort]
        statement = statement.order_by(order, Caller.id).limit(query.limit).offset(query.offset)
        async with self._session_factory() as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={"city": query.city, "preferred_language": query.preferred_language},
        )
        return [_lead_row_data(row) for row in rows], meta

    async def create_appointment(
        self, payload: CreateAppointmentRequest
    ) -> dict[str, object | None]:
        now = _now()
        record = AppointmentRecord(
            id=uuid4(),
            caller_id=payload.caller_id,
            call_id=payload.call_id,
            branch_id=payload.branch_id,
            appointment_date=payload.appointment_date,
            start_time=payload.start_time,
            counsellor_id=payload.counsellor_id,
            course_id=payload.course_id,
            status=payload.status,
            created_at=now,
            updated_at=now,
        )
        try:
            async with PostgresUnitOfWork(self._session_factory) as uow:
                created = await uow.appointments.book(record)
                await uow.commit()
        except RecordNotFound as exc:
            raise RestApiServiceError(
                404, "APPOINTMENT_SLOT_NOT_FOUND", "The selected appointment slot was not found."
            ) from exc
        except PersistenceConflict as exc:
            raise RestApiServiceError(
                409,
                "APPOINTMENT_SLOT_UNAVAILABLE",
                "The selected appointment slot is unavailable.",
            ) from exc
        return _appointment_data(created)

    async def list_appointments(
        self, query: AppointmentsQuery
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        statement = select(Appointment).where(Appointment.anonymized_at.is_(None))
        if query.branch_id is not None:
            statement = statement.where(Appointment.branch_id == query.branch_id)
        if query.status is not None:
            statement = statement.where(Appointment.status == query.status)
        total_statement = statement
        order = {
            "date_asc": (Appointment.appointment_date.asc(), Appointment.start_time.asc()),
            "date_desc": (Appointment.appointment_date.desc(), Appointment.start_time.desc()),
            "created_at_desc": (Appointment.created_at.desc(),),
        }[query.sort]
        statement = statement.order_by(*order, Appointment.id).limit(query.limit).offset(query.offset)
        async with self._session_factory() as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={"branch_id": _uuid(query.branch_id), "status": query.status},
        )
        return [_appointment_row_data(row) for row in rows], meta

    async def update_appointment(
        self, appointment_id: UUID, payload: UpdateAppointmentRequest
    ) -> dict[str, object | None]:
        now = _now()
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.get(Appointment, appointment_id, with_for_update=True)
                    if row is None or row.anonymized_at is not None:
                        raise RestApiServiceError(
                            404, "APPOINTMENT_NOT_FOUND", "Appointment was not found."
                        )
                    row.status = payload.status
                    row.updated_at = now
                    if payload.counsellor_id is not None:
                        row.counsellor_id = payload.counsellor_id
                    if payload.course_id is not None:
                        row.course_id = payload.course_id
                    await session.flush()
                    await session.refresh(row)
            except IntegrityError as exc:
                raise RestApiServiceError(
                    409,
                    "APPOINTMENT_SLOT_UNAVAILABLE",
                    "The selected appointment slot is unavailable.",
                ) from exc
        return _appointment_row_data(row)

    async def cancel_appointment(self, appointment_id: UUID) -> dict[str, object | None]:
        payload = UpdateAppointmentRequest(status="cancelled")
        return await self.update_appointment(appointment_id, payload)

    async def analytics_overview(self) -> dict[str, object]:
        async with self._session_factory() as session:
            total_calls = await session.scalar(select(func.count()).select_from(Call))
            completed_calls = await session.scalar(
                select(func.count()).select_from(Call).where(Call.status == "completed")
            )
            active_appointments = await session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(Appointment.status.in_(("booked", "confirmed")))
            )
            safety_events = await session.scalar(select(func.count()).select_from(SafetyEvent))
            total_cost = await session.scalar(select(func.coalesce(func.sum(CallCost.amount), 0)))
        return {
            "calls": {
                "total": int(total_calls or 0),
                "completed": int(completed_calls or 0),
            },
            "appointments": {"active": int(active_appointments or 0)},
            "safety": {"events": int(safety_events or 0)},
            "costs": {"total": str(total_cost or Decimal("0")), "currency": "INR"},
        }

    async def list_safety_events(
        self, query: SafetyEventsQuery
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        statement = select(SafetyEvent).where(SafetyEvent.deleted_at.is_(None))
        if query.call_id is not None:
            statement = statement.where(SafetyEvent.call_id == query.call_id)
        if query.rule is not None:
            statement = statement.where(SafetyEvent.rule == query.rule)
        total_statement = statement
        order = {
            "created_at_desc": SafetyEvent.created_at.desc(),
            "created_at_asc": SafetyEvent.created_at.asc(),
        }[query.sort]
        statement = statement.order_by(order, SafetyEvent.id).limit(query.limit).offset(query.offset)
        async with self._session_factory() as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={"call_id": _uuid(query.call_id), "rule": query.rule},
        )
        return [_safety_event_row_data(row) for row in rows], meta


__all__ = ["RestApiService", "RestApiServiceError"]
