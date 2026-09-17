"""The Vobiz `start` event — the prelude that identifies the call and its audio format.

Vobiz opens the socket and sends `start` before any media. Everything downstream is keyed on
what this parses: the stream id goes on every outbound `playAudio`, the call id becomes the
recording filename / Redis key / spend row, and the media format decides how inbound audio is
decoded at all.
"""

import asyncio
import json

import pytest

from roma.telephony.media import _read_start


class _FakeWebSocket:
    """Feeds a scripted sequence of Vobiz `<Stream>` text messages."""

    def __init__(self, messages):
        self._it = iter(messages)

    async def receive_text(self):
        return next(self._it)


def _start(**start_fields):
    return json.dumps({"event": "start", "start": start_fields})


def test_read_start_extracts_the_stream_identity_and_media_format():
    messages = [
        _start(
            callId="CA_call",
            streamId="ST_stream",
            tracks=["inbound"],
            mediaFormat={"encoding": "audio/x-mulaw", "sampleRate": 8000},
        )
    ]
    start = asyncio.run(_read_start(_FakeWebSocket(messages)))
    assert start.stream_id == "ST_stream"
    assert start.call_id == "CA_call"
    assert start.encoding == "audio/x-mulaw"
    assert start.sample_rate == 8000


def test_the_declared_media_format_is_carried_even_when_it_is_not_mulaw():
    """Vobiz supports L16 at 8k and 16k as well as μ-law. The parser's job is to report what
    was declared, not to assume the format we asked for — the serializer decodes on it."""
    messages = [
        _start(
            callId="CA",
            streamId="ST",
            mediaFormat={"encoding": "audio/x-l16", "sampleRate": 16000},
        )
    ]
    start = asyncio.run(_read_start(_FakeWebSocket(messages)))
    assert (start.encoding, start.sample_rate) == ("audio/x-l16", 16000)


def test_read_start_skips_leading_non_start_events():
    messages = [
        json.dumps({"event": "connected"}),
        json.dumps({"event": "ping"}),
        _start(streamId="ST2"),
    ]
    start = asyncio.run(_read_start(_FakeWebSocket(messages)))
    assert start.stream_id == "ST2"
    assert start.call_id is None


def test_read_start_ignores_non_json_noise():
    messages = ["not json at all", _start(streamId="ST3")]
    assert asyncio.run(_read_start(_FakeWebSocket(messages))).stream_id == "ST3"


def test_a_start_with_no_media_format_still_parses():
    """The format block is how Vobiz *reports* the encoding; the serializer falls back to the
    μ-law/8k the `<Stream>` element asked for. A missing block is not a broken call."""
    start = asyncio.run(_read_start(_FakeWebSocket([_start(streamId="ST4", callId="CA4")])))
    assert start.stream_id == "ST4"
    assert start.encoding is None and start.sample_rate is None


def test_read_start_rejects_start_without_stream_id():
    """Without it, every outbound `playAudio` would be unaddressed and silently dropped."""
    with pytest.raises(ValueError, match="missing streamId"):
        asyncio.run(_read_start(_FakeWebSocket([_start(callId="CA")])))


def test_read_start_rejects_when_start_never_arrives():
    messages = [json.dumps({"event": "media"}) for _ in range(50)]
    with pytest.raises(ValueError, match="within prelude bound"):
        asyncio.run(_read_start(_FakeWebSocket(messages)))
