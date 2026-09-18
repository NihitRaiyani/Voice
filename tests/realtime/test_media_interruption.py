"""Barge-in / cancellation safety of the custom processors (docs/04, docs/08).

Interruptions are still DISABLED live (that flag flip + backchannel guard + adaptive
endpointing is Step 5B, gated on a clean audio path). These tests make the pieces
barge-in-READY and lock the guarantees so 5B is a safe flag flip:

- cancellation routes THROUGH the pre-TTS filter — an InterruptionFrame never disables it,
  and no raw/blocked text can slip past during teardown;
- the phase controller does not mis-advance on interruption/cancel frames;
- the TTS sanitizer drops any half-sample on Interruption/Cancel/End, idempotently.
"""

import asyncio

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    TextFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from roma.domain.conversation.prompts import assemble_system_prompt
from roma.domain.conversation.state import CallState
from roma.domain.safety import safe_output
from roma.realtime.phase_controller import PhaseControllerProcessor
from roma.realtime.pretts import PreTTSFilterProcessor
from roma.realtime.tts import TTSAudioSanitizer

BLOCKED = "Fees pachaas hazaar hai."


def _run(proc, frames):
    down, _ = asyncio.run(run_test(proc, frames_to_send=frames))
    return down


def test_filter_still_substitutes_a_blocked_line_after_an_interruption():
    proc = PreTTSFilterProcessor()
    down = _run(proc, [InterruptionFrame(), TextFrame(BLOCKED)])
    spoken = [f.text.strip() for f in down if isinstance(f, TextFrame)]
    assert spoken == [safe_output(BLOCKED)]
    assert BLOCKED not in spoken
    assert proc.last_spoken.strip() == safe_output(BLOCKED)


def test_interruption_frame_passes_through_the_filter():
    down = _run(PreTTSFilterProcessor(), [InterruptionFrame()])
    assert any(isinstance(f, InterruptionFrame) for f in down)


def test_phase_controller_does_not_advance_on_interruption_frame():
    state = CallState(call_sid="CA_int", phase="p2_discover", education="12th")
    proc = PhaseControllerProcessor(state, client=None)
    down = _run(proc, [InterruptionFrame()])
    assert state.phase == "p2_discover"
    assert state.turn_count == 0
    assert any(isinstance(f, InterruptionFrame) for f in down)


def test_phase_controller_forwards_context_frame_unchanged_when_no_user_message():
    state = CallState(call_sid="CA_int2", phase="p1_open", lead_name="ji")
    proc = PhaseControllerProcessor(state, client=None)
    ctx = LLMContext(
        messages=[
            {
                "role": "system",
                "content": assemble_system_prompt(state.as_prompt_vars(), "p1_open"),
            }
        ]
    )
    _run(proc, [LLMContextFrame(ctx)])
    assert state.phase == "p1_open" and state.turn_count == 0


from pipecat.processors.frame_processor import FrameDirection  # noqa: E402


def _tts(pcm, ctx="t"):
    return TTSAudioRawFrame(pcm, 8000, 1, context_id=ctx)


def _sanitizer():
    return TTSAudioSanitizer(enable_direct_mode=True)


async def _drive_direct(proc, frames):
    captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    proc.push_frame = _capture
    for f in frames:
        await proc.process_frame(f, FrameDirection.DOWNSTREAM)
    return captured


def _audio(frames):
    return b"".join(f.audio for f in frames if isinstance(f, TTSAudioRawFrame))


def test_sanitizer_drops_half_sample_on_cancel():
    out = asyncio.run(
        _drive_direct(
            _sanitizer(), [_tts(b"\x01\x02\x03"), CancelFrame(), _tts(b"\x04\x05\x06\x07")]
        )
    )
    assert _audio(out) == b"\x01\x02" + b"\x04\x05\x06\x07"


def test_sanitizer_drops_half_sample_on_end():
    out = asyncio.run(
        _drive_direct(
            _sanitizer(), [_tts(b"\x01\x02\x03"), EndFrame(), _tts(b"\x04\x05\x06\x07")]
        )
    )
    assert _audio(out) == b"\x01\x02" + b"\x04\x05\x06\x07"


def test_sanitizer_drops_half_sample_on_interruption():
    """The frame a real barge-in actually carries.

    `InterruptionFrame` is a bare `SystemFrame` — neither a CancelFrame nor an EndFrame —
    so the isinstance check above it had to name it explicitly. Missed, a held odd byte
    prefixes the next turn's PCM and byte-shifts every sample after it: the garbled-audio
    bug, back again, on exactly the path Step 5B turns on.
    """
    out = asyncio.run(
        _drive_direct(
            _sanitizer(),
            [_tts(b"\x01\x02\x03"), InterruptionFrame(), _tts(b"\x04\x05\x06\x07")],
        )
    )
    assert _audio(out) == b"\x01\x02" + b"\x04\x05\x06\x07"


def test_sanitizer_double_interruption_is_idempotent():
    """Two barge-ins in a row (docs/08: cancellation must be idempotent)."""
    out = asyncio.run(
        _drive_direct(
            _sanitizer(),
            [
                _tts(b"\x01\x02\x03"),
                InterruptionFrame(),
                InterruptionFrame(),
                _tts(b"\x04\x05\x06\x07"),
            ],
        )
    )
    assert _audio(out) == b"\x01\x02" + b"\x04\x05\x06\x07"


def test_sanitizer_passes_the_interruption_frame_through():
    """The clear-Twilio-buffer path depends on it reaching `transport.output()`: the
    Twilio serializer turns `InterruptionFrame` into the Media Streams `clear` event
    (docs/05 Layer 3 step 3). Swallowing it would leave ~1s of Roma overtalking."""
    out = asyncio.run(_drive_direct(_sanitizer(), [InterruptionFrame()]))
    assert any(isinstance(f, InterruptionFrame) for f in out)


def test_sanitizer_double_cancel_is_idempotent():
    out = asyncio.run(
        _drive_direct(
            _sanitizer(),
            [_tts(b"\x01\x02\x03"), CancelFrame(), CancelFrame(), _tts(b"\x04\x05\x06\x07")],
        )
    )
    assert _audio(out) == b"\x01\x02" + b"\x04\x05\x06\x07"
