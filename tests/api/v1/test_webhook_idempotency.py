"""Signed webhook contract, retry responses, and secret-free failure handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    from roma.core.call_capabilities import CallCapabilities

    intent = CallCapabilities(TOKEN, ACCOUNT).issue_answer("secret-lead")
    path = f"/answer?lead=secret-lead&intent={intent}"
    with TestClient(app) as client:
        first = signed_post(client, path=path, retry_token="attempt-1")
        second = signed_post(client, path=path, retry_token="attempt-2")
    assert first.status_code == second.status_code == 200
    assert "capability" in first.text and "capability" in second.text
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


@pytest.mark.parametrize("with_database", [True, False])
def test_production_composition_wires_service_and_closes_database(monkeypatch, with_database):
    import roma.main as module

    database = SimpleNamespace(session_factory=object(), close=AsyncMock())
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setattr(module, "require_reachable_base_url", lambda: None)
    knowledge = SimpleNamespace(catalog=object(), provider=SimpleNamespace(embed=AsyncMock()))
    build_knowledge = Mock(return_value=knowledge)
    monkeypatch.setattr(module, "build_knowledge_components", build_knowledge)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=SecretStr("postgresql+asyncpg://unused" if with_database else ""),
            redis_url=SecretStr("redis://unused"),
            pii_hash_key=SecretStr("offline-pii-key" * 4),
            roma_data_dir="var/roma",
            booking_hold_ttl_secs=90,
            booking_db_timeout_secs=3,
            booking_redis_timeout_secs=1,
            rag_enabled=False,
        ),
    )
    monkeypatch.setattr(module.Database, "from_settings", lambda _settings: database)
    redis = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(module.Redis, "from_url", lambda _url, **_kwargs: redis)
    mounted = {}

    def mount_web_api(app, *, session_factory=None):
        mounted["app"] = app
        mounted["session_factory"] = session_factory

    monkeypatch.setattr(module, "mount_web_api", mount_web_api)

    def build_app(**kwargs):
        app = FastAPI(lifespan=kwargs["lifespan"])
        app.state.webhook_service = kwargs["webhook_service"]
        assert kwargs["auto_hang_up"] is True
        from roma.domain.conversation.state import CallState
        from roma.services.booking_session import BookingSession

        state = CallState(call_sid="synthetic")
        booking_session = kwargs["booking_factory"](state)
        assert isinstance(booking_session, BookingSession) and booking_session.state is state
        app.state.test_booking = booking_session.service
        from roma.repositories.postgres.conversation_state import PostgresConversationStateStore
        from roma.repositories.redis.call_lease import RedisCallLease
        from roma.repositories.redis.conversation_state import RedisCallStateStore
        from roma.services.live_call_state import LiveCallStateStore

        factory = kwargs["build_store_fn"]
        settings = module.get_settings()
        first, second = factory(settings), factory(settings)
        assert first is not second
        if not with_database:
            assert isinstance(first, RedisCallStateStore)
            return app
        assert isinstance(first, LiveCallStateStore)
        assert isinstance(first.hot, RedisCallStateStore)
        assert isinstance(first.durable, PostgresConversationStateStore)
        assert first.durable._session_factory is database.session_factory
        lease = first.lease_factory("synthetic")
        assert isinstance(lease, RedisCallLease) and lease._client is redis
        return app

    monkeypatch.setattr(module, "build_media_app", build_app)
    with TestClient(module.create_app()) as client:
        assert client.app.state.knowledge_service is (knowledge.catalog if with_database else None)
        assert client.app.state.knowledge_search_service is None
        assert client.app.state.knowledge_answer_factory is None
        knowledge.provider.embed.assert_not_awaited()
        if with_database:
            assert isinstance(client.app.state.webhook_service, WebhookService)
        else:
            assert client.app.state.webhook_service is None
        assert mounted == {
            "app": client.app,
            "session_factory": database.session_factory if with_database else None,
        }
        assert client.app.state.test_booking is client.app.state.booking_service
        database.close.assert_not_awaited()
        redis.aclose.assert_not_awaited()
    if with_database:
        database.close.assert_awaited_once()
    else:
        database.close.assert_not_awaited()
    redis.aclose.assert_awaited_once()


def test_signed_payload_rejects_oversized_unknown_fields_before_acceptance():
    service = AsyncMock()
    app, _ = make_app(service)
    with TestClient(app) as client:
        response = signed_post(
            client, params={"AccountSid": ACCOUNT, "CallSid": CALL, "FutureField": "x" * 17000}
        )
    assert response.status_code == 413
    service.accept_answer.assert_not_awaited()


def test_outbound_answer_requires_fresh_lead_bound_intent():
    service = AsyncMock()
    app, _ = make_app(service)
    with TestClient(app) as client:
        response = signed_post(client, path="/answer?lead=secret-lead")
    assert response.status_code == 403
    service.accept_answer.assert_not_awaited()


@pytest.mark.parametrize("mode", ["expired", "wrong-lead", "wrong-account"])
def test_outbound_answer_rejects_stale_or_mismatched_intent_before_effects(mode):
    import time

    from roma.core.call_capabilities import CallCapabilities

    clock = (lambda: time.time() - 301) if mode == "expired" else time.time
    account = "AC" + "3" * 32 if mode == "wrong-account" else ACCOUNT
    lead = "another-lead" if mode == "wrong-lead" else "secret-lead"
    intent = CallCapabilities(TOKEN, account, clock=clock).issue_answer(lead)
    service = AsyncMock()
    app, connected = make_app(service)
    with TestClient(app) as client:
        response = signed_post(client, path=f"/answer?lead=secret-lead&intent={intent}")
    assert response.status_code == 403
    service.accept_answer.assert_not_awaited()
    connected.assert_not_awaited()


def test_outbound_without_lead_still_requires_and_accepts_intent():
    from roma.core.call_capabilities import CallCapabilities

    service = AsyncMock()
    app, _ = make_app(service)
    params = {"AccountSid": ACCOUNT, "CallSid": CALL, "Direction": "outbound-api"}
    with TestClient(app) as client:
        assert signed_post(client, params=params).status_code == 403
        intent = CallCapabilities(TOKEN, ACCOUNT).issue_answer(None)
        assert (
            signed_post(client, params=params, path=f"/answer?intent={intent}").status_code
            == 200
        )
    service.accept_answer.assert_awaited_once()


@pytest.mark.parametrize(
    "body",
    [
        b"AccountSid=%ZZ",
        b"AccountSid=%FF",
        b"AccountSid=one&AccountSid=two",
        b"unassigned",
        b"x=" + b"x" * 2049,
        b"&".join(f"k{i}=v".encode() for i in range(65)),
        b"x" * 65 + b"=v",
    ],
)
def test_malformed_forms_are_bounded_before_signature_or_effects(body):
    service = AsyncMock()
    app, connected = make_app(service)
    with TestClient(app) as client:
        response = client.post(
            "/answer",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 400
    service.accept_answer.assert_not_awaited()
    connected.assert_not_awaited()


def test_valid_unknown_form_fields_remain_covered_by_signature():
    service = AsyncMock()
    app, _ = make_app(service)
    params = {"AccountSid": ACCOUNT, "CallSid": CALL, "FutureField": "bounded"}
    signature = RequestValidator(TOKEN).compute_signature(BASE + "/answer", params)
    with TestClient(app) as client:
        assert signed_post(client, params=params, signature=signature).status_code == 200
        assert (
            signed_post(
                client, params={**params, "FutureField": "modified"}, signature=signature
            ).status_code
            == 403
        )
    service.accept_answer.assert_awaited_once()


def test_duplicate_callback_query_is_rejected_before_effects():
    service = AsyncMock()
    app, connected = make_app(service)
    with TestClient(app) as client:
        response = signed_post(client, path="/answer?intent=a&intent=b")
    assert response.status_code == 400
    service.accept_answer.assert_not_awaited()
    connected.assert_not_awaited()
