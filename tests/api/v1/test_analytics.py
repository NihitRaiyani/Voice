"""Permission, privacy and bounded-window API contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.services.auth_service import Principal


def client(role="viewer"):
    from roma.api.v1.analytics import _service, router

    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.state.auth_service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=Principal(
                uuid4(), "synthetic@example.invalid", "Synthetic", frozenset({role})
            )
        )
    )
    service = SimpleNamespace(
        **{
            name: AsyncMock(return_value={"synthetic": True})
            for name in ("overview", "funnel", "branches")
        }
    )
    app.dependency_overrides[_service] = lambda: service
    app.include_router(router)
    return TestClient(app), service


@pytest.mark.parametrize("path", ["overview", "funnel", "branches"])
def test_analytics_requires_permission_and_sends_no_store(path):
    http, service = client()
    url = f"/api/v1/analytics/{path}"
    assert http.get(url).status_code == 401
    response = http.get(url, headers={"Authorization": "Bearer jwt"})
    assert response.status_code == 200
    assert response.json()["data"] == {"synthetic": True}
    assert response.headers["Cache-Control"] == "no-store"
    getattr(service, path).assert_awaited_once()
    forbidden, forbidden_service = client("unknown")
    assert forbidden.get(url, headers={"Authorization": "Bearer jwt"}).status_code == 403
    getattr(forbidden_service, path).assert_not_awaited()


@pytest.mark.parametrize(
    "query",
    [
        "start_date=2030-01-01&end_date=2031-01-03",
        "start_date=2030-01-01&end_date=2030-01-01",
        "start_date=9999-12-31",
        "start_date=bad",
    ],
)
def test_analytics_rejects_invalid_or_unbounded_window(query):
    http, service = client()
    response = http.get(
        f"/api/v1/analytics/overview?{query}", headers={"Authorization": "Bearer jwt"}
    )
    assert response.status_code == 422
    service.overview.assert_not_awaited()
