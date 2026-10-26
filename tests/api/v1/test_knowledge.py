from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from roma.api.v1.auth import current_principal
from roma.api.v1.knowledge import router
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.services.auth_service import Principal


def client(role=None, service=None):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    if role:
        app.dependency_overrides[current_principal] = lambda: Principal(
            uuid4(), "admin@example.invalid", "Role", frozenset({role})
        )
    if service:
        app.state.knowledge_service = service
    return TestClient(app)


@pytest.mark.parametrize("role", ["viewer", "counsellor"])
@pytest.mark.parametrize("action", ["documents", "approve", "activate", "archive"])
def test_mutating_governance_endpoints_deny_non_admin_before_body_or_service(role, action):
    path = "documents" if action == "documents" else f"documents/{uuid4()}/{action}"
    result = client(role).post("/api/v1/knowledge/" + path, json={})
    assert result.status_code == 403


def test_governance_requires_live_human_auth_and_fails_closed_unconfigured():
    assert client().post("/api/v1/knowledge/documents", json={}).status_code == 503
    result = client("admin").post(
        f"/api/v1/knowledge/documents/{uuid4()}/activate", json={"expected_version": 1}
    )
    assert result.status_code == 503


def test_admin_action_uses_server_principal_and_strict_bounded_action_body():
    service = SimpleNamespace(activate=AsyncMock(return_value={"status": "active"}))
    target = uuid4()
    api = client("admin", service)
    assert (
        api.post(
            f"/api/v1/knowledge/documents/{target}/activate", json={"expected_version": True}
        ).status_code
        == 422
    )
    assert (
        api.post(
            f"/api/v1/knowledge/documents/{target}/activate",
            json={"expected_version": 1, "approved_by": str(uuid4())},
        ).status_code
        == 422
    )
    response = api.post(
        f"/api/v1/knowledge/documents/{target}/activate", json={"expected_version": 1}
    )
    assert response.status_code == 200 and response.json()["data"]["status"] == "active"
    args = service.activate.call_args.args
    assert args[0].roles == frozenset({"admin"}) and args[1] == target


def test_search_is_denied_to_viewer_and_strict_for_authorized_roles():
    assert client('viewer').post('/api/v1/knowledge/search', json={}).status_code == 403
    assert client('admin').post('/api/v1/knowledge/search', json={'query':'SEO?', 'category':'fees', 'language':'gu'}).status_code == 422
    for role in ('admin','counsellor'):
        assert client(role).post('/api/v1/knowledge/search', json={'query':'SEO?', 'category':'course', 'language':'gu'}).status_code == 503
