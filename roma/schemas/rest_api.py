"""Pydantic contracts for the Level 8 versioned REST API."""

from __future__ import annotations

from datetime import date, time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CallStatus = Literal[
    "pending",
    "ringing",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
    "no_answer",
    "busy",
]
CallDirection = Literal["inbound", "outbound"]
AppointmentStatus = Literal["booked", "confirmed", "completed", "cancelled", "no_show"]

Limit = Annotated[int, Field(ge=1, le=100)]
Offset = Annotated[int, Field(ge=0)]
ShortText = Annotated[str, Field(min_length=1, max_length=128)]
LanguageCode = Annotated[str, Field(min_length=2, max_length=32)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCallRequest(ApiModel):
    caller_id: UUID | None = None
    provider_call_id: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    direction: CallDirection = "outbound"
    status: CallStatus = "pending"
    language: LanguageCode | None = None


class CreateAppointmentRequest(ApiModel):
    branch_id: UUID
    appointment_date: date
    start_time: time
    caller_id: UUID | None = None
    call_id: UUID | None = None
    counsellor_id: UUID | None = None
    course_id: UUID | None = None
    status: Literal["booked", "confirmed"] = "booked"


class UpdateAppointmentRequest(ApiModel):
    status: AppointmentStatus
    counsellor_id: UUID | None = None
    course_id: UUID | None = None


class CallsQuery(ApiModel):
    limit: Limit = 20
    offset: Offset = 0
    status: CallStatus | None = None
    language: LanguageCode | None = None
    sort: Literal[
        "created_at_desc",
        "created_at_asc",
        "started_at_desc",
        "started_at_asc",
    ] = "created_at_desc"


class LeadsQuery(ApiModel):
    limit: Limit = 20
    offset: Offset = 0
    city: ShortText | None = None
    preferred_language: LanguageCode | None = None
    sort: Literal["created_at_desc", "created_at_asc"] = "created_at_desc"


class AppointmentsQuery(ApiModel):
    limit: Limit = 20
    offset: Offset = 0
    branch_id: UUID | None = None
    status: AppointmentStatus | None = None
    sort: Literal["date_asc", "date_desc", "created_at_desc"] = "date_asc"


class SafetyEventsQuery(ApiModel):
    limit: Limit = 20
    offset: Offset = 0
    call_id: UUID | None = None
    rule: ShortText | None = None
    sort: Literal["created_at_desc", "created_at_asc"] = "created_at_desc"


__all__ = [
    "AppointmentStatus",
    "AppointmentsQuery",
    "CallDirection",
    "CallStatus",
    "CallsQuery",
    "CreateAppointmentRequest",
    "CreateCallRequest",
    "LeadsQuery",
    "SafetyEventsQuery",
    "UpdateAppointmentRequest",
]
