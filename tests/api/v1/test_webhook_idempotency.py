"""Signed webhook contract, retry responses, and secret-free failure handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from roma.api.v1.twilio_webhooks import mount_answer_route
from roma.domain.webhooks import WebhookConflict, WebhookUnavailable
from roma.providers.telephony.twilio.webhooks import answer_event
from roma.services.webhook_service import WebhookService
from starlette.testclient import TestClient
from twilio.request_validator import RequestValidator

ACCOUNT = "AC" + "1" * 32
CALL = "CA" + "2" * 32
TOKEN = "offline-test-secret"
BASE = "https://host.example"


def make_app(service):
    app = FastAPI()
    app.state.webhook_service = service
    connected = AsyncMock()
    settings = SimpleNamespace(
        public_base_url=BASE,
        twilio_account_sid=SecretStr(ACCOUNT),
        twilio_auth_token=SecretStr(TOKEN),
    )
    mount_answer_route(app, settings=settings, on_connected=connected)
    return app, connected


def signed_post(client, *, params=None, path="/answer", signature=None, retry_token=None):
    params = params if params is not None else {"AccountSid": ACCOUNT, "CallSid": CALL}
    headers = {
        "X-Twilio-Signature": signature
        or RequestValidator(TOKEN).compute_signature(BASE + path, params)
    }
    if retry_token:
        headers["I-Twilio-Idempotency-Token"] = retry_token
    return client.post(path, data=params, headers=headers)


def test_duplicate_delivery_returns_same_twiml_with_same_business_key():
    service = AsyncMock()
    service.accept_answer.side_effect = [True, False]
    app, connected = make_app(service)
    with TestClient(app) as client:
        first = signed_post(client, path="/answer?lead=secret-lead", retry_token="attempt-1")
        second = signed_post(client, path="/answer?lead=secret-lead", retry_token="attempt-2")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["x-webhook-duplicate"] == "false"
    assert second.headers["x-webhook-duplicate"] == "true"
    events = [call.args[0] for call in service.accept_answer.await_args_list]
    assert events[0] == events[1]
    assert "secret-lead" not in repr(events[0])
    # Replaying the idempotent Redis projection repairs a crash after DB commit.
    assert connected.await_count == 2


@pytest.mark.parametrize(
    "params,signature,status",
    [
        ({"AccountSid": ACCOUNT, "CallSid": CALL}, "invalid", 403),
        ({"AccountSid": "AC" + "3" * 32, "CallSid": CALL}, None, 403),
        ({"AccountSid": ACCOUNT}, None, 400),
        ({"AccountSid": ACCOUNT, "CallSid": "invalid"}, None, 400),
        ({"AccountSid": ACCOUNT, "CallSid": CALL, "Direction": "invalid"}, None, 400),
    ],
)
def test_rejected_input_never_reaches_the_store(params, signature, status):
    service = AsyncMock()
    app, connected = make_app(service)
    with TestClient(app) as client:
        response = signed_post(client, params=params, signature=signature)
    assert response.status_code == status
    service.accept_answer.assert_not_awaited()
    connected.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status",
    [
        (WebhookUnavailable("secret-database-url"), 503),
        (WebhookConflict("secret-lead"), 409),
    ],
)
def test_failed_acceptance_is_not_acknowledged_or_projected(error, status):
    service = AsyncMock()
    service.accept_answer.side_effect = error
    app, connected = make_app(service)
    with TestClient(app) as client:
        response = signed_post(client)
    assert response.status_code == status
    assert "secret" not in response.text
    connected.assert_not_awaited()


def test_unconfigured_database_refuses_durable_acceptance():
    app, connected = make_app(None)
    with TestClient(app) as client:
        response = signed_post(client)
    assert response.status_code == 503
    connected.assert_not_awaited()


def test_wrong_content_type_is_rejected_without_effects():
    service = AsyncMock()
    app, _ = make_app(service)
    with TestClient(app) as client:
        assert client.post("/answer", json={}).status_code == 415
    service.accept_answer.assert_not_awaited()


def test_payload_digest_ignores_incidental_fields_but_binds_business_input():
    params = {"AccountSid": ACCOUNT, "CallSid": CALL, "Direction": "outbound-api"}
    first = answer_event(params, "lead-A")
    assert first == answer_event(
        {**params, "From": "+15555550100", "CallStatus": "in-progress"}, "lead-A"
    )
    changed = answer_event(params, "lead-B")
    assert changed.provider_event_id == first.provider_event_id
    assert changed.payload_digest != first.payload_digest


def test_acceptance_timeout_is_retryable(monkeypatch):
    import roma.services.webhook_service as module

    monkeypatch.setattr(module, "ACCEPT_TIMEOUT_SECS", 0.01)

    async def run():
        store = AsyncMock()
        cancelled = asyncio.Event()

        async def stalled(_event):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        store.accept_answer.side_effect = stalled
        with pytest.raises(WebhookUnavailable):
            await WebhookService(store).accept_answer(
                answer_event({"AccountSid": ACCOUNT, "CallSid": CALL}, None)
            )
        assert cancelled.is_set()

    asyncio.run(run())


def test_production_composition_wires_service_and_closes_database(monkeypatch):
    import roma.main as module

    database = SimpleNamespace(session_factory=object(), close=AsyncMock())
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setattr(module, "require_reachable_base_url", lambda: None)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(database_url=SecretStr("postgresql+asyncpg://unused")),
    )
    monkeypatch.setattr(module.Database, "from_settings", lambda _settings: database)
    mounted = {}

    def mount_web_api(app, *, session_factory=None):
        mounted["app"] = app
        mounted["session_factory"] = session_factory

    monkeypatch.setattr(module, "mount_web_api", mount_web_api)

    def build_app(**kwargs):
        app = FastAPI(lifespan=kwargs["lifespan"])
        app.state.webhook_service = kwargs["webhook_service"]
        assert kwargs["auto_hang_up"] is True
        return app

    monkeypatch.setattr(module, "build_media_app", build_app)
    with TestClient(module.create_app()) as client:
        assert isinstance(client.app.state.webhook_service, WebhookService)
        assert mounted == {"app": client.app, "session_factory": database.session_factory}
        database.close.assert_not_awaited()
    database.close.assert_awaited_once()
