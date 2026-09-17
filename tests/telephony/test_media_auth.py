"""Who may open `/ws`, and the `/answer` XML that authorises them.

On Twilio the socket was gated by comparing `accountSid` against `TWILIO_ACCOUNT_SID` — one
line, and the only authentication the endpoint had. Vobiz's `start` carries no account field,
so that check has no analogue and could not simply be deleted: `/ws` answers with a live
microphone feed of a lead's conversation.

The replacement mints a single-use token when serving `/answer` and requires it back on the
socket. See `telephony/streamauth.py` for why that was chosen over correlating on `callId`.

The reject paths close before any Pipecat transport is built, so these stay lightweight.
"""

import json
import re

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN are deliberately NOT here. They are Vobiz's account REST
# credentials, this account does not have them, and nothing on the inbound path needs them:
# Vobiz authenticates its own trunk and connects to us. Setting them in the scaffolding would
# hide a dependency on them rather than prove there is none. Do not add them back.
REQUIRED_ENV = {
    "VOBIZ_FROM_NUMBER": "+917971543192",
    "SARVAM_API_KEY": "sarvam_test",
    "OPENAI_API_KEY": "openai_test",
    "REDIS_URL": "redis://localhost:6379/0",
}


@pytest.fixture
def app(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    from roma.config import get_settings

    get_settings.cache_clear()
    from roma.telephony.media import build_media_app

    yield build_media_app(auto_hang_up=False)
    get_settings.cache_clear()


def _ws_path(app, call_id="CA_test"):
    """Do what Vobiz does: fetch the answer XML and read the socket URL out of it."""
    xml = TestClient(app).post("/answer", data={"CallUUID": call_id}).text
    return re.search(r"wss://[^<\s]+", xml).group(0).split("localhost:8020", 1)[-1]


def _expect_close(app, path, start_payload=None, code=1008):
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            if start_payload is not None:
                ws.send_text(json.dumps({"event": "start", "start": start_payload}))
            ws.receive_text()
    assert exc.value.code == code


def test_rejects_a_socket_with_no_token(app):
    """The bare URL is not enough. It appears in logs, in the answer XML, and in Vobiz's own
    records; on its own it must open nothing."""
    _expect_close(app, "/ws")


def test_rejects_a_forged_token(app):
    _expect_close(app, "/ws?t=not-a-real-token")


def test_a_token_is_single_use(app):
    """A URL captured from a log is worthless once the call it belonged to has connected."""
    path = _ws_path(app)
    client = TestClient(app)
    with client.websocket_connect(path) as ws:
        ws.send_text(
            json.dumps({"event": "start", "start": {"streamId": "ST", "callId": "CA_test"}})
        )
    _expect_close(app, path)  # same token, second connection


def test_rejects_start_without_a_stream_id(app):
    """Authorised, but unusable: without a stream id every outbound `playAudio` would be
    unaddressed and silently dropped by Vobiz."""
    _expect_close(app, _ws_path(app), {"callId": "CA_test"})


def test_answer_returns_a_bidirectional_stream(app):
    """`bidirectional="true"` is the whole point — a one-way fork sends us the lead's audio
    and plays nothing back, which is a voice agent that cannot speak."""
    xml = TestClient(app).post("/answer", data={"CallUUID": "CA_x"}).text
    assert 'bidirectional="true"' in xml
    assert "<Stream" in xml and "wss://" in xml
    assert "audio/x-mulaw" in xml


def test_answer_keeps_the_call_alive_for_the_stream(app):
    """This asserted the OPPOSITE and was wrong in a way no offline test could catch.

    The reasoning was that Roma's stream IS the call, so letting the carrier tear down when
    the socket closes would avoid needing a REST hang-up. But without `keepCallAlive` Vobiz
    does not wait for the stream at all: on the first live call it answered at 19:05:00,
    fetched this XML, never opened the WebSocket, and hung up at 19:05:02. Vobiz's documented
    example carries the attribute; it should have been copied verbatim."""
    xml = TestClient(app).post("/answer", data={"CallUUID": "CA_x"}).text
    assert 'keepCallAlive="true"' in xml


def test_each_answer_mints_a_distinct_token(app):
    """Two concurrent calls must not be able to redeem each other's socket."""
    a, b = _ws_path(app, "CA_1"), _ws_path(app, "CA_2")
    assert a != b


def test_the_inbound_path_works_with_the_rest_credentials_explicitly_blank(monkeypatch):
    """Answering and streaming must not need VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN.

    Stronger than simply leaving them out of the scaffolding: `Settings` also reads `.env`, so
    on a machine that HAS them the other tests here would pass without proving anything.

    The first attempt at that used `setenv("VOBIZ_AUTH_ID", "")` and did not work — with
    `env_ignore_empty=True` an empty variable is IGNORED, so the lookup falls through to
    `.env` and picks the real value up again. It only looked correct while `.env` held blanks.
    Cutting the file out of the load is the version that actually isolates."""
    from roma.config import Settings, get_settings

    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("VOBIZ_AUTH_ID", raising=False)
    monkeypatch.delenv("VOBIZ_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("roma.config.Settings", lambda **kw: Settings(_env_file=None, **kw))

    get_settings.cache_clear()
    try:
        assert get_settings().vobiz_auth_id.get_secret_value() == ""
        from roma.telephony.media import build_media_app

        built = build_media_app(auto_hang_up=False)
        path = _ws_path(built)
        with TestClient(built).websocket_connect(path) as ws:
            ws.send_text(
                json.dumps({"event": "start", "start": {"streamId": "ST", "callId": "CA"}})
            )
    finally:
        get_settings.cache_clear()


def test_health_reports_ok(app):
    """The runbook has curled this since before it existed (docs/12); it used to 404 and
    'proved' the tunnel worked only by accident."""
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"


def test_an_inbound_call_is_refused_when_the_testing_budget_is_spent(app, monkeypatch):
    """Gate 0's money check lives in the DIAL path (`dialer/precall.py`), and inbound never
    dials — so until this existed the cap was measured at teardown and enforced nowhere.

    That was survivable while "test handsets only" bounded who could be reached. It is not
    survivable inbound: anyone who has the number can ring it, as many times as they like,
    and every call spends Sarvam and OpenAI credit.

    Answer time is the one clean place to refuse. precall's docstring rules out an in-call
    budget check because it "would have to hang up on a lead mid-sentence" — an objection
    that does not apply before the pipeline exists and nothing has been spent."""
    from roma.spend import SpendLedger

    monkeypatch.setattr(SpendLedger, "exhausted", lambda self, budget: True)
    _expect_close(app, _ws_path(app), {"streamId": "ST", "callId": "CA_broke"}, code=1013)


def test_a_call_within_budget_is_not_refused(app, monkeypatch):
    """The counterpart: the guard must not close every socket if the ledger reads clean."""
    from roma.spend import SpendLedger

    monkeypatch.setattr(SpendLedger, "exhausted", lambda self, budget: False)
    path = _ws_path(app)
    with TestClient(app).websocket_connect(path) as ws:
        ws.send_text(
            json.dumps({"event": "start", "start": {"streamId": "ST", "callId": "CA"}})
        )
