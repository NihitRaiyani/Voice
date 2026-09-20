"""Plain durable-record contracts shared by application code and persistence adapters.

This module deliberately knows nothing about database sessions, SQL, or provider clients.
Adapters translate their storage representations to these immutable records at the boundary.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID


class PersistenceError(RuntimeError):
    """Base error exposed by durable persistence adapters."""


class PersistenceUnavailable(PersistenceError):
    """Raised when durable storage cannot safely serve a request."""


class PersistenceConflict(PersistenceError):
    """Raised when a database constraint rejects a conflicting business write."""


class RecordNotFound(PersistenceError):
    """Raised when an adapter requires a durable record that does not exist."""


def hash_phone_e164(phone: str, pepper: str) -> str:
    """Return a deterministic, secret-bound digest for a canonical E.164 phone number.

    Callers must pass the canonical number; this function intentionally does not normalize or
    log phone numbers, because accepting loosely formatted values would undermine uniqueness.
    """
    digits = phone[1:]
    if (
        not phone.startswith("+")
        or not 1 <= len(digits) <= 15
        or not digits.isascii()
        or not digits.isdigit()
        or digits.startswith("0")
    ):
        raise ValueError("phone must be canonical E.164: '+' followed by digits")

    pepper_bytes = pepper.encode("utf-8")
    if len(pepper_bytes) < 32:
        raise ValueError("phone hash pepper must be at least 32 UTF-8 bytes")

    return hmac.new(pepper_bytes, phone.encode("ascii"), hashlib.sha256).hexdigest()


def _freeze_json_value(value: object) -> object:
    """Create a recursively immutable snapshot of JSON-like metadata."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json_value(item) for item in value)
    return value


def _freeze_mapping_fields(record: object, *field_names: str) -> None:
    for field_name in field_names:
        object.__setattr__(record, field_name, _freeze_json_value(getattr(record, field_name)))


@dataclass(frozen=True, slots=True)
class CallerRecord:
    id: UUID
    phone_hash: str
    created_at: datetime
    updated_at: datetime
    name: str | None = None
    preferred_language: str | None = None
    city: str | None = None
    education: str | None = None
    current_status: str | None = None
    anonymized_at: datetime | None = None
    retention_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class CallRecord:
    id: UUID
    caller_id: UUID | None
    direction: str
    status: str
    started_at: datetime
    created_at: datetime
    updated_at: datetime
    provider_call_id: str | None = None
    language: str | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    final_stage: str | None = None
    booking_status: str | None = None
    total_cost: Decimal | None = None
    currency: str | None = None
    recording_url: str | None = None
    retention_until: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CallTurnRecord:
    id: UUID
    call_id: UUID
    turn_number: int
    speaker: str
    created_at: datetime
    transcript: str | None = None
    conversation_stage: str | None = None
    route: str | None = None
    latency_ms: int | None = None
    retention_until: datetime | None = None
    transcript_deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CallEventRecord:
    id: UUID
    call_id: UUID
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, object] = field(default_factory=dict)
    conversation_stage: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "payload")


@dataclass(frozen=True, slots=True)
class AppointmentSlotRecord:
    id: UUID
    branch_id: UUID
    appointment_date: date
    start_time: time
    end_time: time
    capacity: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AppointmentRecord:
    id: UUID
    branch_id: UUID
    appointment_date: date
    start_time: time
    status: str
    created_at: datetime
    updated_at: datetime
    caller_id: UUID | None = None
    call_id: UUID | None = None
    counsellor_id: UUID | None = None
    course_id: UUID | None = None
    retention_until: datetime | None = None
    anonymized_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SafetyEventRecord:
    id: UUID
    created_at: datetime
    rule: str | None = None
    original_category: str | None = None
    replacement_type: str | None = None
    call_id: UUID | None = None
    turn_id: UUID | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    retention_until: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "metadata")


@dataclass(frozen=True, slots=True)
class ProviderUsageRecord:
    id: UUID
    provider: str
    service: str
    measured_units: Decimal
    unit_name: str
    occurred_at: datetime
    idempotency_key: str
    model: str | None = None
    call_id: UUID | None = None
    turn_id: UUID | None = None
    provider_metadata: Mapping[str, object] = field(default_factory=dict)
    retention_until: datetime | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "provider_metadata")


@dataclass(frozen=True, slots=True)
class CallCostRecord:
    id: UUID
    provider: str
    service: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    amount: Decimal
    currency: str
    pricing_version: str
    occurred_at: datetime
    idempotency_key: str
    model: str | None = None
    call_id: UUID | None = None
    provider_usage_id: UUID | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    retention_until: datetime | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "metadata")


@dataclass(frozen=True, slots=True)
class RecordingRecord:
    id: UUID
    call_id: UUID
    storage_provider: str
    object_key: str
    media_type: str
    duration_ms: int | None
    size_bytes: int | None
    checksum: str | None
    status: str
    retention_until: datetime
    created_at: datetime
    consent_at: datetime | None = None
    deleted_at: datetime | None = None
    deletion_metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "deletion_metadata")


@dataclass(frozen=True, slots=True)
class FollowupJobRecord:
    id: UUID
    job_type: str
    payload: Mapping[str, object]
    status: str
    attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    idempotency_key: str
    call_id: UUID | None = None
    appointment_id: UUID | None = None
    lock_owner: str | None = None
    locked_at: datetime | None = None
    last_error: str | None = None
    retention_until: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "payload")


@dataclass(frozen=True, slots=True)
class AuditLogRecord:
    id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    retention_until: datetime | None = None

    def __post_init__(self) -> None:
        _freeze_mapping_fields(self, "metadata")


__all__ = [
    "AppointmentRecord",
    "AppointmentSlotRecord",
    "AuditLogRecord",
    "CallCostRecord",
    "CallEventRecord",
    "CallRecord",
    "CallTurnRecord",
    "CallerRecord",
    "FollowupJobRecord",
    "PersistenceConflict",
    "PersistenceError",
    "PersistenceUnavailable",
    "ProviderUsageRecord",
    "RecordNotFound",
    "RecordingRecord",
    "SafetyEventRecord",
    "hash_phone_e164",
]
