"""Lead metadata travels from the signed answer URL into Twilio custom parameters."""

import asyncio
from xml.etree import ElementTree

import pytest
from starlette.testclient import TestClient
from twilio.request_validator import RequestValidator

AUTH_TOKEN = "test-auth-token"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "1" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+16295550100")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam_test")
    monkeypatch.setenv("OPENAI_API_KEY", "openai_test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example")
    from roma.core.config import get_settings

    get_settings.cache_clear()
    from roma.realtime.pipeline import build_media_app

    yield build_media_app(auto_hang_up=False)
    get_settings.cache_clear()


def _answer(app, query=""):
    params = {"CallSid": "CA" + "2" * 32, "AccountSid": "AC" + "1" * 32}
    url = f"https://host.example/answer{('?' + query) if query else ''}"
    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, params)
    path = f"/answer{('?' + query) if query else ''}"
    return TestClient(app).post(
        path, data=params, headers={"X-Twilio-Signature": signature}
    )


def test_the_lead_token_is_a_stream_custom_parameter(app):
    response = _answer(app, "lead=LEADTOK123")
    assert response.status_code == 200
    root = ElementTree.fromstring(response.text)
    stream = root.find("./Connect/Stream")
    parameter = root.find("./Connect/Stream/Parameter")
    assert stream is not None and stream.attrib == {"url": "wss://host.example/ws"}
    assert parameter is not None
    assert parameter.attrib == {"name": "lead", "value": "LEADTOK123"}


def test_an_inbound_call_carries_no_lead_and_that_is_not_an_error(app):
    root = ElementTree.fromstring(_answer(app).text)
    assert root.find("./Connect/Stream") is not None
    assert root.find("./Connect/Stream/Parameter") is None


def test_the_lead_token_is_never_written_to_the_log(app, caplog):
    with caplog.at_level("INFO", logger="roma.api.twilio"):
        _answer(app, "lead=SECRETLEADTOKEN")
    assert "SECRETLEADTOKEN" not in caplog.text
    assert "lead=yes" in caplog.text


def test_a_lead_token_with_no_store_configured_does_not_break_the_call(app):
    from roma.realtime.pipeline import _load_triggered_lead

    app.state.lead_store = None
    assert asyncio.run(_load_triggered_lead(app, "tok")) is None
    assert asyncio.run(_load_triggered_lead(app, None)) is None


def test_a_failing_lead_store_degrades_instead_of_dropping_the_call(app):
    from roma.realtime.pipeline import _load_triggered_lead

    class _Broken:
        async def get(self, token):
            raise RuntimeError("redis is down")

    app.state.lead_store = _Broken()
    assert asyncio.run(_load_triggered_lead(app, "tok")) is None
