from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from roma.api.v1.costs import _service, router
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.services.auth_service import Principal


def client(role="viewer"):
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.state.auth_service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=Principal(
                uuid4(), "test@example.invalid", "Synthetic", frozenset({role})
            )
        )
    )
    service = SimpleNamespace(
        summary=AsyncMock(return_value={"currencies": [], "unpriced_events": 1})
    )
    app.dependency_overrides[_service] = lambda: service
    app.include_router(router)
    return TestClient(app), service


@pytest.mark.parametrize("path", ["today", "provider", "average", f"call/{uuid4()}"])
def test_cost_analytics_require_auth_and_allow_analytics_viewer(path):
    http, service = client()
    url = f"/api/v1/analytics/cost/{path}"
    assert http.get(url).status_code == 401
    response = http.get(url, headers={"Authorization": "Bearer jwt"})
    assert response.status_code == 200
    assert response.json()["data"]["unpriced_events"] == 1
    assert "no-store" in response.headers["cache-control"]
    service.summary.assert_awaited_once()


def test_month_validation_and_day_month_conflict_have_safe_errors():
    http, service = client()
    auth = {"Authorization": "Bearer jwt"}
    for query in ("month=2026-99", "month=2026-10&day=2026-10-26"):
        response = http.get(f"/api/v1/analytics/cost/today?{query}", headers=auth)
        assert response.status_code == 422
    service.summary.assert_not_awaited()
