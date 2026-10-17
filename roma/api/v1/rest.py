"""Versioned JSON REST resources for Level 8."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from roma.api.v1.responses import ApiError, success_response
from roma.core.config import get_settings
from roma.schemas.rest_api import (
    AppointmentHoldRequest,
    AppointmentsQuery,
    CallsQuery,
    CreateAppointmentRequest,
    CreateCallRequest,
    LeadsQuery,
    ReleaseAppointmentHoldRequest,
    SafetyEventsQuery,
    SafetyStatsQuery,
    UpdateAppointmentRequest,
)
from roma.services.rest_api_service import RestApiService, RestApiServiceError

SessionFactory = async_sessionmaker[AsyncSession]


def require_api_auth(request: Request) -> None:
    settings = get_settings()
    if settings.api_token is None or not settings.api_token.get_secret_value():
        raise ApiError(503, "API_TOKEN_NOT_CONFIGURED", "API_TOKEN is not configured.")
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        presented.strip(), settings.api_token.get_secret_value()
    ):
        raise ApiError(401, "UNAUTHORIZED", "Bad or missing bearer token.")


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_auth)])


def _service_from_request(request: Request) -> RestApiService:
    session_factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "DATABASE_URL is not configured.")
    return RestApiService(session_factory, getattr(request.app.state, "booking_service", None))


def _translate(error: RestApiServiceError) -> ApiError:
    return ApiError(error.status_code, error.code, error.message)


def _query(model, **values):
    try:
        return model(**values)
    except ValidationError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "Request validation failed.") from exc


@router.post("/calls", status_code=status.HTTP_201_CREATED, tags=["calls"])
async def create_call(
    payload: CreateCallRequest,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.create_call(payload)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data, status_code=status.HTTP_201_CREATED)


@router.get("/calls", tags=["calls"])
async def list_calls(
    service: Annotated[RestApiService, Depends(_service_from_request)],
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    language: str | None = None,
    sort: str = "created_at_desc",
):
    query = _query(
        CallsQuery,
        limit=limit,
        offset=offset,
        status=status,
        language=language,
        sort=sort,
    )
    data, meta = await service.list_calls(query)
    return success_response(data, meta=meta)


@router.get("/calls/{call_id}", tags=["calls"])
async def get_call(
    call_id: UUID,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.get_call(call_id)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data)


@router.get("/leads", tags=["leads"])
async def list_leads(
    service: Annotated[RestApiService, Depends(_service_from_request)],
    limit: int = 20,
    offset: int = 0,
    city: str | None = None,
    preferred_language: str | None = None,
    sort: str = "created_at_desc",
):
    query = _query(
        LeadsQuery,
        limit=limit,
        offset=offset,
        city=city,
        preferred_language=preferred_language,
        sort=sort,
    )
    data, meta = await service.list_leads(query)
    return success_response(data, meta=meta)


@router.get("/appointment-slots", tags=["appointments"])
async def appointment_slots(
    branch_id: UUID,
    service: Annotated[RestApiService, Depends(_service_from_request)],
    on: date | None = None,
):
    try:
        data = await service.appointment_slots(branch_id, on)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data)


@router.post("/appointment-holds", status_code=201, tags=["appointments"])
async def hold_appointment(
    payload: AppointmentHoldRequest,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.hold_appointment(payload)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data, status_code=201)


@router.delete("/appointment-holds", tags=["appointments"])
async def release_appointment_hold(
    payload: ReleaseAppointmentHoldRequest,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    data = await service.release_appointment_hold(payload)
    return success_response(data)


@router.post("/appointments", status_code=status.HTTP_201_CREATED, tags=["appointments"])
async def create_appointment(
    payload: CreateAppointmentRequest,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.create_appointment(payload)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data, status_code=status.HTTP_201_CREATED)


@router.get("/appointments", tags=["appointments"])
async def list_appointments(
    service: Annotated[RestApiService, Depends(_service_from_request)],
    limit: int = 20,
    offset: int = 0,
    branch_id: UUID | None = None,
    status: str | None = None,
    sort: str = "date_asc",
):
    query = _query(
        AppointmentsQuery,
        limit=limit,
        offset=offset,
        branch_id=branch_id,
        status=status,
        sort=sort,
    )
    data, meta = await service.list_appointments(query)
    return success_response(data, meta=meta)


@router.patch("/appointments/{appointment_id}", tags=["appointments"])
async def update_appointment(
    appointment_id: UUID,
    payload: UpdateAppointmentRequest,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.update_appointment(appointment_id, payload)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data)


@router.delete("/appointments/{appointment_id}", tags=["appointments"])
async def delete_appointment(
    appointment_id: UUID,
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    try:
        data = await service.cancel_appointment(appointment_id)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data)


@router.get("/analytics/overview", tags=["analytics"])
async def analytics_overview(
    service: Annotated[RestApiService, Depends(_service_from_request)],
):
    return success_response(await service.analytics_overview())


@router.get("/safety/events", tags=["safety"])
async def list_safety_events(
    service: Annotated[RestApiService, Depends(_service_from_request)],
    limit: int = 20,
    offset: int = 0,
    call_id: UUID | None = None,
    rule: str | None = None,
    category: str | None = None,
    sort: str = "created_at_desc",
):
    query = _query(
        SafetyEventsQuery,
        limit=limit,
        offset=offset,
        call_id=call_id,
        rule=rule,
        category=category,
        sort=sort,
    )
    try:
        data, meta = await service.list_safety_events(query)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data, meta=meta)


@router.get("/safety/stats", tags=["safety"])
async def safety_stats(
    service: Annotated[RestApiService, Depends(_service_from_request)],
    call_id: UUID | None = None,
    rule: str | None = None,
    category: str | None = None,
):
    query = _query(SafetyStatsQuery, call_id=call_id, rule=rule, category=category)
    try:
        data = await service.safety_stats(query)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data)


@router.get("/calls/{call_id}/safety-events", tags=["safety"])
async def call_safety_events(
    call_id: UUID,
    service: Annotated[RestApiService, Depends(_service_from_request)],
    limit: int = 20,
    offset: int = 0,
    rule: str | None = None,
    category: str | None = None,
    sort: str = "created_at_desc",
):
    query = _query(
        SafetyEventsQuery, limit=limit, offset=offset, rule=rule, category=category, sort=sort
    )
    try:
        data, meta = await service.list_call_safety_events(call_id, query)
    except RestApiServiceError as exc:
        raise _translate(exc) from exc
    return success_response(data, meta=meta)


__all__ = ["router"]
