"""The Vobiz `<Stream>` wire format (roma.telephony.vobiz).

Vobiz is Twilio-shaped but not Twilio-compatible, and every difference here is one that fails
SILENTLY on a live call: a `playAudio` without `streamId` is dropped by the carrier and looks
exactly like Roma having nothing to say. With ~₹22 of budget left, these have to catch the
envelope offline.
"""

import asyncio
import base64
import json

from pipecat.frames.frames import (
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)

from roma.telephony.vobiz import VobizFrameSerializer

RATE = 8000
SILENCE_ULAW = bytes([0xFF] * 160)  # 20ms, the chunk size Vobiz documents


def _ser(**kwargs):
    s = VobizFrameSerializer(stream_id="ST_test", **kwargs)
    asyncio.run(s.setup(StartFrame(audio_in_sample_rate=RATE, audio_out_sample_rate=RATE)))
    return s


def _serialize(s, frame):
    out = asyncio.run(s.serialize(frame))
    return json.loads(out) if out else None


def _deserialize(s, message):
    return asyncio.run(s.deserialize(json.dumps(message)))


# --- outbound: playAudio -------------------------------------------------------


def test_audio_becomes_a_play_audio_event_addressed_to_the_stream():
    """`streamId` is not decoration. Vobiz drops an unaddressed playAudio, which on a live
    call is indistinguishable from silence — the failure mode this test exists to prevent."""
    s = _ser()
    msg = _serialize(
        s, OutputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=RATE, num_channels=1)
    )
    assert msg["event"] == "playAudio"
    assert msg["streamId"] == "ST_test"
    assert msg["media"]["contentType"] == "audio/x-mulaw"
    assert msg["media"]["sampleRate"] == RATE
    assert base64.b64decode(msg["media"]["payload"])


def test_the_outbound_event_is_play_audio_not_media():
    """Twilio's outbound envelope is `media`; Vobiz's is `playAudio`. Sending Twilio's would
    be accepted by the socket and ignored by the carrier."""
    s = _ser()
    msg = _serialize(
        s, OutputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=RATE, num_channels=1)
    )
    assert msg["event"] != "media"


# --- barge-in: clearAudio ------------------------------------------------------


def test_an_interruption_clears_the_carrier_buffer():
    """THE barge-in path, and the single most worked-on behaviour in this repo.

    When the lead starts talking, everything Roma has already queued AT VOBIZ has to go — not
    just what has not been generated yet. If this flush silently did not land, she would keep
    talking over the lead for however much audio the carrier still held, which is precisely
    the failure barge-in exists to prevent. Twilio spells this `clear`; Vobiz spells it
    `clearAudio`, and the wrong spelling fails quietly."""
    s = _ser()
    msg = _serialize(s, InterruptionFrame())
    assert msg == {"event": "clearAudio", "streamId": "ST_test"}


# --- inbound: media, decoded per the declared format ---------------------------


def test_inbound_mulaw_becomes_pcm_at_the_pipeline_rate():
    s = _ser()
    s.set_media_format("audio/x-mulaw", 8000)
    frame = _deserialize(
        s, {"event": "media", "media": {"payload": base64.b64encode(SILENCE_ULAW).decode()}}
    )
    assert frame is not None
    assert frame.sample_rate == RATE
    assert len(frame.audio) == len(SILENCE_ULAW) * 2  # μ-law byte -> PCM16


def test_the_declared_format_is_honoured_rather_than_assumed():
    """Vobiz reports the inbound encoding in `start.mediaFormat` and supports L16 as well as
    μ-law. Decoding L16 as μ-law produces noise at the right volume — audio that sounds like
    a bad line rather than a bug, which is how a codec mismatch survives a debugging session
    aimed at the model."""
    s = _ser()
    s.set_media_format("audio/x-l16", 8000)
    pcm = b"\x01\x02" * 160
    frame = _deserialize(
        s, {"event": "media", "media": {"payload": base64.b64encode(pcm).decode()}}
    )
    assert frame is not None
    # Passed through as PCM at the same rate, NOT expanded as though it were companded.
    assert len(frame.audio) == len(pcm)


def test_a_parameterised_content_type_is_understood():
    """Vobiz's `<Stream contentType>` uses "audio/x-l16;rate=16000"; the start event may echo
    that shape."""
    s = _ser()
    s.set_media_format("audio/x-l16;rate=16000", 16000)
    assert s._in_encoding == "audio/x-l16"
    assert s._in_sample_rate == 16000


def test_an_unknown_encoding_falls_back_and_says_so(caplog):
    s = _ser()
    with caplog.at_level("WARNING"):
        s.set_media_format("audio/x-opus", 8000)
    assert "unknown inbound encoding" in caplog.text
    assert s._in_encoding == "audio/x-mulaw"


def test_the_default_format_matches_what_the_answer_xml_requests():
    """The `<Stream>` element asks for μ-law/8000 and the serializer assumes the same when no
    format is declared. Two files, one number — if they drift, the first call is noise."""
    from roma.telephony.answer import INBOUND_CONTENT_TYPE
    from roma.telephony.vobiz import DEFAULT_ENCODING, DEFAULT_SAMPLE_RATE

    assert DEFAULT_ENCODING in INBOUND_CONTENT_TYPE
    assert str(DEFAULT_SAMPLE_RATE) in INBOUND_CONTENT_TYPE


# --- events that carry no audio ------------------------------------------------


def test_playback_acknowledgements_are_absorbed():
    """`playedStream` and `clearedAudio` acknowledge `checkpoint` and `clearAudio`. Nothing
    downstream waits on them — Roma's turn-taking runs off its own VAD and TTS frames — so
    they must not be turned into frames the pipeline would then have to ignore."""
    s = _ser()
    assert _deserialize(s, {"event": "playedStream", "name": "utt-1"}) is None
    assert _deserialize(s, {"event": "clearedAudio"}) is None


def test_malformed_input_never_raises():
    """This runs on the audio path of a live call. A crash here is a dropped call."""
    s = _ser()
    assert asyncio.run(s.deserialize("not json")) is None
    assert _deserialize(s, {"event": "media", "media": {}}) is None
    assert _deserialize(s, {"event": "media", "media": {"payload": "!!!not base64!!!"}}) is None


def test_hang_up_is_off_by_default():
    """The stream closing ends the call — `<Stream>` carries no `keepCallAlive`. Pipecat's
    Twilio serializer defaults this ON and hides a REST call inside what looks like a codec;
    that surprise is deliberately not re-inherited, and the endpoint is unverified anyway."""
    assert VobizFrameSerializer.InputParams().auto_hang_up is False
    _ser()  # constructing without credentials must not raise


def test_hang_up_without_credentials_degrades_instead_of_dropping_the_call(caplog):
    """The REST hang-up needs VOBIZ_AUTH_ID/TOKEN, which this account does not have. Asking
    for it anyway must disable the feature, not kill the call.

    This used to raise ValueError, on a "fail at construction, not at teardown" argument. That
    was wrong about where construction happens: the serializer is built PER CALL inside the
    `/ws` handler, so the raise dropped a live call — with a lead already on the line — over
    an optional extra. `<Stream>` omits `keepCallAlive`, so the call ends when the socket
    closes whether or not the REST hang-up ever fires."""
    with caplog.at_level("WARNING"):
        s = VobizFrameSerializer(
            stream_id="ST", params=VobizFrameSerializer.InputParams(auto_hang_up=True)
        )
    assert s._params.auto_hang_up is False
    assert "DISABLED" in caplog.text


def test_the_audio_path_works_with_no_rest_credentials():
    """The whole conversation — inbound audio, outbound audio, barge-in — must not touch the
    account API. Vobiz authenticates its own trunk and connects to us; nothing on this path
    has a credential to present."""
    s = _ser()
    assert _serialize(s, InterruptionFrame())["event"] == "clearAudio"
    assert (
        _serialize(
            s, OutputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=RATE, num_channels=1)
        )["event"]
        == "playAudio"
    )
    assert (
        _deserialize(
            s, {"event": "media", "media": {"payload": base64.b64encode(SILENCE_ULAW).decode()}}
        )
        is not None
    )


# --- audio-OUT is countable ----------------------------------------------------


def test_serializer_counts_what_it_writes_to_the_wire():
    """The teardown line must be able to say audio-OUT the way it says audio-IN.

    On b5273f93 the log proved the caller reached us (`inbound_frames=956`) and said
    NOTHING about the other direction. "Roma went silent" and "Roma spoke and Vobiz
    dropped it" produced identical logs, and they have opposite fixes. The count has to
    live in `serialize`, after `pcm_to_ulaw` and inside the `playAudio` branch: a frame
    processor sees frames, not wire bytes, and would have counted audio that never
    became a message.
    """
    s = _ser()
    pcm = b"\x00\x01" * 160  # 160 PCM16 samples @8k = 20ms -> 160 bytes of mu-law

    assert (s.play_frames, s.play_bytes, s.clear_events) == (0, 0, 0)

    for _ in range(3):
        message = _serialize(
            s, OutputAudioRawFrame(audio=pcm, sample_rate=RATE, num_channels=1)
        )
        assert message["event"] == "playAudio"

    assert s.play_frames == 3
    # Counted as mu-law, which is what Vobiz receives -- not the PCM16 that went in.
    assert s.play_bytes == 3 * len(base64.b64decode(message["media"]["payload"]))
    assert s.play_bytes < 3 * len(pcm)

    assert _serialize(s, InterruptionFrame())["event"] == "clearAudio"
    assert s.clear_events == 1
    assert s.play_frames == 3  # a barge-in is not playback


def test_empty_audio_is_not_counted_as_written():
    """`serialize` returns None for an empty payload; the counter must agree with the wire."""
    s = _ser()
    assert (
        _serialize(s, OutputAudioRawFrame(audio=b"", sample_rate=RATE, num_channels=1)) is None
    )
    assert s.play_frames == 0
    assert s.play_bytes == 0


def test_the_leads_own_audio_is_never_written_back_to_them():
    """THE echo. `serialize` matched `AudioRawFrame`, the BASE class — and
    `InputAudioRawFrame`, the lead's own voice arriving from the carrier, subclasses it. Every
    inbound frame that reached the serializer was re-encoded and returned as `playAudio`: a
    literal loopback of the caller into their own ear.

    Reported on every live call and dismissed three times, including by a check of mine that
    tested whether ROMA heard herself — the opposite direction. On call 172f24c5 the lead said
    it plainly: "मुझे मेरी आवाजें क्यों सुनाई दे रही हैं" — "why am I hearing my own voice?"
    """
    import asyncio

    from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

    from roma.telephony.vobiz import VobizFrameSerializer

    ser = VobizFrameSerializer("stream-1")
    asyncio.run(ser.setup(StartFrame(audio_in_sample_rate=8000, audio_out_sample_rate=8000)))
    pcm = b"\x00\x01" * 160

    lead = InputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    assert asyncio.run(ser.serialize(lead)) is None, "the lead's own audio was sent back"
    assert ser.play_frames == 0, "an inbound frame was counted as outbound audio"

    roma = OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    assert asyncio.run(ser.serialize(roma)) is not None, "Roma's audio stopped going out"
    assert ser.play_frames == 1
