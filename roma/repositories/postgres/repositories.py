"""SQLAlchemy-backed durable repository adapters for PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from roma.domain.persistence import (
    AppointmentRecord,
    AppointmentSlotRecord,
    AuditLogRecord,
    BranchRecord,
    CallCostRecord,
    CallerRecord,
    CallEventRecord,
    CallRecord,
    CallTurnRecord,
    CounsellorRecord,
    CourseRecord,
    FollowupJobRecord,
    InstituteRecord,
    PersistenceConflict,
    ProviderUsageRecord,
    RecordingRecord,
    RecordNotFound,
    RoleRecord,
    SafetyEventRecord,
    UserRecord,
)

from .models import (
    Appointment,
    AppointmentSlot,
    AuditLog,
    Branch,
    BranchCourse,
    Call,
    CallCost,
    Caller,
    CallEvent,
    CallTurn,
    Counsellor,
    Course,
    FollowupJob,
    Institute,
    ProviderUsage,
    Recording,
    Role,
    SafetyEvent,
    User,
    UserRole,
)


def _json(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _json(value)
    if isinstance(value, (list, tuple, frozenset)):
        return [_json_value(item) for item in value]
    return value


async def _flush_or_conflict(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        raise PersistenceConflict("durable write conflicts with existing data") from exc


async def _execute_or_conflict(session: AsyncSession, statement: Any) -> Any:
    try:
        return await session.execute(statement)
    except IntegrityError as exc:
        raise PersistenceConflict("durable write conflicts with existing data") from exc


async def _scalar_or_conflict(session: AsyncSession, statement: Any) -> Any:
    result = await _execute_or_conflict(session, statement)
    return result.scalar_one()


def _caller_record(row: Caller) -> CallerRecord:
    return CallerRecord(
        id=row.id,
        phone_hash=row.phone_hash,
        name=row.name,
        preferred_language=row.preferred_language,
        city=row.city,
        education=row.education,
        current_status=row.current_status,
        anonymized_at=row.anonymized_at,
        retention_until=row.retention_until,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _call_record(row: Call) -> CallRecord:
    return CallRecord(
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
        retention_until=row.retention_until,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _turn_record(row: CallTurn) -> CallTurnRecord:
    return CallTurnRecord(
        id=row.id,
        call_id=row.call_id,
        turn_number=row.turn_number,
        speaker=row.speaker,
        transcript=row.transcript,
        conversation_stage=row.conversation_stage,
        route=row.route,
        latency_ms=row.latency_ms,
        retention_until=row.retention_until,
        transcript_deleted_at=row.transcript_deleted_at,
        created_at=row.created_at,
    )


def _event_record(row: CallEvent) -> CallEventRecord:
    return CallEventRecord(
        id=row.id,
        call_id=row.call_id,
        event_type=row.event_type,
        conversation_stage=row.conversation_stage,
        payload=row.payload,
        occurred_at=row.occurred_at,
        idempotency_key=row.idempotency_key,
    )


def _slot_record(row: AppointmentSlot) -> AppointmentSlotRecord:
    return AppointmentSlotRecord(
        id=row.id,
        branch_id=row.branch_id,
        appointment_date=row.appointment_date,
        start_time=row.start_time,
        end_time=row.end_time,
        capacity=row.capacity,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _appointment_record(row: Appointment) -> AppointmentRecord:
    return AppointmentRecord(
        id=row.id,
        caller_id=row.caller_id,
        call_id=row.call_id,
        branch_id=row.branch_id,
        appointment_date=row.appointment_date,
        start_time=row.start_time,
        counsellor_id=row.counsellor_id,
        course_id=row.course_id,
        status=row.status,
        retention_until=row.retention_until,
        anonymized_at=row.anonymized_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _safety_event_record(row: SafetyEvent) -> SafetyEventRecord:
    return SafetyEventRecord(
        id=row.id,
        call_id=row.call_id,
        turn_id=row.turn_id,
        rule=row.rule,
        original_category=row.original_category,
        replacement_type=row.replacement_type,
        metadata=row.metadata_,
        retention_until=row.retention_until,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def _provider_usage_record(row: ProviderUsage) -> ProviderUsageRecord:
    return ProviderUsageRecord(
        id=row.id,
        call_id=row.call_id,
        turn_id=row.turn_id,
        provider=row.provider,
        service=row.service,
        model=row.model,
        measured_units=row.measured_units,
        unit_name=row.unit_name,
        provider_metadata=row.provider_metadata,
        occurred_at=row.occurred_at,
        idempotency_key=row.idempotency_key,
        retention_until=row.retention_until,
    )


def _call_cost_record(row: CallCost) -> CallCostRecord:
    return CallCostRecord(
        id=row.id,
        call_id=row.call_id,
        provider_usage_id=row.provider_usage_id,
        provider=row.provider,
        service=row.service,
        model=row.model,
        quantity=row.quantity,
        unit=row.unit,
        unit_price=row.unit_price,
        amount=row.amount,
        currency=row.currency,
        pricing_version=row.pricing_version,
        metadata=row.metadata_,
        occurred_at=row.occurred_at,
        idempotency_key=row.idempotency_key,
        retention_until=row.retention_until,
    )


def _recording_record(row: Recording) -> RecordingRecord:
    return RecordingRecord(
        id=row.id,
        call_id=row.call_id,
        storage_provider=row.storage_provider,
        object_key=row.object_key,
        media_type=row.media_type,
        duration_ms=row.duration_ms,
        size_bytes=row.size_bytes,
        checksum=row.checksum,
        status=row.status,
        consent_at=row.consent_at,
        retention_until=row.retention_until,
        deleted_at=row.deleted_at,
        deletion_metadata=row.deletion_metadata,
        created_at=row.created_at,
    )


def _followup_job_record(row: FollowupJob) -> FollowupJobRecord:
    return FollowupJobRecord(
        id=row.id,
        call_id=row.call_id,
        appointment_id=row.appointment_id,
        job_type=row.job_type,
        payload=row.payload,
        status=row.status,
        attempts=row.attempts,
        available_at=row.available_at,
        lock_owner=row.lock_owner,
        locked_at=row.locked_at,
        last_error=row.last_error,
        idempotency_key=row.idempotency_key,
        retention_until=row.retention_until,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit_log_record(row: AuditLog) -> AuditLogRecord:
    return AuditLogRecord(
        id=row.id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        metadata=row.metadata_,
        occurred_at=row.occurred_at,
        retention_until=row.retention_until,
    )


def _institute_record(row: Institute) -> InstituteRecord:
    return InstituteRecord(
        id=row.id,
        code=row.code,
        name=row.name,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _branch_record(row: Branch) -> BranchRecord:
    return BranchRecord(
        id=row.id,
        institute_id=row.institute_id,
        code=row.code,
        name=row.name,
        city=row.city,
        timezone=row.timezone,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _course_record(row: Course) -> CourseRecord:
    return CourseRecord(
        id=row.id,
        institute_id=row.institute_id,
        code=row.code,
        name=row.name,
        description=row.description,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _role_record(row: Role) -> RoleRecord:
    return RoleRecord(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _user_record(row: User) -> UserRecord:
    return UserRecord(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        password_hash=row.password_hash,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _counsellor_record(row: Counsellor) -> CounsellorRecord:
    return CounsellorRecord(
        id=row.id,
        branch_id=row.branch_id,
        user_id=row.user_id,
        employee_code=row.employee_code,
        display_name=row.display_name,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CallerPostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: CallerRecord) -> CallerRecord:
        values = {
            "id": record.id,
            "phone_hash": record.phone_hash,
            "name": record.name,
            "preferred_language": record.preferred_language,
            "city": record.city,
            "education": record.education,
            "current_status": record.current_status,
            "anonymized_at": record.anonymized_at,
            "retention_until": record.retention_until,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        statement = (
            insert(Caller)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Caller.phone_hash],
                set_={
                    "name": record.name,
                    "preferred_language": record.preferred_language,
                    "city": record.city,
                    "education": record.education,
                    "current_status": record.current_status,
                    "anonymized_at": record.anonymized_at,
                    "retention_until": record.retention_until,
                    "updated_at": record.updated_at,
                },
            )
            .returning(Caller.id)
        )
        caller_id = await _scalar_or_conflict(self._session, statement)
        row = await self._session.get(Caller, caller_id)
        if row is None:
            raise RecordNotFound("caller was not persisted")
        return _caller_record(row)

    async def get(self, caller_id: UUID) -> CallerRecord | None:
        row = await self._session.get(Caller, caller_id)
        return None if row is None else _caller_record(row)

    async def get_by_phone_hash(self, phone_hash: str) -> CallerRecord | None:
        row = await self._session.scalar(select(Caller).where(Caller.phone_hash == phone_hash))
        return None if row is None else _caller_record(row)


class CallPostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: CallRecord) -> CallRecord:
        row = Call(
            id=record.id,
            caller_id=record.caller_id,
            provider_call_id=record.provider_call_id,
            direction=record.direction,
            status=record.status,
            language=record.language,
            started_at=record.started_at,
            answered_at=record.answered_at,
            ended_at=record.ended_at,
            final_stage=record.final_stage,
            booking_status=record.booking_status,
            total_cost=record.total_cost,
            currency=record.currency,
            recording_url=record.recording_url,
            retention_until=record.retention_until,
            deleted_at=record.deleted_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _call_record(row)

    async def get(self, call_id: UUID) -> CallRecord | None:
        row = await self._session.get(Call, call_id)
        return None if row is None else _call_record(row)

    async def set_provider_call_id(self, call_id: UUID, provider_call_id: str) -> CallRecord:
        row = await self._lock_call(call_id)
        row.provider_call_id = provider_call_id
        await _flush_or_conflict(self._session)
        await self._session.refresh(row)
        return _call_record(row)

    async def update_lifecycle(
        self,
        call_id: UUID,
        *,
        status: str,
        occurred_at: datetime,
        final_stage: str | None = None,
        booking_status: str | None = None,
    ) -> CallRecord:
        row = await self._lock_call(call_id)
        row.status = status
        row.updated_at = occurred_at
        if status == "in_progress":
            row.answered_at = occurred_at
        if status in {"completed", "failed", "cancelled", "no_answer", "busy"}:
            row.ended_at = occurred_at
        if final_stage is not None:
            row.final_stage = final_stage
        if booking_status is not None:
            row.booking_status = booking_status
        await _flush_or_conflict(self._session)
        await self._session.refresh(row)
        return _call_record(row)

    async def add_turn(self, record: CallTurnRecord) -> CallTurnRecord:
        row = CallTurn(
            id=record.id,
            call_id=record.call_id,
            turn_number=record.turn_number,
            speaker=record.speaker,
            transcript=record.transcript,
            conversation_stage=record.conversation_stage,
            route=record.route,
            latency_ms=record.latency_ms,
            retention_until=record.retention_until,
            transcript_deleted_at=record.transcript_deleted_at,
            created_at=record.created_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _turn_record(row)

    async def list_turns(self, call_id: UUID) -> tuple[CallTurnRecord, ...]:
        rows = await self._session.scalars(
            select(CallTurn)
            .where(CallTurn.call_id == call_id)
            .order_by(CallTurn.turn_number, CallTurn.created_at)
        )
        return tuple(_turn_record(row) for row in rows)

    async def add_event(self, record: CallEventRecord) -> CallEventRecord:
        row = CallEvent(
            id=record.id,
            call_id=record.call_id,
            event_type=record.event_type,
            conversation_stage=record.conversation_stage,
            payload=_json(record.payload),
            occurred_at=record.occurred_at,
            idempotency_key=record.idempotency_key,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _event_record(row)

    async def _lock_call(self, call_id: UUID) -> Call:
        row = await self._session.scalar(select(Call).where(Call.id == call_id).with_for_update())
        if row is None:
            raise RecordNotFound("call does not exist")
        return row


class AppointmentPostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_slot(self, record: AppointmentSlotRecord) -> AppointmentSlotRecord:
        row = AppointmentSlot(
            id=record.id,
            branch_id=record.branch_id,
            appointment_date=record.appointment_date,
            start_time=record.start_time,
            end_time=record.end_time,
            capacity=record.capacity,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _slot_record(row)

    async def list_available_slots(
        self, branch_id: UUID, appointment_date: date
    ) -> tuple[AppointmentSlotRecord, ...]:
        active_booking = (
            select(Appointment.id)
            .where(
                Appointment.branch_id == AppointmentSlot.branch_id,
                Appointment.appointment_date == AppointmentSlot.appointment_date,
                Appointment.start_time == AppointmentSlot.start_time,
                Appointment.status.in_(("booked", "confirmed")),
            )
            .exists()
        )
        rows = await self._session.scalars(
            select(AppointmentSlot)
            .where(
                AppointmentSlot.branch_id == branch_id,
                AppointmentSlot.appointment_date == appointment_date,
                AppointmentSlot.status == "available",
                ~active_booking,
            )
            .order_by(AppointmentSlot.status, AppointmentSlot.start_time)
        )
        return tuple(_slot_record(row) for row in rows)

    async def book(self, record: AppointmentRecord) -> AppointmentRecord:
        if record.status not in {"booked", "confirmed"}:
            raise PersistenceConflict("appointment booking must use an active status")

        slot = await self._session.scalar(
            select(AppointmentSlot)
            .where(
                AppointmentSlot.branch_id == record.branch_id,
                AppointmentSlot.appointment_date == record.appointment_date,
                AppointmentSlot.start_time == record.start_time,
            )
            .with_for_update()
        )
        if slot is None:
            raise RecordNotFound("appointment slot does not exist")
        if slot.status != "available":
            raise PersistenceConflict("appointment slot is not available")

        row = Appointment(
            id=record.id,
            caller_id=record.caller_id,
            call_id=record.call_id,
            branch_id=record.branch_id,
            appointment_date=record.appointment_date,
            start_time=record.start_time,
            counsellor_id=record.counsellor_id,
            course_id=record.course_id,
            status=record.status,
            retention_until=record.retention_until,
            anonymized_at=record.anonymized_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _appointment_record(row)

    async def get(self, appointment_id: UUID) -> AppointmentRecord | None:
        row = await self._session.get(Appointment, appointment_id)
        return None if row is None else _appointment_record(row)


class EvidencePostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_safety_event(self, record: SafetyEventRecord) -> SafetyEventRecord:
        row = SafetyEvent(
            id=record.id,
            call_id=record.call_id,
            turn_id=record.turn_id,
            rule=record.rule,
            original_category=record.original_category,
            replacement_type=record.replacement_type,
            metadata_=_json(record.metadata),
            retention_until=record.retention_until,
            deleted_at=record.deleted_at,
            created_at=record.created_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _safety_event_record(row)

    async def add_provider_usage(self, record: ProviderUsageRecord) -> ProviderUsageRecord:
        row = ProviderUsage(
            id=record.id,
            call_id=record.call_id,
            turn_id=record.turn_id,
            provider=record.provider,
            service=record.service,
            model=record.model,
            measured_units=record.measured_units,
            unit_name=record.unit_name,
            provider_metadata=_json(record.provider_metadata),
            occurred_at=record.occurred_at,
            idempotency_key=record.idempotency_key,
            retention_until=record.retention_until,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _provider_usage_record(row)

    async def add_call_cost(self, record: CallCostRecord) -> CallCostRecord:
        row = CallCost(
            id=record.id,
            call_id=record.call_id,
            provider_usage_id=record.provider_usage_id,
            provider=record.provider,
            service=record.service,
            model=record.model,
            quantity=record.quantity,
            unit=record.unit,
            unit_price=record.unit_price,
            amount=record.amount,
            currency=record.currency,
            pricing_version=record.pricing_version,
            metadata_=_json(record.metadata),
            occurred_at=record.occurred_at,
            idempotency_key=record.idempotency_key,
            retention_until=record.retention_until,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _call_cost_record(row)

    async def add_recording(self, record: RecordingRecord) -> RecordingRecord:
        row = Recording(
            id=record.id,
            call_id=record.call_id,
            storage_provider=record.storage_provider,
            object_key=record.object_key,
            media_type=record.media_type,
            duration_ms=record.duration_ms,
            size_bytes=record.size_bytes,
            checksum=record.checksum,
            status=record.status,
            consent_at=record.consent_at,
            retention_until=record.retention_until,
            deleted_at=record.deleted_at,
            deletion_metadata=_json(record.deletion_metadata),
            created_at=record.created_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _recording_record(row)

    async def add_followup_job(self, record: FollowupJobRecord) -> FollowupJobRecord:
        row = FollowupJob(
            id=record.id,
            call_id=record.call_id,
            appointment_id=record.appointment_id,
            job_type=record.job_type,
            payload=_json(record.payload),
            status=record.status,
            attempts=record.attempts,
            available_at=record.available_at,
            lock_owner=record.lock_owner,
            locked_at=record.locked_at,
            last_error=record.last_error,
            idempotency_key=record.idempotency_key,
            retention_until=record.retention_until,
            deleted_at=record.deleted_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _followup_job_record(row)

    async def add_audit_log(self, record: AuditLogRecord) -> AuditLogRecord:
        row = AuditLog(
            id=record.id,
            actor_user_id=record.actor_user_id,
            action=record.action,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            request_id=record.request_id,
            correlation_id=record.correlation_id,
            metadata_=_json(record.metadata),
            occurred_at=record.occurred_at,
            retention_until=record.retention_until,
        )
        self._session.add(row)
        await _flush_or_conflict(self._session)
        return _audit_log_record(row)


class ReferenceDataPostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_institute(self, institute_id: UUID) -> InstituteRecord | None:
        row = await self._session.get(Institute, institute_id)
        return None if row is None else _institute_record(row)

    async def list_institutes(self) -> tuple[InstituteRecord, ...]:
        rows = await self._session.scalars(select(Institute).order_by(Institute.code))
        return tuple(_institute_record(row) for row in rows)

    async def upsert_institute(self, *, code: str, name: str, is_active: bool) -> UUID:
        return await self._upsert_id(
            Institute,
            {"code": code, "name": name, "is_active": is_active},
            [Institute.code],
            {"name": name, "is_active": is_active},
        )

    async def get_branch(self, branch_id: UUID) -> BranchRecord | None:
        row = await self._session.get(Branch, branch_id)
        return None if row is None else _branch_record(row)

    async def list_branches(self, institute_id: UUID | None = None) -> tuple[BranchRecord, ...]:
        statement = select(Branch)
        if institute_id is not None:
            statement = statement.where(Branch.institute_id == institute_id)
        rows = await self._session.scalars(statement.order_by(Branch.code))
        return tuple(_branch_record(row) for row in rows)

    async def upsert_branch(
        self,
        *,
        institute_id: UUID,
        code: str,
        name: str,
        city: str,
        timezone: str,
        is_active: bool,
    ) -> UUID:
        return await self._upsert_id(
            Branch,
            {
                "institute_id": institute_id,
                "code": code,
                "name": name,
                "city": city,
                "timezone": timezone,
                "is_active": is_active,
            },
            [Branch.institute_id, Branch.code],
            {"name": name, "city": city, "timezone": timezone, "is_active": is_active},
        )

    async def get_course(self, course_id: UUID) -> CourseRecord | None:
        row = await self._session.get(Course, course_id)
        return None if row is None else _course_record(row)

    async def list_courses(self, institute_id: UUID | None = None) -> tuple[CourseRecord, ...]:
        statement = select(Course)
        if institute_id is not None:
            statement = statement.where(Course.institute_id == institute_id)
        rows = await self._session.scalars(statement.order_by(Course.code))
        return tuple(_course_record(row) for row in rows)

    async def upsert_course(
        self,
        *,
        institute_id: UUID,
        code: str,
        name: str,
        description: str | None,
        is_active: bool,
    ) -> UUID:
        return await self._upsert_id(
            Course,
            {
                "institute_id": institute_id,
                "code": code,
                "name": name,
                "description": description,
                "is_active": is_active,
            },
            [Course.institute_id, Course.code],
            {"name": name, "description": description, "is_active": is_active},
        )

    async def upsert_branch_course_offering(
        self, *, branch_id: UUID, course_id: UUID
    ) -> tuple[UUID, UUID]:
        statement = (
            insert(BranchCourse)
            .values(branch_id=branch_id, course_id=course_id)
            .on_conflict_do_nothing(index_elements=[BranchCourse.branch_id, BranchCourse.course_id])
        )
        await _execute_or_conflict(self._session, statement)
        return branch_id, course_id

    async def list_branch_course_offerings(self, branch_id: UUID) -> tuple[tuple[UUID, UUID], ...]:
        rows = await self._session.execute(
            select(BranchCourse.branch_id, BranchCourse.course_id)
            .where(BranchCourse.branch_id == branch_id)
            .order_by(BranchCourse.course_id)
        )
        return tuple((row.branch_id, row.course_id) for row in rows)

    async def get_role(self, role_id: UUID) -> RoleRecord | None:
        row = await self._session.get(Role, role_id)
        return None if row is None else _role_record(row)

    async def list_roles(self) -> tuple[RoleRecord, ...]:
        rows = await self._session.scalars(select(Role).order_by(Role.name))
        return tuple(_role_record(row) for row in rows)

    async def upsert_role(self, *, name: str, description: str | None) -> UUID:
        return await self._upsert_id(
            Role,
            {"name": name, "description": description},
            [Role.name],
            {"description": description},
        )

    async def get_user(self, user_id: UUID) -> UserRecord | None:
        row = await self._session.get(User, user_id)
        return None if row is None else _user_record(row)

    async def list_users(self) -> tuple[UserRecord, ...]:
        rows = await self._session.scalars(select(User).order_by(User.email))
        return tuple(_user_record(row) for row in rows)

    async def upsert_user(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        is_active: bool,
    ) -> UUID:
        return await self._upsert_id(
            User,
            {
                "email": email,
                "display_name": display_name,
                "password_hash": password_hash,
                "is_active": is_active,
            },
            [User.email],
            {
                "display_name": display_name,
                "password_hash": password_hash,
                "is_active": is_active,
            },
        )

    async def upsert_user_role(self, *, user_id: UUID, role_id: UUID) -> tuple[UUID, UUID]:
        statement = (
            insert(UserRole)
            .values(user_id=user_id, role_id=role_id)
            .on_conflict_do_nothing(index_elements=[UserRole.user_id, UserRole.role_id])
        )
        await _execute_or_conflict(self._session, statement)
        return user_id, role_id

    async def list_user_roles(self, user_id: UUID) -> tuple[tuple[UUID, UUID], ...]:
        rows = await self._session.execute(
            select(UserRole.user_id, UserRole.role_id)
            .where(UserRole.user_id == user_id)
            .order_by(UserRole.role_id)
        )
        return tuple((row.user_id, row.role_id) for row in rows)

    async def get_counsellor(self, counsellor_id: UUID) -> CounsellorRecord | None:
        row = await self._session.get(Counsellor, counsellor_id)
        return None if row is None else _counsellor_record(row)

    async def list_counsellors(
        self, branch_id: UUID | None = None
    ) -> tuple[CounsellorRecord, ...]:
        statement = select(Counsellor)
        if branch_id is not None:
            statement = statement.where(Counsellor.branch_id == branch_id)
        rows = await self._session.scalars(statement.order_by(Counsellor.employee_code))
        return tuple(_counsellor_record(row) for row in rows)

    async def upsert_counsellor(
        self,
        *,
        branch_id: UUID,
        employee_code: str,
        display_name: str,
        user_id: UUID | None,
        is_active: bool,
    ) -> UUID:
        return await self._upsert_id(
            Counsellor,
            {
                "branch_id": branch_id,
                "employee_code": employee_code,
                "display_name": display_name,
                "user_id": user_id,
                "is_active": is_active,
            },
            [Counsellor.branch_id, Counsellor.employee_code],
            {"display_name": display_name, "user_id": user_id, "is_active": is_active},
        )

    async def _upsert_id(
        self,
        model: Any,
        values: Mapping[str, object],
        index_elements: list[Any],
        update_values: Mapping[str, object],
    ) -> UUID:
        statement = (
            insert(model)
            .values(**values)
            .on_conflict_do_update(index_elements=index_elements, set_=dict(update_values))
            .returning(model.id)
        )
        return await _scalar_or_conflict(self._session, statement)


__all__ = [
    "AppointmentPostgresRepository",
    "CallPostgresRepository",
    "CallerPostgresRepository",
    "EvidencePostgresRepository",
    "ReferenceDataPostgresRepository",
]
