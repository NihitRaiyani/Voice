"""Authorized, bounded operational aggregates without caller content."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from roma.api.v1.auth import require_resource_permission
from roma.api.v1.responses import ApiError, success_response
from roma.schemas.analytics import AnalyticsQuery
from roma.services.analytics_service import AnalyticsService

router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["analytics"],
    dependencies=[Depends(require_resource_permission("analytics.view"))],
)


def _service(request: Request) -> AnalyticsService:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "DATABASE_URL is not configured.")
    return AnalyticsService(factory)


async def _report(service, name, start_date, end_date):
    try:
        query = AnalyticsQuery(start_date=start_date, end_date=end_date)
    except ValidationError:
        raise ApiError(422, "VALIDATION_ERROR", "Request validation failed.") from None
    try:
        data = await getattr(service, name)(**query.model_dump())
    except (SQLAlchemyError, OSError, TimeoutError):
        raise ApiError(503, "DATABASE_UNAVAILABLE", "Analytics are unavailable.") from None
    response = success_response(data)
    response.headers["Cache-Control"] = "no-store"
    return response


AnalyticsDependency = Annotated[AnalyticsService, Depends(_service)]


@router.get("/overview")
async def overview(
    service: AnalyticsDependency, start_date: date | None = None, end_date: date | None = None
):
    return await _report(service, "overview", start_date, end_date)


@router.get("/funnel")
async def funnel(
    service: AnalyticsDependency, start_date: date | None = None, end_date: date | None = None
):
    return await _report(service, "funnel", start_date, end_date)


@router.get("/branches")
async def branches(
    service: AnalyticsDependency, start_date: date | None = None, end_date: date | None = None
):
    return await _report(service, "branches", start_date, end_date)
