"""Twilio HTTP and WebSocket callbacks must be signed and account-bound."""

import json
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

ACCOUNT_SID = "AC" + "1" * 32
AUTH_TOKEN = "test-auth-token"
CALL_SID = "CA" + "2" * 32

REQUIRED_ENV = {
    "TWILIO_ACCOUNT_SID": ACCOUNT_SID,
    "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
    "TWILIO_FROM_NUMBER": "+16295550100",
    "SARVAM_API_KEY": "sarvam_test",
    "OPENAI_API_KEY": "openai_test",
    "REDIS_URL": "redis://localhost:6379/0",
    "PUBLIC_BASE_URL": "https://host.example",
}


@pytest.fixture
def app(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    from roma.core.config import get_settings

    get_settings.cache_clear()
    from roma.realtime.pipeline import build_media_app

    yield build_media_app(auto_hang_up=False, webhook_service=AsyncMock())
    get_settings.cache_clear()


def _answer(app, *, signature=True, query=""):
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    public_url = f"https://host.example/answer{('?' + query) if query else ''}"
    headers = {}
    if signature:
        headers["X-Twilio-Signature"] = RequestValidator(AUTH_TOKEN).compute_signature(
            public_url, params
        )
    path = f"/answer{('?' + query) if query else ''}"
    return TestClient(app).post(path, data=params, headers=headers)


def _ws_headers(url="wss://host.example/ws"):
    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, {})
    return {"X-Twilio-Signature": signature}


def _expect_close(app, *, headers=None, start=None, code=1008):
    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(app).websocket_connect("/ws", headers=headers or {}) as ws:
            if start is not None:
                ws.send_text(json.dumps({"event": "start", "start": start}))
            ws.receive_text()
    assert exc.value.code == code


def test_answer_rejects_missing_and_invalid_signatures(app):
    assert _answer(app, signature=False).status_code == 403
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    response = TestClient(app).post(
        "/answer",
        data=params,
        headers={"X-Twilio-Signature": "invalid"},
    )
    assert response.status_code == 403


def test_answer_returns_a_bidirectional_stream(app):
    response = _answer(app)
    assert response.status_code == 200
    root = ElementTree.fromstring(response.text)
    stream = root.find("./Connect/Stream")
    assert stream is not None
    assert stream.attrib == {"url": "wss://host.example/ws"}


def test_rejects_a_socket_with_no_or_invalid_signature(app):
    _expect_close(app)
    _expect_close(app, headers={"X-Twilio-Signature": "invalid"})


def test_rejects_a_signed_socket_from_another_twilio_account(app):
    _expect_close(
        app,
        headers=_ws_headers(),
        start={
            "streamSid": "MZ123",
            "accountSid": "AC-wrong",
            "callSid": CALL_SID,
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
            "customParameters": {},
        },
    )


def test_health_reports_ok(app):
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"


@pytest.mark.parametrize(
    "mode", ["missing", "expired", "tampered", "wrong-call", "wrong-account", "wrong-lead"]
)
def test_stream_capability_rejected_before_replay_and_provider_construction(
    app, monkeypatch, mode
):
    import time

    import roma.realtime.pipeline as pipeline
    from roma.core.call_capabilities import CallCapabilities

    replay = AsyncMock()
    app.state.stream_replay_store = replay
    provider = Mock(side_effect=AssertionError("provider constructed"))
    monkeypatch.setattr(pipeline, "ValidatedTwilioSerializer", provider)
    account = "AC" + "3" * 32 if mode == "wrong-account" else ACCOUNT_SID
    call = "CA" + "3" * 32 if mode == "wrong-call" else CALL_SID
    lead = "lead-token" if mode == "wrong-lead" else None
    clock = (lambda: time.time() - 121) if mode == "expired" else time.time
    capability = CallCapabilities(AUTH_TOKEN, account, clock=clock).issue_stream(call, lead)
    if mode == "missing":
        capability = ""
    elif mode == "tampered":
        capability += "x"
    _expect_close(
        app,
        headers=_ws_headers(),
        start={
            "streamSid": "MZ123",
            "accountSid": ACCOUNT_SID,
            "callSid": CALL_SID,
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
            "customParameters": {"capability": capability},
        },
    )
    replay.consume.assert_not_awaited()
    provider.assert_not_called()


@pytest.mark.parametrize("unavailable", [False, True])
def test_stream_replay_and_redis_failure_are_closed_before_provider_construction(
    app, monkeypatch, unavailable
):
    import roma.realtime.pipeline as pipeline
    from roma.core.call_capabilities import CallCapabilities

    replay = AsyncMock()
    replay.consume.side_effect = RuntimeError("private redis url") if unavailable else None
    replay.consume.return_value = False
    app.state.stream_replay_store = replay
    provider = Mock(side_effect=AssertionError("provider constructed"))
    monkeypatch.setattr(pipeline, "ValidatedTwilioSerializer", provider)
    capability = CallCapabilities(AUTH_TOKEN, ACCOUNT_SID).issue_stream(CALL_SID, None)
    _expect_close(
        app,
        headers=_ws_headers(),
        code=1013 if unavailable else 1008,
        start={
            "streamSid": "MZ123",
            "accountSid": ACCOUNT_SID,
            "callSid": CALL_SID,
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
            "customParameters": {"capability": capability},
        },
    )
    replay.consume.assert_awaited_once_with(ACCOUNT_SID, CALL_SID)
    provider.assert_not_called()


def test_valid_stream_consumes_identity_once_and_replay_is_rejected(app, monkeypatch):
    import roma.realtime.pipeline as pipeline
    from roma.core.call_capabilities import CallCapabilities, RedisStreamReplay

    class Redis:
        def __init__(self):
            self.seen = set()

        async def set(self, key, value, *, nx, ex):
            if key in self.seen:
                return None
            self.seen.add(key)
            return True

    app.state.stream_replay_store = RedisStreamReplay(client=Redis())
    # End accepted admission at the existing budget gate, before speech construction.
    monkeypatch.setattr(pipeline.SpendLedger, "exhausted", lambda *_: True)
    capability = CallCapabilities(AUTH_TOKEN, ACCOUNT_SID).issue_stream(CALL_SID, None)
    start = {
        "streamSid": "MZ123",
        "accountSid": ACCOUNT_SID,
        "callSid": CALL_SID,
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
        "customParameters": {"capability": capability},
    }
    _expect_close(app, headers=_ws_headers(), start=start, code=1013)
    _expect_close(app, headers=_ws_headers(), start=start)
