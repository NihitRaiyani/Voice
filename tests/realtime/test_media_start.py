"""Twilio's start event identifies the call, stream, format, and custom parameters."""

import asyncio
import json

import pytest
from roma.realtime.pipeline import _read_start


class _FakeWebSocket:
    def __init__(self, messages):
        self._it = iter(messages)

    async def receive_text(self):
        return next(self._it)


def _start(**start_fields):
    stream_sid = start_fields.get("streamSid")
    return json.dumps({"event": "start", "streamSid": stream_sid, "start": start_fields})


def test_read_start_extracts_twilio_identity_format_and_lead():
    ws = _FakeWebSocket(
        [
            _start(
                streamSid="MZ123",
                accountSid="AC123",
                callSid="CA123",
                mediaFormat={
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
                customParameters={"lead": "lead-token"},
            )
        ]
    )
    start = asyncio.run(_read_start(ws))
    assert start.stream_id == "MZ123"
    assert start.account_id == "AC123"
    assert start.call_id == "CA123"
    assert start.encoding == "audio/x-mulaw"
    assert start.sample_rate == 8000
    assert start.custom_parameters == {"lead": "lead-token"}


def test_read_start_skips_leading_non_start_events_and_noise():
    ws = _FakeWebSocket(
        ["not json", json.dumps({"event": "connected"}), _start(streamSid="MZ2")]
    )
    assert asyncio.run(_read_start(ws)).stream_id == "MZ2"


def test_start_without_optional_fields_still_parses():
    start = asyncio.run(_read_start(_FakeWebSocket([_start(streamSid="MZ4")])))
    assert start.call_id is None
    assert start.account_id is None
    assert start.encoding is None
    assert start.sample_rate is None
    assert start.custom_parameters == {}


def test_top_level_stream_sid_is_accepted():
    message = json.dumps({"event": "start", "streamSid": "MZ5", "start": {}})
    assert asyncio.run(_read_start(_FakeWebSocket([message]))).stream_id == "MZ5"


def test_read_start_rejects_start_without_stream_sid():
    with pytest.raises(ValueError, match="missing streamSid"):
        asyncio.run(_read_start(_FakeWebSocket([_start(callSid="CA")])))


def test_read_start_rejects_when_start_never_arrives():
    messages = [json.dumps({"event": "media"}) for _ in range(50)]
    with pytest.raises(ValueError, match="within prelude bound"):
        asyncio.run(_read_start(_FakeWebSocket(messages)))


@pytest.mark.parametrize(
    "fields",
    [
        {"streamSid": "x" * 65},
        {"streamSid": "MZ1", "accountSid": []},
        {"streamSid": "MZ1", "customParameters": {"capability": 1}},
        {"streamSid": "MZ1", "customParameters": {"capability": "x" * 1025}},
        {"streamSid": "MZ1", "customParameters": {str(i): "x" for i in range(9)}},
        {"streamSid": "MZ1", "mediaFormat": "invalid"},
    ],
)
def test_read_start_rejects_invalid_bounded_fields(fields):
    with pytest.raises(ValueError):
        asyncio.run(_read_start(_FakeWebSocket([_start(**fields)])))


def test_prelude_byte_bound_is_enforced_before_parsing():
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(_read_start(_FakeWebSocket([" " * 8193])))


def test_prelude_deadline_covers_all_leading_messages(monkeypatch):
    import roma.realtime.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_PRELUDE_TIMEOUT_SECS", 0.03)

    class SlowWebSocket:
        async def receive_text(self):
            await asyncio.sleep(0.02)
            return '{"event":"connected"}'

    with pytest.raises(ValueError, match="timed out"):
        asyncio.run(_read_start(SlowWebSocket()))


def test_binary_prelude_frame_is_a_rejection():
    class BinaryWebSocket:
        async def receive_text(self):
            raise KeyError("text")

    with pytest.raises(ValueError, match="invalid stream prelude frame"):
        asyncio.run(_read_start(BinaryWebSocket()))
