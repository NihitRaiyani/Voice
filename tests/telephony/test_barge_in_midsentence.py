"""Barge-in cuts Roma mid-sentence, and the carrier is told to drop what it already has.

THE test docs/05 Layer 3 always needed and never had. Ten tests in
`test_media_interruption.py` cover the pieces — the filter still substitutes, the sanitizer
drops its half-sample — and none of them put audio ON THE WIRE and then interrupt it. So
"barge-in works" rested on three unit tests and an assumption about how they compose.

It is written against the real `FastAPIWebsocketTransport` output rather than the serializer
alone, because the serializer cannot fail this test: it translates whatever frame it is
handed and has no opinion about whether playback stopped. The thing that stops playback is
the output transport cancelling its audio task on `InterruptionFrame`. Asserting on the
serializer would have been a tautology that passed on a broken pipeline -- which is exactly
how the flat `VAD_STOP_SECS` and the dropped `vad_analyzer` survived their own test suites.

What must hold, from docs/05 Layer 3:
  1. `clear` reaches the wire, carrying the live `streamSid` (step 3).
  2. Audio queued behind the interruption never becomes `media` (steps 1-2).
  3. The cut lands MID-utterance -- strictly less of Roma's audio out than went in.

**Count Roma's audio, never raw `media` bytes.** The output transport streams
CONTINUOUSLY, padding the gaps with silence, so the wire carries ~5x more bytes than Roma
ever generated and keeps carrying them after a barge-in. A byte-total assertion reads that
padding as speech and a "did playback stop?" test passes on a pipeline that never stopped.
Every assertion below therefore classifies payload bytes as Roma-vs-silence first.
"""

import asyncio
import base64
import json

from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.websockets import WebSocketState

RATE = 8000
STREAM_ID = "MZ_bargein"
# 20ms of PCM16 @8k = 160 samples = 320 bytes, which mu-law encodes to the 160 bytes
# Twilio uses for a 20ms mono chunk. A constant non-zero sample so every byte of
# Roma's audio is distinguishable from the transport's silence padding.
CHUNK_PCM = b"\x11\x00" * 160
# ~0.5s of speech, long enough that a mid-point cut is unambiguous. EVEN on purpose: the
# transport re-chunks 20ms frames into its own (40ms) writes and drops a partial trailing
# chunk on EndFrame, so an odd count loses its last 160 bytes and the control test fails by
# exactly one chunk for a reason that has nothing to do with barge-in.
UTTERANCE_CHUNKS = 26
UTTERANCE_ULAW_BYTES = UTTERANCE_CHUNKS * 160
CUT_AFTER_BYTES = 480  # ~60ms on the wire: unambiguously mid-sentence


class _FakeWebSocket:
    """Records what Twilio would have received, in order."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        # BOTH states are required, as the real enum members rather than their values.
        # `client_state` backs `is_connected`; `application_state` is read separately by
        # `_can_send`. pipecat wraps the whole send in `except Exception -> logger.warning`,
        # so a fake missing either one sends nothing and says nothing -- the wire just comes
        # back empty, which looks identical to the bug this test exists to catch.
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def send_bytes(self, data: bytes) -> None:  # pragma: no cover
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.client_state = WebSocketState.DISCONNECTED


def _ulaw_byte(pcm: bytes) -> int:
    """Encode a constant-valued PCM block through the REAL serializer and return its byte."""
    s = TwilioFrameSerializer(
        stream_sid="MZ_probe",
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )

    async def go():
        from pipecat.frames.frames import StartFrame

        await s.setup(StartFrame(audio_in_sample_rate=RATE, audio_out_sample_rate=RATE))
        msg = await s.serialize(
            OutputAudioRawFrame(audio=pcm, sample_rate=RATE, num_channels=1)
        )
        payload = base64.b64decode(json.loads(msg)["media"]["payload"])
        assert len(set(payload)) == 1, "probe block must encode to a single repeated byte"
        return payload[0]

    return asyncio.run(go())


ROMA_BYTE = _ulaw_byte(CHUNK_PCM)
SILENCE_BYTE = _ulaw_byte(b"\x00\x00" * 160)
assert ROMA_BYTE != SILENCE_BYTE, "test sample is indistinguishable from silence padding"


def _events(ws) -> list[dict]:
    out = []
    for raw in ws.sent:
        if isinstance(raw, bytes):
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _roma_bytes(events) -> int:
    """Bytes of ROMA's audio on the wire, excluding the transport's silence padding."""
    total = 0
    for e in events:
        if e.get("event") != "media":
            continue
        payload = base64.b64decode(e["media"]["payload"])
        total += payload.count(ROMA_BYTE)
    return total


async def _wait_for(pred, *, required: bool = True, wait_secs: float = 5.0) -> bool:
    """Poll until `pred()`. Playback is clocked at real time, so this waits on the wire
    rather than on a guessed sleep. `required=False` means "settle, then continue"."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_secs
    while loop.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.01)
    if required:
        raise AssertionError("timed out waiting for Roma's audio to reach the wire")
    return False


def _audio(n: int) -> list[OutputAudioRawFrame]:
    return [
        OutputAudioRawFrame(audio=CHUNK_PCM, sample_rate=RATE, num_channels=1) for _ in range(n)
    ]


def _run_utterance(*, interrupt: bool):
    """Push one utterance through the real output transport, optionally cutting it."""
    ws = _FakeWebSocket()
    serializer = TwilioFrameSerializer(
        stream_sid=STREAM_ID,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )
    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=False,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_in_sample_rate=RATE,
            audio_out_sample_rate=RATE,
            serializer=serializer,
        ),
    )
    task = PipelineTask(Pipeline([transport.output()]))

    def on_wire() -> int:
        return _roma_bytes(_events(ws))

    async def go():
        runner = PipelineRunner(handle_sigint=False)
        run = asyncio.create_task(runner.run(task))

        for f in _audio(UTTERANCE_CHUNKS):
            await task.queue_frame(f)

        if interrupt:
            # Wait for Roma's audio to be ON THE WIRE before cutting, rather than sleeping
            # a guessed interval. `InterruptionFrame` is a SystemFrame: it is pushed
            # out-of-band and overtakes the queued audio, so a fixed sleep raced it and the
            # cut landed before playback started -- which is not the case under test.
            await _wait_for(lambda: on_wire() >= CUT_AFTER_BYTES)
            await task.queue_frame(InterruptionFrame())

        # Run to completion (control), or prove it stays cut (interrupted).
        await _wait_for(
            lambda: on_wire() >= UTTERANCE_ULAW_BYTES, required=False, wait_secs=2.0
        )
        await task.queue_frame(EndFrame())
        await run

    asyncio.run(go())
    return ws, serializer


def test_barge_in_clears_the_carrier_and_stops_playback_short():
    """Audio in flight -> InterruptionFrame -> clear on the wire, playback truncated."""
    ws, _serializer = _run_utterance(interrupt=True)
    events = _events(ws)
    kinds = [e.get("event") for e in events]

    # 1. clear reached the wire, with the live streamSid (docs/05 Layer 3 step 3).
    assert "clear" in kinds, f"barge-in never reached Twilio; wire was {set(kinds)}"
    clears = [e for e in events if e.get("event") == "clear"]
    assert all(e["streamSid"] == STREAM_ID for e in clears)

    cut = kinds.index("clear")

    # 2. Roma was speaking when it landed -- otherwise this proves nothing about a CUT.
    assert _roma_bytes(events[:cut]) > 0, "nothing was in flight; not a mid-sentence cut"

    # 3. Nothing queued behind the interruption reached the carrier. Silence padding still
    #    flows after the cut, which is why this counts Roma's bytes and not `media` events.
    leaked = _roma_bytes(events[cut + 1 :])
    assert leaked == 0, f"{leaked} bytes of Roma's audio leaked past the barge-in"

    # 4. The cut landed MID-utterance: strictly less of her audio out than went in.
    assert 0 < _roma_bytes(events) < UTTERANCE_ULAW_BYTES


def test_the_same_utterance_uninterrupted_plays_through():
    """The control. Without it, a transport that dropped ALL audio would pass the test above.

    This is the half that makes the assertion above mean "the interruption cut it" rather
    than "nothing ever reaches the wire" -- the failure mode that let the missing
the old stream-lifecycle bug and the never-opened `OpeningTurnGuard` both look healthy.
    """
    ws, _serializer = _run_utterance(interrupt=False)
    events = _events(ws)

    assert "clear" not in [e.get("event") for e in events]
    assert _roma_bytes(events) == UTTERANCE_ULAW_BYTES

    interrupted_ws, _ = _run_utterance(interrupt=True)
    assert _roma_bytes(_events(interrupted_ws)) < _roma_bytes(events)
