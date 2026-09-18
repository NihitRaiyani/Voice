"""Twilio HTTP and WebSocket callbacks must be signed and account-bound."""

import json
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

    yield build_media_app(auto_hang_up=False)
    get_settings.cache_clear()


def _answer(app, *, signature=True, query=""):
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    public_url = f"https://host.example/answer{('?' + query) if query else ''}"
    headers = {}
    if signature:
        headers["X-Twilio-Signature"] = RequestValidator(
            AUTH_TOKEN
        ).compute_signature(public_url, params)
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
