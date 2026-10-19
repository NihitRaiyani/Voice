"""Application service for Level 8 versioned REST resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.domain.appointments.booking import BookingError, HoldOwner, SlotKey
from roma.domain.persistence import (
    AppointmentRecord,
    CallRecord,
    PersistenceConflict,
    PersistenceUnavailable,
)
from roma.repositories.postgres.booking import PostgresBookingStore
from roma.repositories.postgres.models import (
    Appointment,
    Call,
    CallCost,
    Caller,
    SafetyEvent,
)
from roma.repositories.postgres.unit_of_work import (
    AsyncpgAvailabilityError,
    PostgresUnitOfWork,
)
from roma.schemas.rest_api import (
    AppointmentsQuery,
    CallsQuery,
    CreateAppointmentRequest,
    CreateCallRequest,
    LeadsQuery,
    SafetyEventsQuery,
    SafetyStatsQuery,
    UpdateAppointmentRequest,
)
from roma.services.booking_service import BookingService

SessionFactory = async_sessionmaker[AsyncSession]


class RestApiServiceError(RuntimeError):
    """HTTP-facing service error with a stable API code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@asynccontextmanager
async def _database_errors():
    try:
        yield
    except (
        SQLAlchemyError,
        PersistenceUnavailable,
        OSError,
        TimeoutError,
        *AsyncpgAvailabilityError,
    ) as exc:
        raise RestApiServiceError(
            503, "DATABASE_UNAVAILABLE", "Database is temporarily unavailable."
        ) from exc


@asynccontextmanager
async def _read_session(factory):
    async with _database_errors(), factory() as session:
        yield session


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


def _page_meta(
    *, query: Any, total: int, filters: dict[str, object | None]
) -> dict[str, object]:
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
        "metadata": {
            key: value
            for key, value in row.metadata_.items()
            if key
            in {
                "policy_version",
                "replacement_used",
                "latency_ms",
                "turn_number",
                "stage",
                "source",
            }
        },
        "rule_id": row.rule,
        "category": row.original_category,
        "replacement_used": row.metadata_.get("replacement_used"),
        "detected_at": _dt(row.created_at),
        "latency_ms": row.metadata_.get("latency_ms"),
        "created_at": _dt(row.created_at),
    }


async def _count(session: AsyncSession, statement: Select[tuple[Any]]) -> int:
    count_statement = select(func.count()).select_from(statement.subquery())
    value = await session.scalar(count_statement)
    return int(value or 0)


class RestApiService:
    """Coordinates short database work for versioned REST resources."""

    def __init__(self, session_factory: SessionFactory, booking_service=None) -> None:
        self._session_factory = session_factory
        self.booking = booking_service or BookingService(PostgresBookingStore(session_factory))

    @staticmethod
    def booking_error(error: BookingError) -> RestApiServiceError:
        if error.code == "APPOINTMENT_NOT_FOUND":
            return RestApiServiceError(
                404, error.code, "Call, appointment or configured slot was not found."
            )
        if error.code == "APPOINTMENT_SLOT_UNAVAILABLE":
            return RestApiServiceError(
                409, error.code, "The selected appointment slot is unavailable."
            )
        return RestApiServiceError(503, error.code, "Booking is temporarily unavailable.")

    async def appointment_slots(self, branch_id, on=None):
        try:
            records = await self.booking.candidates(branch_id, on=on)
            out = []
            for r in records:
                key = SlotKey(r.branch_id, r.appointment_date, r.start_time)
                if await self.booking.held(key):
                    continue
                out.append(
                    {
                        "id": str(r.id),
                        "branch_id": str(r.branch_id),
                        "appointment_date": r.appointment_date.isoformat(),
                        "start_time": r.start_time.isoformat(),
                    }
                )
            return out
        except BookingError as exc:
            raise self.booking_error(exc) from exc

    async def hold_appointment(self, payload):
        key = SlotKey(payload.branch_id, payload.appointment_date, payload.start_time)
        try:
            return await self.booking.hold(key, payload.call_id, payload.hold_token)
        except BookingError as exc:
            raise self.booking_error(exc) from exc

    async def release_appointment_hold(self, payload):
        key = SlotKey(payload.branch_id, payload.appointment_date, payload.start_time)
        owner = HoldOwner(payload.call_id, payload.hold_token)
        return {"released": await self.booking.release(key, owner)}

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
            async with _database_errors(), PostgresUnitOfWork(self._session_factory) as uow:
                created = await uow.calls.add(record)
                await uow.commit()
        except PersistenceConflict as exc:
            raise RestApiServiceError(
                409, "CALL_CONFLICT", "Call record conflicts with existing data."
            ) from exc
        return _call_data(created)

    async def list_calls(
        self, query: CallsQuery
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
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
        async with _read_session(self._session_factory) as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={"status": query.status, "language": query.language},
        )
        return [_call_row_data(row) for row in rows], meta

    async def get_call(self, call_id: UUID) -> dict[str, object]:
        async with _database_errors(), PostgresUnitOfWork(self._session_factory) as uow:
            record = await uow.calls.get(call_id)
        if record is None or record.deleted_at is not None:
            raise RestApiServiceError(404, "CALL_NOT_FOUND", "Call was not found.")
        return _call_data(record)

    async def list_leads(
        self, query: LeadsQuery
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
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
        async with _read_session(self._session_factory) as session:
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
            id=uuid5(NAMESPACE_URL, f"roma:appointment:{payload.idempotency_key}")
            if payload.idempotency_key
            else uuid4(),
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
        owner = (
            HoldOwner(payload.call_id, payload.hold_token)
            if payload.call_id and payload.hold_token
            else None
        )
        if payload.hold_token and not payload.call_id:
            raise RestApiServiceError(422, "VALIDATION_ERROR", "A hold token requires call_id.")
        try:
            created = await self.booking.confirm(record, owner=owner)
        except BookingError as exc:
            raise self.booking_error(exc) from exc
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
        statement = (
            statement.order_by(*order, Appointment.id).limit(query.limit).offset(query.offset)
        )
        async with _read_session(self._session_factory) as session:
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
        try:
            saved = await self.booking.update(
                appointment_id,
                status=payload.status,
                now=_now(),
                counsellor_id=payload.counsellor_id,
                course_id=payload.course_id,
            )
        except BookingError as exc:
            raise self.booking_error(exc) from exc
        return _appointment_data(saved)

    async def cancel_appointment(self, appointment_id: UUID) -> dict[str, object | None]:
        payload = UpdateAppointmentRequest(status="cancelled")
        return await self.update_appointment(appointment_id, payload)

    async def analytics_overview(self) -> dict[str, object]:
        async with _read_session(self._session_factory) as session:
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
            total_cost = await session.scalar(
                select(func.coalesce(func.sum(CallCost.amount), 0))
            )
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
        if query.category is not None:
            statement = statement.where(SafetyEvent.original_category == query.category)
        total_statement = statement
        order = {
            "created_at_desc": SafetyEvent.created_at.desc(),
            "created_at_asc": SafetyEvent.created_at.asc(),
        }[query.sort]
        statement = (
            statement.order_by(order, SafetyEvent.id).limit(query.limit).offset(query.offset)
        )
        async with _read_session(self._session_factory) as session:
            total = await _count(session, total_statement)
            rows = tuple(await session.scalars(statement))
        meta = _page_meta(
            query=query,
            total=total,
            filters={
                "call_id": _uuid(query.call_id),
                "rule": query.rule,
                "category": query.category,
            },
        )
        return [_safety_event_row_data(row) for row in rows], meta

    async def list_call_safety_events(self, call_id: UUID, query: SafetyEventsQuery):
        async with _read_session(self._session_factory) as session:
            existing = await session.scalar(
                select(Call.id).where(Call.id == call_id, Call.deleted_at.is_(None))
            )
        if existing is None:
            raise RestApiServiceError(404, "CALL_NOT_FOUND", "Call was not found.")
        return await self.list_safety_events(query.model_copy(update={"call_id": call_id}))

    async def safety_stats(self, query: SafetyStatsQuery) -> dict[str, object]:
        statement = select(SafetyEvent.original_category, SafetyEvent.rule, func.count()).where(
            SafetyEvent.deleted_at.is_(None)
        )
        if query.call_id is not None:
            statement = statement.where(SafetyEvent.call_id == query.call_id)
        if query.rule is not None:
            statement = statement.where(SafetyEvent.rule == query.rule)
        if query.category is not None:
            statement = statement.where(SafetyEvent.original_category == query.category)
        statement = statement.group_by(SafetyEvent.original_category, SafetyEvent.rule)
        async with _read_session(self._session_factory) as session:
            rows = (await session.execute(statement)).all()
        categories: dict[str, int] = {}
        rules: dict[str, int] = {}
        total = 0
        for category, rule, count in rows:
            total += count
            categories[category or "UNKNOWN"] = categories.get(category or "UNKNOWN", 0) + count
            rules[rule or "UNKNOWN"] = rules.get(rule or "UNKNOWN", 0) + count
        return {"total": total, "by_category": categories, "by_rule": rules}


__all__ = ["RestApiService", "RestApiServiceError"]
