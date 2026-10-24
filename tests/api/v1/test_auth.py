from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from tests.repositories.postgres.test_repositories import (
    alembic_config,
    disposable_postgres_url,
    session_factory,
)

__all__ = ["session_factory", "alembic_config", "disposable_postgres_url"]

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from roma.api.v1.auth import require_permission, router
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.services.auth_service import Principal


def app(service=None):
    application = FastAPI()
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.state.auth_service = service
    application.state.auth_rate_limiter = SimpleNamespace(take=AsyncMock(return_value=True))
    application.include_router(router)

    @application.get(
        "/protected", dependencies=[Depends(require_permission("recordings.view"))]
    )
    async def protected():
        return {"allowed": True}

    return TestClient(application)


def test_auth_endpoints_fail_closed_when_unconfigured_and_never_echo_credentials():
    client = app()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.invalid", "password": "synthetic password 123!"},
    )
    assert response.status_code == 503
    assert "password" not in response.text and "synthetic" not in response.text
    assert (
        client.get("/protected", headers={"Authorization": "Bearer static-secret"}).status_code
        == 503
    )


def test_rbac_requires_valid_bearer_and_denies_viewer_recordings():
    viewer = Principal(uuid4(), "test@example.invalid", "Synthetic", frozenset({"viewer"}))
    service = SimpleNamespace(authenticate=AsyncMock(return_value=viewer))
    client = app(service)
    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer jwt"}).status_code == 403
    assert (
        client.post(
            "/api/v1/users", headers={"Authorization": "Bearer jwt"}, json={}
        ).status_code
        == 403
    )


@pytest.mark.postgres
def test_admin_can_create_and_disable_users_with_standard_json_roles(session_factory):
    import asyncio

    import httpx
    from pydantic import SecretStr
    from roma.core.auth_settings import AuthSettings
    from roma.services.auth_service import AuthService

    async def run():
        service = AuthService(
            session_factory,
            AuthSettings(
                _env_file=None, auth_jwt_secret=SecretStr("synthetic-only-secret-" * 3)
            ),
        )
        await service.create_user(
            "admin@example.invalid", "Synthetic admin", "synthetic password 123!", {"admin"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(service).app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.invalid", "password": "synthetic password 123!"},
            )
            assert (
                response.status_code == 200 and response.headers["cache-control"] == "no-store"
            )
            credentials = response.json()["data"]
            headers = {"Authorization": f"Bearer {credentials['access_token']}"}
            created = await client.post(
                "/api/v1/users",
                headers=headers,
                json={
                    "email": "new@example.invalid",
                    "display_name": "Synthetic viewer",
                    "password": "synthetic password 123!",
                    "roles": ["viewer"],
                },
            )
            assert created.status_code == 201, created.text
            user = created.json()["data"]
            assert "password" not in created.text and user["roles"] == ["viewer"]
            updated = await client.patch(
                f"/api/v1/users/{user['id']}",
                headers=headers,
                json={"roles": ["counsellor"], "is_active": False},
            )
            assert updated.status_code == 200 and not updated.json()["data"]["is_active"]

    asyncio.run(run())


def test_native_database_outage_uses_safe_auth_unavailable_envelope():
    import asyncio

    from asyncpg.exceptions import TooManyConnectionsError
    from pydantic import SecretStr
    from roma.core.auth_settings import AuthSettings
    from roma.services.auth_service import AuthError, AuthService

    async def run():
        repository = SimpleNamespace(
            find_user=AsyncMock(side_effect=TooManyConnectionsError("private SQL details"))
        )
        service = AuthService(
            None,
            AuthSettings(_env_file=None, auth_jwt_secret=SecretStr("synthetic-secret-" * 4)),
            repository=repository,
        )
        try:
            await service.login("test@example.invalid", "synthetic password 123!")
        except AuthError as error:
            assert error.status_code == 503 and "private" not in error.message
        else:
            raise AssertionError("Expected a safe storage failure")

    asyncio.run(run())


def test_static_machine_token_is_not_trusted_without_explicit_compatibility(monkeypatch):
    from roma.api.v1.calls import mount_web_api
    from roma.api.v1.rest import _service_from_request
    from roma.core.auth_settings import AuthSettings
    from roma.core.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(AuthSettings.model_config, "env_file", None)
    for key, value in {
        "SARVAM_API_KEY": "offline-key",
        "OPENAI_API_KEY": "offline-key",
        "REDIS_URL": "redis://unused",
        "API_TOKEN": "legacy-secret",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AUTH_ALLOW_LEGACY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    application = FastAPI()
    mount_web_api(application)
    application.dependency_overrides[_service_from_request] = lambda: SimpleNamespace(
        list_calls=AsyncMock(return_value=([], {}))
    )
    response = TestClient(application).get(
        "/api/v1/calls", headers={"Authorization": "Bearer legacy-secret"}
    )
    assert response.status_code == 503
    get_settings.cache_clear()


def test_actual_rest_permissions_separate_recordings_leads_and_appointments(monkeypatch):
    from roma.api.v1.calls import mount_web_api
    from roma.api.v1.rest import _service_from_request
    from roma.core.auth_settings import AuthSettings
    from roma.core.config import Settings, get_settings
    from tests.api.v1.test_rest_resources import (
        APPOINTMENT_ID,
        BRANCH_ID,
        CALL_ID,
        CALLER_ID,
        FakeRestApiService,
    )

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(AuthSettings.model_config, "env_file", None)
    for key, value in {
        "SARVAM_API_KEY": "offline-key",
        "OPENAI_API_KEY": "offline-key",
        "REDIS_URL": "redis://unused",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from roma.api.v1 import calls as calls_module

    dial = AsyncMock(return_value={"request_uuid": "offline-request"})
    monkeypatch.setattr(calls_module, "place_outbound_call", dial)
    monkeypatch.setattr(
        calls_module,
        "get_call_status",
        AsyncMock(return_value={"status": "ended", "reason": ""}),
    )

    def service_override(service):
        return lambda: service

    for role in ["viewer", "counsellor", "admin"]:
        application = FastAPI()
        mount_web_api(application)
        principal = Principal(uuid4(), "test@example.invalid", "Synthetic", frozenset({role}))
        application.state.auth_service = SimpleNamespace(
            authenticate=AsyncMock(return_value=principal)
        )
        service = FakeRestApiService()
        original_get = service.get_call

        async def get_call(call_id, original_get=original_get):
            data = await original_get(call_id)
            return {**data, "recording_url": "private/recording.wav"}

        service.get_call = get_call
        original_list = service.list_calls

        async def list_calls(query, original_list=original_list):
            rows, meta = await original_list(query)
            return [{**row, "recording_url": "private/recording.wav"} for row in rows], meta

        service.list_calls = list_calls
        application.dependency_overrides[_service_from_request] = service_override(service)
        client = TestClient(application)
        auth = {"Authorization": "Bearer synthetic-jwt"}
        listing = client.get("/api/v1/calls", headers=auth)
        assert listing.status_code == 200
        assert listing.json()["data"][0]["recording_url"] == (
            None if role == "viewer" else "private/recording.wav"
        )
        detail = client.get(f"/api/v1/calls/{CALL_ID}", headers=auth)
        assert detail.status_code == 200
        assert detail.json()["data"]["recording_url"] == (
            None if role == "viewer" else "private/recording.wav"
        )
        assert client.get("/api/v1/leads", headers=auth).status_code == (
            403 if role == "viewer" else 200
        )
        assert client.get("/api/v1/appointments", headers=auth).status_code == (
            403 if role == "viewer" else 200
        )
        assert client.get("/api/v1/analytics/overview", headers=auth).status_code == 200
        created = client.post("/api/v1/calls", headers=auth, json={"direction": "inbound"})
        assert created.status_code == (201 if role == "admin" else 403)
        assert client.get("/api/v1/safety/events", headers=auth).status_code == 200
        assert client.get("/api/v1/safety/stats", headers=auth).status_code == 200
        booked = client.post(
            "/api/v1/appointments",
            headers=auth,
            json={
                "branch_id": str(BRANCH_ID),
                "caller_id": str(CALLER_ID),
                "appointment_date": "2026-10-05",
                "start_time": "10:00:00",
                "status": "booked",
            },
        )
        assert booked.status_code == (403 if role == "viewer" else 201)
        assert client.patch(
            f"/api/v1/appointments/{APPOINTMENT_ID}", headers=auth, json={"status": "confirmed"}
        ).status_code == (403 if role == "viewer" else 200)
        assert client.delete(
            f"/api/v1/appointments/{APPOINTMENT_ID}", headers=auth
        ).status_code == (403 if role == "viewer" else 200)
        assert client.post(
            "/api/call", headers=auth, json={"to_number": "+919876543210"}
        ).status_code == (200 if role == "admin" else 403)
        assert client.get("/api/call/offline-request", headers=auth).status_code == 200
    assert dial.await_count == 1
    get_settings.cache_clear()


def test_login_limit_denies_before_password_work_and_backend_errors_fail_closed():
    from roma.services.auth_service import TokenPair

    service = SimpleNamespace(
        login=AsyncMock(return_value=TokenPair("synthetic-access", "x" * 64, 900))
    )
    client = app(service)
    client.app.state.auth_rate_limiter = SimpleNamespace(take=AsyncMock(return_value=False))
    payload = {"email": "test@example.invalid", "password": "synthetic password 123!"}
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 429
    service.login.assert_not_awaited()
    client.app.state.auth_rate_limiter.take.side_effect = OSError(
        "private Redis connection details"
    )
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 503 and "private" not in response.text
    service.login.assert_not_awaited()


def test_failed_jwt_never_downgrades_to_machine_or_user_management(monkeypatch):
    from roma.api.v1.calls import mount_web_api
    from roma.api.v1.rest import _service_from_request
    from roma.core.auth_settings import AuthSettings
    from roma.core.config import Settings, get_settings
    from roma.services.auth_service import AuthError

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(AuthSettings.model_config, "env_file", None)
    for key, value in {
        "SARVAM_API_KEY": "offline-key",
        "OPENAI_API_KEY": "offline-key",
        "REDIS_URL": "redis://unused",
        "API_TOKEN": "synthetic.jwt.credential",
        "AUTH_ALLOW_LEGACY_API_TOKEN": "true",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    application = FastAPI()
    mount_web_api(application)
    application.state.auth_service = SimpleNamespace(
        authenticate=AsyncMock(side_effect=AuthError())
    )
    resources = SimpleNamespace(list_calls=AsyncMock(return_value=([], {})))
    application.dependency_overrides[_service_from_request] = lambda: resources
    client = TestClient(application)
    assert (
        client.get(
            "/api/v1/calls", headers={"Authorization": "Bearer synthetic.jwt.credential"}
        ).status_code
        == 401
    )
    resources.list_calls.assert_not_awaited()
    # The explicit machine trust can read existing resources, never administer accounts.
    monkeypatch.setenv("API_TOKEN", "legacy-machine")
    get_settings.cache_clear()
    assert (
        client.get(
            "/api/v1/calls", headers={"Authorization": "Bearer legacy-machine"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/users", headers={"Authorization": "Bearer legacy-machine"}, json={}
        ).status_code
        == 401
    )
    get_settings.cache_clear()


@pytest.mark.parametrize("with_database", [True, False])
def test_production_auth_is_explicit_and_closes_before_database(monkeypatch, with_database):
    import roma.main as module
    from pydantic import SecretStr
    from roma.core.auth_settings import AuthSettings

    cfg = AuthSettings(
        _env_file=None, auth_jwt_secret=SecretStr("synthetic-only-auth-key-" * 3)
    )
    events = []

    async def auth_close():
        events.append("auth")

    async def redis_close():
        events.append("redis")

    async def db_close():
        events.append("database")

    auth = SimpleNamespace(aclose=auth_close)
    database = SimpleNamespace(session_factory=object(), close=db_close)
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setattr(module, "require_reachable_base_url", lambda: None)
    monkeypatch.setattr(module, "AuthSettings", lambda: cfg)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=SecretStr("postgresql+asyncpg://unused" if with_database else ""),
            redis_url=SecretStr("redis://unused"),
            booking_hold_ttl_secs=90,
            booking_db_timeout_secs=3,
            booking_redis_timeout_secs=1,
        ),
    )
    monkeypatch.setattr(module.Database, "from_settings", lambda _: database)
    monkeypatch.setattr(
        module.Redis, "from_url", lambda *a, **kw: SimpleNamespace(aclose=redis_close)
    )
    monkeypatch.setattr(module, "AuthService", lambda factory, settings: auth)
    monkeypatch.setattr(module, "WebhookService", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        module, "build_media_app", lambda **kwargs: FastAPI(lifespan=kwargs["lifespan"])
    )
    monkeypatch.setattr(module, "mount_web_api", lambda *args, **kwargs: None)
    with TestClient(module.create_app()) as client:
        assert (client.app.state.auth_service is auth) == with_database
        assert (client.app.state.auth_rate_limiter is not None) == with_database
    assert events == (["auth", "redis", "database"] if with_database else ["redis"])
