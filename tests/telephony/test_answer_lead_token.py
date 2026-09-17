"""The lead token's trip through `/answer` — the only channel from trigger to conversation.

Vobiz's Call API takes four fields and none of them carries metadata, so a triggered call
hands Roma the lead by way of a token in the answer URL it dialled with. Vobiz echoes that
URL back when the callee picks up. If `/answer` drops the token here, the call still connects
and still sounds fine — Roma just knows nothing about the person she rang, silently. That is
precisely the failure shape this repo keeps producing, so it gets a test.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("VOBIZ_FROM_NUMBER", "+917971543192")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam_test")
    monkeypatch.setenv("OPENAI_API_KEY", "openai_test")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example")
    from roma.config import get_settings

    get_settings.cache_clear()
    from roma.telephony.media import build_media_app

    yield build_media_app(auto_hang_up=False)
    get_settings.cache_clear()


def _stream_url(xml: str) -> str:
    """The URL as Vobiz will see it — XML-UNESCAPED.

    `answer_xml` escapes the element text, so a second query parameter arrives on the wire as
    `?t=…&amp;lead=…`. That is correct XML and Vobiz's parser undoes it; a test that reads the
    raw text instead sees a parameter named `amp;lead` and fails for the wrong reason.
    """
    import html

    inner = xml.split("<Stream", 1)[1].split(">", 1)[1]
    return html.unescape(inner.split("</Stream>", 1)[0].strip())


def test_the_lead_token_is_carried_from_answer_into_the_stream_url(app):
    xml = TestClient(app).post("/answer?lead=LEADTOK123", data={"CallUUID": "CA_x"}).text
    q = parse_qs(urlparse(_stream_url(xml)).query)

    assert q["lead"] == ["LEADTOK123"], "lead token dropped between /answer and /ws"
    assert q["t"], "the stream auth token must still be minted alongside it"


def test_an_inbound_call_carries_no_lead_and_that_is_not_an_error(app):
    """No `?lead=` is the normal inbound case, not a degraded one."""
    xml = TestClient(app).post("/answer", data={"CallUUID": "CA_x"}).text
    q = parse_qs(urlparse(_stream_url(xml)).query)

    assert "lead" not in q
    assert q["t"]


def test_the_answer_xml_still_carries_keepcallalive_with_a_lead_token(app):
    """The regression that cost a live call. It must survive every new query parameter."""
    xml = TestClient(app).post("/answer?lead=LEADTOK123", data={"CallUUID": "CA_x"}).text
    assert 'keepCallAlive="true"' in xml
    assert 'bidirectional="true"' in xml
    assert "audio/x-mulaw;rate=8000" in xml


def test_the_lead_token_is_never_written_to_the_log(app, caplog):
    """The token authorises reading a lead's record — PII by proxy (docs/07).

    `logging_setup` redacts configured secrets; a per-call token cannot be pre-registered
    there, so the call site must not log it in the first place.
    """
    with caplog.at_level("INFO", logger="roma.telephony"):
        TestClient(app).post("/answer?lead=SECRETLEADTOKEN", data={"CallUUID": "CA_x"})
    assert "SECRETLEADTOKEN" not in caplog.text
    assert "lead=yes" in caplog.text, "presence of a lead should still be observable"


def test_a_lead_token_with_no_store_configured_does_not_break_the_call(app):
    """Redis is not running on the dev laptop; a triggered call must still connect."""
    import asyncio

    from roma.telephony.media import _load_triggered_lead

    app.state.lead_store = None
    assert asyncio.run(_load_triggered_lead(app, "tok")) is None
    assert asyncio.run(_load_triggered_lead(app, None)) is None


def test_a_failing_lead_store_degrades_instead_of_dropping_the_call(app):
    import asyncio

    from roma.telephony.media import _load_triggered_lead

    class _Broken:
        async def get(self, token):
            raise RuntimeError("redis is down")

    app.state.lead_store = _Broken()
    assert asyncio.run(_load_triggered_lead(app, "tok")) is None
