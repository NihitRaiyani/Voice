"""Authorized cost projections; values remain decimal strings per currency."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from roma.api.v1.auth import require_resource_permission
from roma.api.v1.responses import ApiError, success_response
from roma.schemas.costs import CostQuery
from roma.services.cost_service import CostCallNotFound, CostService

router = APIRouter(
    prefix="/api/v1/analytics/cost",
    tags=["analytics"],
    dependencies=[Depends(require_resource_permission("analytics.view"))],
)


def _service(request: Request) -> CostService:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "DATABASE_URL is not configured.")
    return CostService(factory)


def _query(day, month, provider=None):
    try:
        return CostQuery(day=day, month=month, provider=provider)
    except ValidationError:
        raise ApiError(422, "VALIDATION_ERROR", "Request validation failed.") from None


async def _report(service, query, **kwargs):
    try:
        data = await service.summary(**query.model_dump(), **kwargs)
    except CostCallNotFound:
        raise ApiError(404, "CALL_NOT_FOUND", "Call was not found.") from None
    except (SQLAlchemyError, OSError, TimeoutError):
        raise ApiError(503, "DATABASE_UNAVAILABLE", "Cost analytics are unavailable.") from None
    response = success_response(data)
    response.headers["Cache-Control"] = "no-store"
    return response


CostDependency = Annotated[CostService, Depends(_service)]


@router.get("/today")
async def today(service: CostDependency, day: date | None = None, month: str | None = None):
    return await _report(service, _query(day, month))


@router.get("/provider")
async def provider(
    service: CostDependency,
    provider: str | None = None,
    day: date | None = None,
    month: str | None = None,
):
    return await _report(service, _query(day, month, provider), group_by="provider")


@router.get("/call/{call_id}")
async def call_cost(call_id: UUID, service: CostDependency):
    return await _report(service, CostQuery(), call_id=call_id, group_by="turn")


@router.get("/average")
async def average(service: CostDependency, day: date | None = None, month: str | None = None):
    return await _report(service, _query(day, month))
