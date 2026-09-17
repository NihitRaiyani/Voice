# Twilio Backend Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace VoBiz/SIP with Twilio Programmable Voice and remove the React/Vite frontend while preserving Roma's existing backend voice pipeline and safety behavior.

**Architecture:** Twilio's Calls API invokes the existing `/answer` webhook, which returns bidirectional `<Connect><Stream>` TwiML. Twilio sends the call to `/ws`, where the app validates the Twilio signature, parses Twilio's `start` event and custom lead parameter, and hands audio to Pipecat's native `TwilioFrameSerializer`; every downstream processor remains unchanged. The protected backend call API stays, while browser-only CORS, configuration, startup code, documentation, and assets are removed.

**Tech Stack:** Python 3.11+, FastAPI, Twilio Python SDK, Twilio Programmable Voice/Media Streams, Pipecat, pytest, Ruff, uv.

---

## File structure

**Create**

- `src/roma/telephony/twiml.py` — construct bidirectional TwiML and attach Stream custom parameters.
- `src/roma/telephony/twilio_auth.py` — build public callback URLs and validate Twilio signatures.
- `tests/telephony/test_twiml.py` — pin TwiML structure and escaping.
- `tests/telephony/test_twilio_auth.py` — pin valid/invalid HTTP and WebSocket signatures.
- `tests/telephony/test_twilio_serializer.py` — pin the small Pipecat/Twilio protocol contract Roma depends on.

**Modify**

- `pyproject.toml`, `uv.lock` — add the official Twilio SDK.
- `.env.example`, `src/roma/config.py`, `src/roma/logging_setup.py`, `tests/test_config.py`, `tests/test_logging.py`, `tests/test_logging_lead_token.py` — replace VoBiz/SIP settings with Twilio settings and redaction.
- `src/roma/telephony/dialer.py`, `src/roma/dialer/trigger.py`, `src/roma/telephony/webapi.py`, `scripts/place_test_call.py` and their tests — use Twilio's Calls API while retaining all gates and dependency injection.
- `src/roma/telephony/media.py`, `scripts/serve_media.py`, and media tests — validate Twilio, parse Twilio start events/custom parameters, and use `TwilioFrameSerializer`.
- `scripts/start_roma.sh`, `scripts/verify_media.py` — run and verify the backend-only Twilio service.
- `README.md`, `CLAUDE.md`, `HANDOFF.md`, current `docs/*.md`, `skills/README.md`, and affected source/test comments — describe the active Twilio backend accurately.

**Delete**

- `src/roma/telephony/vobiz.py`, `src/roma/telephony/answer.py`, `src/roma/telephony/streamauth.py` — superseded carrier code.
- `tests/telephony/test_vobiz_serializer.py`, `tests/telephony/test_streamauth.py` — superseded protocol tests.
- `scripts/rtt_mumbai.py` — VoBiz-edge probe that does not measure Twilio Media Streams.
- `docs/13-web-ui.md` and `web/` — frontend-only documentation and React/Vite application.

## Constraints

- Do not change VAD, STT, LLM, TTS, controller, guardrail, recording, or post-call behavior.
- Do not place a live call during implementation.
- Do not write the user's test destination into tracked files.
- Do not print, log, commit, or paste the supplied Twilio credentials.
- Preserve `var/roma` spend/recording/job data. Only the obsolete `var/run/vite.log` may be moved out during cleanup.

---

### Task 1: Replace carrier configuration and add the Twilio dependency

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_logging.py`
- Modify: `tests/test_logging_lead_token.py`
- Modify: `src/roma/config.py`
- Modify: `src/roma/logging_setup.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Rewrite configuration tests to name the Twilio contract**

Change `REQUIRED` so it contains only `SARVAM_API_KEY`, `OPENAI_API_KEY`, and `REDIS_URL`,
and replace `OPTIONAL_API_CREDENTIALS` with all three Twilio keys. Then replace the VoBiz
field assertions with these cases, using the file's existing `_set_full_env` helper:

```python
@pytest.mark.parametrize(
    "key",
    ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"],
)
def test_twilio_credentials_are_optional_at_server_startup(monkeypatch, key):
    _set_full_env(monkeypatch)
    monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert getattr(settings, key.lower()).get_secret_value() == ""


def test_dialing_without_twilio_credentials_fails_with_a_useful_message(monkeypatch):
    from roma.config import Settings, get_settings
    from roma.telephony.dialer import build_twilio_client

    _set_full_env(monkeypatch)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)
    monkeypatch.setattr("roma.config.Settings", lambda **kw: Settings(_env_file=None, **kw))
    get_settings.cache_clear()
    try:
        with pytest.raises(
            RuntimeError,
            match="TWILIO_ACCOUNT_SID.*TWILIO_AUTH_TOKEN.*TWILIO_FROM_NUMBER",
        ):
            build_twilio_client()
    finally:
        get_settings.cache_clear()
```

Update logging fixtures to use Twilio-shaped fake values and assert that the Account SID, Auth Token, and caller number are all redacted.

- [ ] **Step 2: Run the focused tests and verify the old configuration fails them**

Run:

```bash
uv run --extra dev pytest tests/test_config.py tests/test_logging.py tests/test_logging_lead_token.py -q
```

Expected: FAIL because `Settings` and `logging_setup` still expose `vobiz_*`, and `build_twilio_client` does not exist.

- [ ] **Step 3: Replace the Settings fields**

Use optional-at-boot secrets so inbound-independent imports and offline tests remain usable; outbound client construction is the fail-fast boundary:

```python
# --- Twilio -------------------------------------------------------------------------
twilio_account_sid: SecretStr = SecretStr("")
twilio_auth_token: SecretStr = SecretStr("")
twilio_from_number: SecretStr = SecretStr("")
```

Delete `vobiz_auth_id`, `vobiz_auth_token`, `vobiz_from_number`, and all `vobiz_trunk_*`/`vobiz_sip_*` fields. Retain `public_base_url`, but rewrite its documentation and error text to say Twilio reaches `/answer` and `/ws`.

- [ ] **Step 4: Replace the example environment and redaction set**

The tracked example begins with names only:

```dotenv
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
SARVAM_API_KEY=
OPENAI_API_KEY=
REDIS_URL=
```

In `_secret_values`, return Twilio values instead of VoBiz/SIP values:

```python
return [
    settings.twilio_account_sid.get_secret_value(),
    settings.twilio_auth_token.get_secret_value(),
    settings.twilio_from_number.get_secret_value(),
    settings.sarvam_api_key.get_secret_value(),
    settings.openai_api_key.get_secret_value(),
    settings.redis_url.get_secret_value(),
]
```

- [ ] **Step 5: Add and lock the official Twilio SDK**

Add this to the `telephony` optional dependency list:

```toml
"twilio>=9.10.9",
```

Run:

```bash
uv lock
uv sync --extra telephony --extra dev
```

Expected: `uv.lock` contains a `twilio` package and `.venv/bin/python` can import `twilio`.

- [ ] **Step 6: Run the focused tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/test_config.py tests/test_logging.py tests/test_logging_lead_token.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .env.example src/roma/config.py src/roma/logging_setup.py tests/test_config.py tests/test_logging.py tests/test_logging_lead_token.py
git commit -m "refactor: replace VoBiz configuration with Twilio"
```

---

### Task 2: Add TwiML generation and Twilio request validation

**Files:**
- Create: `tests/telephony/test_twiml.py`
- Create: `tests/telephony/test_twilio_auth.py`
- Create: `src/roma/telephony/twiml.py`
- Create: `src/roma/telephony/twilio_auth.py`
- Delete later in Task 5: `src/roma/telephony/answer.py`

- [ ] **Step 1: Write failing TwiML tests**

```python
from xml.etree import ElementTree

from roma.telephony.twiml import connect_stream_twiml


def test_connect_stream_twiml_is_bidirectional():
    xml = connect_stream_twiml("wss://voice.example/ws")
    root = ElementTree.fromstring(xml)
    stream = root.find("./Connect/Stream")
    assert stream is not None
    assert stream.attrib == {"url": "wss://voice.example/ws"}


def test_lead_token_is_a_custom_parameter_not_a_query_string():
    xml = connect_stream_twiml("wss://voice.example/ws", lead_token="lead<&token")
    root = ElementTree.fromstring(xml)
    stream = root.find("./Connect/Stream")
    parameter = root.find("./Connect/Stream/Parameter")
    assert stream.attrib["url"] == "wss://voice.example/ws"
    assert "?" not in stream.attrib["url"]
    assert parameter.attrib == {"name": "lead", "value": "lead<&token"}
```

- [ ] **Step 2: Write failing signature helper tests**

```python
from twilio.request_validator import RequestValidator

from roma.telephony.twilio_auth import external_url, valid_twilio_signature


def test_external_url_rebuilds_the_public_http_and_wss_urls():
    assert external_url("https://voice.example", "/answer", "lead=abc") == (
        "https://voice.example/answer?lead=abc"
    )
    assert external_url("https://voice.example", "/ws", websocket=True) == (
        "wss://voice.example/ws"
    )


def test_valid_signature_is_accepted_and_tampering_is_rejected():
    token = "test-auth-token"
    url = "https://voice.example/answer?lead=abc"
    params = {"AccountSid": "AC" + "1" * 32, "CallSid": "CA" + "2" * 32}
    signature = RequestValidator(token).compute_signature(url, params)
    assert valid_twilio_signature(url, params, signature, token)
    assert not valid_twilio_signature(url, {**params, "CallSid": "tampered"}, signature, token)
    assert not valid_twilio_signature(url, params, "", token)
```

- [ ] **Step 3: Run tests and verify imports fail**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_twiml.py tests/telephony/test_twilio_auth.py -q
```

Expected: collection FAIL because `twiml.py` and `twilio_auth.py` do not exist.

- [ ] **Step 4: Implement the minimal TwiML builder**

```python
"""Twilio instructions for a bidirectional Media Stream."""

from twilio.twiml.voice_response import VoiceResponse


def connect_stream_twiml(wss_url: str, *, lead_token: str | None = None) -> str:
    response = VoiceResponse()
    stream = response.connect().stream(url=wss_url)
    if lead_token:
        stream.parameter(name="lead", value=lead_token)
    return str(response)


__all__ = ["connect_stream_twiml"]
```

- [ ] **Step 5: Implement framework-independent signature helpers**

```python
"""Twilio callback URL reconstruction and signature verification."""

from twilio.request_validator import RequestValidator


def external_url(
    public_base_url: str,
    path: str,
    query: str = "",
    *,
    websocket: bool = False,
) -> str:
    base = public_base_url.rstrip("/")
    if websocket:
        base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    url = f"{base}/{path.lstrip('/')}"
    return f"{url}?{query}" if query else url


def valid_twilio_signature(
    url: str,
    params: dict[str, str],
    signature: str,
    auth_token: str,
) -> bool:
    if not signature or not auth_token:
        return False
    return RequestValidator(auth_token).validate(url, params, signature)


__all__ = ["external_url", "valid_twilio_signature"]
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_twiml.py tests/telephony/test_twilio_auth.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/roma/telephony/twiml.py src/roma/telephony/twilio_auth.py tests/telephony/test_twiml.py tests/telephony/test_twilio_auth.py
git commit -m "feat: add Twilio TwiML and request validation"
```

---

### Task 3: Replace VoBiz outbound dialing with Twilio

**Files:**
- Modify: `tests/telephony/test_dialer.py`
- Modify: `tests/dialer/test_outbound_trigger.py`
- Modify: `tests/telephony/test_api_call.py`
- Modify: `tests/telephony/test_redis_midcall.py`
- Modify: `src/roma/telephony/dialer.py`
- Modify: `src/roma/dialer/trigger.py`
- Modify: `src/roma/telephony/webapi.py`
- Modify: `scripts/place_test_call.py`

- [ ] **Step 1: Rewrite dialer tests around Twilio's Calls API**

Use a mock with the same shape as the official SDK:

```python
class FakeCalls:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(sid="CA" + "1" * 32)


class FakeTwilio:
    def __init__(self):
        self.calls = FakeCalls()


def test_may_dial_places_one_call_pointing_at_the_answer_url():
    client = FakeTwilio()
    result = place_call(
        PHONE,
        ALLOWED_TIME,
        StubRegistry(consented={PHONE}),
        client,
        "+16295550100",
        "https://voice.example/answer",
    )
    assert result.call_sid == "CA" + "1" * 32
    assert client.calls.created == [
        {
            "to": PHONE,
            "from_": "+16295550100",
            "url": "https://voice.example/answer",
            "method": "POST",
        }
    ]
```

Update trigger tests to assert the lead is stored before `client.calls.create`, the URL contains `?lead=...`, and the returned `request_uuid` is the Twilio Call SID.

- [ ] **Step 2: Run the focused tests and verify they fail against VoBiz**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_dialer.py tests/dialer/test_outbound_trigger.py tests/telephony/test_api_call.py tests/telephony/test_redis_midcall.py -q
```

Expected: FAIL because production calls `client.create_call` and imports `build_vobiz_client`.

- [ ] **Step 3: Implement the Twilio dialer**

Replace the VoBiz REST client with the official SDK boundary:

```python
def _created_call_sid(call) -> str | None:
    value = getattr(call, "sid", None)
    return str(value) if value else None


def place_call(...):
    verdict = precall_check(phone, now, registry, spend=spend, budget_inr=budget_inr)
    if not verdict.may_dial:
        return DialResult(False, None, verdict.reason, None)
    call = client.calls.create(
        to=phone,
        from_=from_number,
        url=answer_url,
        method="POST",
    )
    call_sid = _created_call_sid(call)
    _log.info("outbound call placed: call_sid=%s", call_sid)
    return DialResult(True, call_sid, "ok", verdict.consent_line)


def build_twilio_client():
    from twilio.rest import Client

    from roma.config import get_settings

    settings = get_settings()
    values = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid.get_secret_value(),
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token.get_secret_value(),
        "TWILIO_FROM_NUMBER": settings.twilio_from_number.get_secret_value(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} are required to place a Twilio call")
    return Client(values["TWILIO_ACCOUNT_SID"], values["TWILIO_AUTH_TOKEN"])
```

- [ ] **Step 4: Adapt `trigger_outbound_call` without weakening ordering**

After storing the lead and pre-rendering the opener, call:

```python
call = client.calls.create(
    to=lead.phone,
    from_=from_number,
    url=answer_url,
    method="POST",
)
request_uuid = str(call.sid)
return TriggeredCall(request_uuid=request_uuid, lead_token=token)
```

Keep the existing Redis-write-before-carrier-call guarantee and `DialPrereqError` behavior.

- [ ] **Step 5: Rewire the backend API and CLI**

In `webapi.py` and `place_test_call.py`:

```python
from roma.telephony.dialer import build_twilio_client

client = build_twilio_client()
from_number = settings.twilio_from_number.get_secret_value()
```

Keep `/api/call`, bearer auth, denylist, window, budget, hourly cap, preflight, lead store, opener render, and sanitized `502 carrier refused (...)` behavior unchanged. Remove only CORS in Task 6.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_dialer.py tests/dialer/test_outbound_trigger.py tests/telephony/test_api_call.py tests/telephony/test_redis_midcall.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/roma/telephony/dialer.py src/roma/dialer/trigger.py src/roma/telephony/webapi.py scripts/place_test_call.py tests/telephony/test_dialer.py tests/dialer/test_outbound_trigger.py tests/telephony/test_api_call.py tests/telephony/test_redis_midcall.py
git commit -m "feat: place outbound calls through Twilio"
```

---

### Task 4: Migrate `/answer` and `/ws` to Twilio Media Streams

**Files:**
- Modify: `tests/telephony/test_media_start.py`
- Modify: `tests/telephony/test_answer_lead_token.py`
- Modify: `tests/telephony/test_media_auth.py`
- Modify: `tests/telephony/test_media_isolation.py`
- Modify: `tests/telephony/test_media_stt.py`
- Modify: `tests/telephony/test_outbound_edge_cases.py`
- Modify: `src/roma/telephony/media.py`
- Modify: `scripts/serve_media.py`

- [ ] **Step 1: Rewrite start-event tests for Twilio field names and custom parameters**

```python
def test_read_start_extracts_twilio_identity_format_and_lead():
    ws = ScriptedWebSocket(
        {
            "event": "start",
            "streamSid": "MZ123",
            "start": {
                "streamSid": "MZ123",
                "accountSid": "AC123",
                "callSid": "CA123",
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
                "customParameters": {"lead": "lead-token"},
            },
        }
    )
    start = asyncio.run(_read_start(ws))
    assert start.stream_id == "MZ123"
    assert start.account_id == "AC123"
    assert start.call_id == "CA123"
    assert start.encoding == "audio/x-mulaw"
    assert start.sample_rate == 8000
    assert start.custom_parameters == {"lead": "lead-token"}
```

Retain timeout, leading-event, malformed JSON, and missing-stream-ID tests with Twilio names.

- [ ] **Step 2: Rewrite answer/auth tests**

For `/answer`, compute a valid signature with `RequestValidator`, post Twilio form fields, parse the returned TwiML, and assert the lead token is a `<Parameter>` rather than part of the Stream URL. Add explicit `403` tests for missing and invalid signatures.

For `/ws`, test the pure signature helper plus one route-level rejection before pipeline construction. Use `wss://testserver/ws` as the signed URL and `X-Twilio-Signature` as the header.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_media_start.py tests/telephony/test_answer_lead_token.py tests/telephony/test_media_auth.py -q
```

Expected: FAIL because the handler still expects VoBiz fields, query tokens, and `answer_xml`.

- [ ] **Step 4: Implement the Twilio `StreamStart` parser**

```python
@dataclass(frozen=True)
class StreamStart:
    stream_id: str
    account_id: str | None
    call_id: str | None
    encoding: str | None
    sample_rate: int | None
    custom_parameters: dict[str, str]


async def _read_start(websocket: WebSocket) -> StreamStart:
    for _ in range(_MAX_PRELUDE_MESSAGES):
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=_PRELUDE_TIMEOUT_SECS
        )
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict) and message.get("event") == "start":
            start = message.get("start") or {}
            stream_id = start.get("streamSid") or message.get("streamSid")
            if not stream_id:
                raise ValueError("Twilio start event missing streamSid")
            media_format = start.get("mediaFormat") or {}
            custom = start.get("customParameters") or {}
            return StreamStart(
                stream_id=str(stream_id),
                account_id=start.get("accountSid"),
                call_id=start.get("callSid"),
                encoding=media_format.get("encoding"),
                sample_rate=media_format.get("sampleRate"),
                custom_parameters={str(k): str(v) for k, v in custom.items()},
            )
    raise ValueError("no well-formed Twilio start event within prelude bound")
```

Keep the current timeout exception wrapping.

- [ ] **Step 5: Implement signed `/answer` TwiML**

```python
form = await request.form()
params = {str(key): str(value) for key, value in form.items()}
signature_url = external_url(
    settings.public_base_url,
    request.url.path,
    request.url.query,
)
if not valid_twilio_signature(
    signature_url,
    params,
    request.headers.get("x-twilio-signature", ""),
    settings.twilio_auth_token.get_secret_value(),
):
    raise HTTPException(status_code=403, detail="invalid Twilio signature")

call_sid = params.get("CallSid")
lead_token = lead_token_from_query(request.query_params)
wss_url = external_url(settings.public_base_url, "/ws", websocket=True)
await _mark_call_connected(app, lead_token)
return Response(
    content=connect_stream_twiml(wss_url, lead_token=lead_token),
    media_type="application/xml",
)
```

- [ ] **Step 6: Implement signed `/ws` and native serialization**

Before `websocket.accept()`:

```python
signature_url = external_url(
    settings.public_base_url,
    websocket.url.path,
    websocket.url.query,
    websocket=True,
)
if not valid_twilio_signature(
    signature_url,
    {},
    websocket.headers.get("x-twilio-signature", ""),
    settings.twilio_auth_token.get_secret_value(),
):
    await websocket.close(code=1008)
    return
await websocket.accept()
```

After `_read_start`:

```python
if start.account_id != settings.twilio_account_sid.get_secret_value():
    await websocket.close(code=1008)
    return

stream_sid = start.stream_id
call_sid = start.call_id
lead_token = start.custom_parameters.get("lead")
is_outbound = bool(lead_token)

serializer = TwilioFrameSerializer(
    stream_sid=stream_sid,
    call_sid=call_sid,
    account_sid=settings.twilio_account_sid.get_secret_value(),
    auth_token=settings.twilio_auth_token.get_secret_value(),
    params=TwilioFrameSerializer.InputParams(auto_hang_up=auto_hang_up),
)
```

Replace later `websocket.query_params.get("lead")` calls with `lead_token`. Delete `PendingStreams` initialization, mint/redeem logic, and VoBiz media-format mutation. Keep the remainder of the pipeline unchanged.

- [ ] **Step 7: Run the media tests**

Run:

```bash
uv run --extra telephony --extra dev pytest \
  tests/telephony/test_media_start.py \
  tests/telephony/test_answer_lead_token.py \
  tests/telephony/test_media_auth.py \
  tests/telephony/test_media_isolation.py \
  tests/telephony/test_media_stt.py \
  tests/telephony/test_outbound_edge_cases.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/roma/telephony/media.py scripts/serve_media.py tests/telephony/test_media_start.py tests/telephony/test_answer_lead_token.py tests/telephony/test_media_auth.py tests/telephony/test_media_isolation.py tests/telephony/test_media_stt.py tests/telephony/test_outbound_edge_cases.py
git commit -m "feat: receive Twilio bidirectional media streams"
```

---

### Task 5: Remove the custom VoBiz protocol and pin the Twilio serializer contract

**Files:**
- Create: `tests/telephony/test_twilio_serializer.py`
- Modify: `tests/telephony/test_barge_in_midsentence.py`
- Modify: `src/roma/telephony/__init__.py`
- Delete: `src/roma/telephony/vobiz.py`
- Delete: `src/roma/telephony/answer.py`
- Delete: `src/roma/telephony/streamauth.py`
- Delete: `tests/telephony/test_vobiz_serializer.py`
- Delete: `tests/telephony/test_streamauth.py`

- [ ] **Step 1: Add contract tests for the native serializer**

```python
import json

import pytest
from pipecat.frames.frames import InterruptionFrame, OutputAudioRawFrame
from pipecat.serializers.twilio import TwilioFrameSerializer


@pytest.mark.asyncio
async def test_output_audio_uses_twilio_media_envelope():
    serializer = TwilioFrameSerializer(
        stream_sid="MZ123",
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
    serializer._sample_rate = 8000
    message = json.loads(
        await serializer.serialize(
            OutputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1)
        )
    )
    assert message["event"] == "media"
    assert message["streamSid"] == "MZ123"
    assert message["media"]["payload"]


@pytest.mark.asyncio
async def test_interruption_clears_twilio_buffer():
    serializer = TwilioFrameSerializer(
        stream_sid="MZ123",
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
    assert json.loads(await serializer.serialize(InterruptionFrame())) == {
        "event": "clear",
        "streamSid": "MZ123",
    }
```

Use public setup APIs where the locked Pipecat version exposes them; do not copy the serializer implementation into Roma.

- [ ] **Step 2: Run the new serializer tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_twilio_serializer.py -q
```

Expected: PASS against Pipecat's native serializer after any test setup adjustment required by the locked version.

- [ ] **Step 3: Convert the barge-in wire assertions**

Replace `VobizFrameSerializer`, `playAudio`, and `clearAudio` with `TwilioFrameSerializer`, `media`, and `clear`. Keep the behavioral assertion: a mid-sentence interruption must emit `clear` before later audio.

- [ ] **Step 4: Delete superseded modules and tests**

Run:

```bash
git rm src/roma/telephony/vobiz.py src/roma/telephony/answer.py src/roma/telephony/streamauth.py
git rm tests/telephony/test_vobiz_serializer.py tests/telephony/test_streamauth.py
```

Update `src/roma/telephony/__init__.py` to export `connect_stream_twiml` instead of `answer_xml`.

- [ ] **Step 5: Run protocol and barge-in tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_twilio_serializer.py tests/telephony/test_barge_in_midsentence.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/roma/telephony tests/telephony/test_twilio_serializer.py tests/telephony/test_barge_in_midsentence.py
git commit -m "refactor: remove the VoBiz wire protocol"
```

---

### Task 6: Remove the React/Vite frontend but retain the backend API

**Files:**
- Modify: `tests/telephony/test_api_call.py`
- Modify: `src/roma/telephony/webapi.py`
- Modify: `src/roma/config.py`
- Modify: `.env.example`
- Modify: `scripts/start_roma.sh`
- Delete: `docs/13-web-ui.md`
- Delete: `web/.env.example`
- Delete: `web/.gitignore`
- Delete: `web/index.html`
- Delete: `web/package-lock.json`
- Delete: `web/package.json`
- Delete: `web/src/App.jsx`
- Delete: `web/src/main.jsx`
- Delete: `web/vite.config.js`

- [ ] **Step 1: Replace the CORS test with a backend-only assertion**

```python
def test_call_api_does_not_emit_browser_cors_headers(client):
    response = client.options(
        "/api/call",
        headers={"Origin": "http://localhost:3030", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers
```

- [ ] **Step 2: Run the assertion and verify the current middleware fails it**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_api_call.py::test_call_api_does_not_emit_browser_cors_headers -q
```

Expected: FAIL because `CORSMiddleware` emits the configured browser origin.

- [ ] **Step 3: Remove frontend coupling from the backend**

Delete the `CORSMiddleware` import and `app.add_middleware(...)` block. Delete `Settings.web_origin` and `WEB_ORIGIN` from `.env.example`. Keep `API_TOKEN`, but describe it as authentication for programmatic backend clients rather than a Vite-bundled browser value.

- [ ] **Step 4: Make the launcher backend-only**

From `scripts/start_roma.sh`, remove:

- `WEB_LOG`.
- Vite from `stop_all`.
- `web/.env` token matching.
- the entire web UI startup section.
- the instruction to open `http://localhost:3030`.

The final output becomes:

```bash
printf '\n\033[32mRoma backend is up.\033[0m\n'
printf '  health  %s/health\n  logs    %s\n  stop    ./scripts/start_roma.sh --stop\n\n' "$BASE_URL" "$RUN_DIR"
```

- [ ] **Step 5: Delete the frontend**

Run:

```bash
git rm -r web docs/13-web-ui.md
```

- [ ] **Step 6: Run backend API and configuration tests**

Run:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_api_call.py tests/test_config.py -q
bash -n scripts/start_roma.sh
```

Expected: tests PASS and shell syntax exits 0.

- [ ] **Step 7: Commit**

```bash
git add .env.example src/roma/config.py src/roma/telephony/webapi.py scripts/start_roma.sh tests/telephony/test_api_call.py
git commit -m "refactor: remove the frontend application"
```

---

### Task 7: Replace VoBiz verification and clean active documentation

**Files:**
- Modify: `scripts/verify_media.py`
- Delete: `scripts/rtt_mumbai.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `HANDOFF.md`
- Modify: `docs/01-architecture.md`
- Modify: `docs/02-pipeline.md`
- Modify: `docs/03-phase-machine.md`
- Modify: `docs/04-guardrails.md`
- Modify: `docs/05-endpointing-vad.md`
- Modify: `docs/07-security.md`
- Modify: `docs/08-concurrency.md`
- Modify: `docs/09-recording-storage.md`
- Modify: `docs/10-build-order.md`
- Modify: `docs/12-verification.md`
- Modify: `docs/decisions.md`
- Modify: `skills/README.md`
- Modify: affected source and test comments returned by the cleanup search

- [ ] **Step 1: Convert the synthetic media probe to Twilio**

The synthetic prelude must use Twilio's contract:

```python
start = {
    "event": "start",
    "sequenceNumber": "1",
    "streamSid": "MZ_synthetic",
    "start": {
        "accountSid": settings.twilio_account_sid.get_secret_value(),
        "callSid": "CA_synthetic",
        "streamSid": "MZ_synthetic",
        "mediaFormat": {
            "encoding": "audio/x-mulaw",
            "sampleRate": 8000,
            "channels": 1,
        },
        "customParameters": {},
    },
}
media = {
    "event": "media",
    "sequenceNumber": "2",
    "streamSid": "MZ_synthetic",
    "media": {"track": "inbound", "chunk": "1", "timestamp": "0", "payload": payload},
}
```

Compute the WebSocket handshake signature with `RequestValidator` for the exact `wss://.../ws` URL before opening the test socket. Continue to assert audio enters and exits the real transport without making a live call.

- [ ] **Step 2: Delete the misleading VoBiz RTT probe**

```bash
git rm scripts/rtt_mumbai.py
```

Do not replace it with a REST latency probe: `api.twilio.com` RTT does not measure the Media Streams audio path.

- [ ] **Step 3: Update operator documentation without executing the roadmap**

Document only the shipped migration:

- Architecture: `Twilio -> Pipecat -> Saaras -> OpenAI -> Bulbul -> Twilio`.
- Required environment: the three `TWILIO_*` variables plus existing backend variables.
- Inbound phone configuration: the Twilio number's Voice webhook points to `<PUBLIC_BASE_URL>/answer` using POST.
- Outbound: the backend API or `scripts/place_test_call.py` invokes Twilio and supplies the same answer URL.
- Media: `/answer` returns `<Connect><Stream>` and `/ws` receives signed Twilio Media Streams.
- Security: validate `X-Twilio-Signature`; never log tokens, phone numbers, or lead tokens.
- Frontend: removed; show a `curl` example for `POST /api/call` with `Authorization: Bearer ...`.
- Testing: offline suite first; live call only by explicit operator action.

Keep historical lessons in `docs/decisions.md` only when they still explain a current constraint; remove VoBiz credentials, endpoints, trunk identifiers, and instructions.

- [ ] **Step 4: Rewrite active source/test comments mechanically, then review each match**

Run:

```bash
rg -n -i --hidden -g '!.git/**' -g '!var/**' -g '!docs/superpowers/**' 'vobiz|VOBIZ_|vobiz_' .
rg -n --hidden -g '!.git/**' -g '!var/**' 'WEB_ORIGIN|VITE_|vite|React|browser UI|web UI' .
```

Expected after edits: no VoBiz match outside historical migration specs/plans, and no frontend match outside a sentence stating that the frontend was removed.

- [ ] **Step 5: Run the synthetic probe and documentation-linked checks**

Run:

```bash
uv run --extra telephony --extra dev python scripts/verify_media.py
uv run --extra telephony --extra dev pytest tests/telephony/test_media_start.py tests/telephony/test_twilio_serializer.py -q
```

Expected: the synthetic probe reports inbound and outbound audio success; tests PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md HANDOFF.md docs scripts src tests skills
git commit -m "docs: describe the Twilio backend service"
```

---

### Task 8: Configure local secrets, clean runtime frontend residue, and verify everything

**Files:**
- Modify locally but never stage: `.env`
- Move out of runtime tree if present: `var/run/vite.log`
- Verify all tracked project files

- [ ] **Step 1: Update the ignored local `.env` without printing values**

First prove it is ignored:

```bash
git check-ignore -v .env
```

Expected: `.gitignore` matches `.env`.

Replace all VoBiz/SIP keys in `.env` with the user-supplied `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER`. Preserve existing Sarvam, OpenAI, Redis, Google, runtime, and tuning values. Do not add the personal test destination.

Verify names only, never values:

```bash
awk -F= '/^(TWILIO_ACCOUNT_SID|TWILIO_AUTH_TOKEN|TWILIO_FROM_NUMBER)=/{print $1 "=set"}' .env
```

Expected: exactly three `=set` lines.

- [ ] **Step 2: Move obsolete frontend runtime output to a recoverable temporary location**

If present:

```bash
mkdir -p /private/tmp/voice-agent-frontend-runtime-backup
mv var/run/vite.log /private/tmp/voice-agent-frontend-runtime-backup/vite.log
```

Do not remove `var/roma`, recordings, post-call jobs, `spend.jsonl`, Uvicorn logs, or Cloudflare logs.

- [ ] **Step 3: Prove no credential is tracked**

Run checks using only field names and Git state:

```bash
git status --short
git ls-files .env
git diff -- .env
rg -n 'TWILIO_(ACCOUNT_SID|AUTH_TOKEN|FROM_NUMBER)=[^[:space:]]+' --glob '!*.md' --glob '!.env' .
```

Expected: `.env` is absent from tracked files/diff; the repository contains no populated Twilio assignment.

- [ ] **Step 4: Run lint and the complete offline suite**

Run:

```bash
uv run --extra telephony --extra dev ruff check src tests scripts
uv run --extra telephony --extra dev pytest -q
bash -n scripts/start_roma.sh
```

Expected: Ruff exits 0, pytest reports zero failures, and shell syntax exits 0.

- [ ] **Step 5: Verify removal and retained surfaces**

Run:

```bash
test ! -d web
test ! -e src/roma/telephony/vobiz.py
test ! -e src/roma/telephony/streamauth.py
test ! -e scripts/rtt_mumbai.py
rg -n 'TWILIO_ACCOUNT_SID|TwilioFrameSerializer|connect_stream_twiml' src scripts .env.example
rg -n '@app.post\("/api/call"\)|@app.get\("/api/call/\{request_uuid\}"\)' src/roma/telephony/webapi.py
git status --short --branch
```

Expected: deletion assertions exit 0, Twilio wiring and both backend API routes are present, and only intended changes exist.

- [ ] **Step 6: Record the verification result**

If Task 8 required tracked fixes after the Task 7 commit, commit only those fixes:

```bash
git status --short
git add -u
git commit -m "test: complete Twilio backend verification"
```

Use `git add -u` only after confirming `git status --short` contains exclusively the
verification fixes. If no tracked fix was required, do not create an empty commit.

- [ ] **Step 7: Security handoff**

Report that the pasted Twilio Auth Token must be rotated in the Twilio Console. After the user rotates it, update only `.env` and rerun:

```bash
uv run --extra telephony --extra dev pytest tests/telephony/test_twilio_auth.py tests/test_logging.py -q
```

Do not place a live call. Offer a separate, explicit live-call verification using the user-provided test destination.

---

## Final acceptance checklist

- [ ] Only Twilio carrier settings and APIs remain active.
- [ ] Twilio HTTP and WebSocket signatures are validated.
- [ ] Lead metadata travels through Twilio custom parameters, not Stream query parameters.
- [ ] Pipecat's native `TwilioFrameSerializer` handles audio and interruption clearing.
- [ ] The protected backend call API and status API remain.
- [ ] React/Vite, CORS, frontend configuration, startup, and documentation are removed.
- [ ] `var/roma` data is preserved.
- [ ] No credentials or personal test destination are tracked.
- [ ] Ruff, the full pytest suite, shell syntax, and synthetic media verification pass.
- [ ] No paid live call was placed.
