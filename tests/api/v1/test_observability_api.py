from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.api.v1.observability import metrics_principal, router
from roma.api.v1.responses import ApiError, api_error_handler
from roma.core.telemetry import Telemetry
from roma.services.auth_service import Principal


def client(host='203.0.113.1', role=None):
    app = FastAPI()
    app.state.telemetry = Telemetry()
    app.state.telemetry.observe_latency('stt', .2)
    app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    if role:
        app.dependency_overrides[metrics_principal] = lambda: Principal(
            uuid4(), 'private@example.invalid', 'Role', frozenset({role}))
    return TestClient(app, client=(host, 9000))


@pytest.mark.parametrize('host', ['127.0.0.1', '::1'])
def test_loopback_scrape_is_allowed_and_ignores_untrusted_forwarded_header(host):
    result = client(host).get('/metrics', headers={'x-forwarded-for': '203.0.113.10'})
    assert result.status_code == 200
    assert result.headers['content-type'].startswith('text/plain')
    assert 'roma_latency_seconds' in result.text
    assert result.headers['cache-control'] == 'no-store'


def test_remote_scrape_requires_live_admin_and_forwarded_loopback_does_not_bypass():
    assert client().get('/metrics', headers={'x-forwarded-for': '127.0.0.1'}).status_code == 503
    assert client(role='viewer').get('/metrics').status_code == 403
    response = client(role='admin').get('/metrics')
    assert response.status_code == 200
    assert 'private@example.invalid' not in response.text


def test_scrape_refreshes_native_resources_only_after_access_check(monkeypatch):
    observed = []
    monkeypatch.setattr('roma.api.v1.observability.sample_process_resources',
                        lambda telemetry: observed.append(telemetry))
    assert client(role='viewer').get('/metrics').status_code == 403
    assert observed == []
    assert client('127.0.0.1').get('/metrics').status_code == 200
    assert len(observed) == 1 and isinstance(observed[0], Telemetry)
