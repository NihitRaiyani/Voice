"""Dependency-free protocols for PostgreSQL's durable business records.

Adapters own storage concerns and translate failures to ``roma.domain.persistence`` errors.
Repository methods participate in the surrounding unit of work; they never commit alone.
"""

from __future__ import annotations

from datetime import date, datetime
from types import TracebackType
from typing import Protocol, runtime_checkable
from uuid import UUID

from roma.domain.persistence import (
    AppointmentRecord,
    AppointmentSlotRecord,
    AuditLogRecord,
    CallCostRecord,
    CallerRecord,
    CallEventRecord,
    CallRecord,
    CallTurnRecord,
    FollowupJobRecord,
    ProviderUsageRecord,
    RecordingRecord,
    SafetyEventRecord,
)


@runtime_checkable
class CallerRepository(Protocol):
    """Stores caller profiles identified by a secret-bound phone digest."""

    async def add(self, record: CallerRecord) -> CallerRecord: ...

    async def get(self, caller_id: UUID) -> CallerRecord | None: ...

    async def get_by_phone_hash(self, phone_hash: str) -> CallerRecord | None: ...


@runtime_checkable
class CallRepository(Protocol):
    """Stores calls plus their append-only finalized turns and lifecycle events.

    ``occurred_at`` is the timestamp of the lifecycle change. The adapter uses it for the
    appropriate lifecycle timestamp (started, answered, or ended) while recording ``status``.
    """

    async def add(self, record: CallRecord) -> CallRecord: ...

    async def get(self, call_id: UUID) -> CallRecord | None: ...

    async def set_provider_call_id(self, call_id: UUID, provider_call_id: str) -> CallRecord: ...

    async def update_lifecycle(
        self,
        call_id: UUID,
        *,
        status: str,
        occurred_at: datetime,
        final_stage: str | None = None,
        booking_status: str | None = None,
    ) -> CallRecord: ...

    async def add_turn(self, record: CallTurnRecord) -> CallTurnRecord: ...

    async def list_turns(self, call_id: UUID) -> tuple[CallTurnRecord, ...]: ...

    async def add_event(self, record: CallEventRecord) -> CallEventRecord: ...


@runtime_checkable
class AppointmentRepository(Protocol):
    """Stores slots and books appointments atomically within one unit of work."""

    async def add_slot(self, record: AppointmentSlotRecord) -> AppointmentSlotRecord: ...

    async def list_available_slots(
        self, branch_id: UUID, appointment_date: date
    ) -> tuple[AppointmentSlotRecord, ...]: ...

    async def book(self, record: AppointmentRecord) -> AppointmentRecord: ...

    async def get(self, appointment_id: UUID) -> AppointmentRecord | None: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    """Appends safety, metering, cost, recording, work, and audit evidence."""

    async def add_safety_event(self, record: SafetyEventRecord) -> SafetyEventRecord: ...

    async def add_provider_usage(self, record: ProviderUsageRecord) -> ProviderUsageRecord: ...

    async def add_call_cost(self, record: CallCostRecord) -> CallCostRecord: ...

    async def add_recording(self, record: RecordingRecord) -> RecordingRecord: ...

    async def add_followup_job(self, record: FollowupJobRecord) -> FollowupJobRecord: ...

    async def add_audit_log(self, record: AuditLogRecord) -> AuditLogRecord: ...


@runtime_checkable
class ReferenceDataRepository(Protocol):
    """Upserts explicit organization and identity reference data without table-name APIs.

    Branch-course offerings and user-role assignments have composite primary keys, so their
    upserts return the stable ``(branch_id, course_id)`` and ``(user_id, role_id)`` identities.
    """

    async def upsert_institute(self, *, code: str, name: str, is_active: bool) -> UUID: ...

    async def upsert_branch(
        self,
        *,
        institute_id: UUID,
        code: str,
        name: str,
        city: str,
        timezone: str,
        is_active: bool,
    ) -> UUID: ...

    async def upsert_course(
        self,
        *,
        institute_id: UUID,
        code: str,
        name: str,
        description: str | None,
        is_active: bool,
    ) -> UUID: ...

    async def upsert_branch_course_offering(
        self, *, branch_id: UUID, course_id: UUID
    ) -> tuple[UUID, UUID]: ...

    async def upsert_role(self, *, name: str, description: str | None) -> UUID: ...

    async def upsert_user(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        is_active: bool,
    ) -> UUID: ...

    async def upsert_user_role(self, *, user_id: UUID, role_id: UUID) -> tuple[UUID, UUID]: ...

    async def upsert_counsellor(
        self,
        *,
        branch_id: UUID,
        employee_code: str,
        display_name: str,
        user_id: UUID | None,
        is_active: bool,
    ) -> UUID: ...


@runtime_checkable
class DurableUnitOfWork(Protocol):
    """Coordinates one atomic durable use case across the repository set."""

    @property
    def callers(self) -> CallerRepository: ...

    @property
    def calls(self) -> CallRepository: ...

    @property
    def appointments(self) -> AppointmentRepository: ...

    @property
    def evidence(self) -> EvidenceRepository: ...

    @property
    def reference_data(self) -> ReferenceDataRepository: ...

    async def __aenter__(self) -> DurableUnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


__all__ = [
    "AppointmentRepository",
    "CallRepository",
    "CallerRepository",
    "DurableUnitOfWork",
    "EvidenceRepository",
    "ReferenceDataRepository",
]
