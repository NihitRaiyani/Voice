"""A dead service must be loud, and a deaf call must end.

Live call CA34ca96e (2026-07-27): the Sarvam STT websocket never connected, Roma greeted
the lead, and the lead then talked for the rest of the call into a line that could not hear
them. The only trace was one DEBUG-level line in a 300 KB log. From their side the call
"got cut instantly".
"""

import asyncio
import logging

from pipecat.frames.frames import BotStoppedSpeakingFrame, EndWorkerFrame, ErrorFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService
from roma.realtime.closing import CallCloser
from roma.realtime.health import CallHealth

CONNECT_FAILURE = "Failed to connect to Sarvam: "


class _FakeSTT(STTService):
    """A real `STTService` subclass, so identification is by TYPE and not by message text."""

    async def run_stt(self, audio):  # pragma: no cover - never called
        yield None


class _NotSTT:
    pass


def _error(processor, text=CONNECT_FAILURE) -> ErrorFrame:
    return ErrorFrame(error=text, fatal=False, processor=processor)


def test_an_stt_error_makes_the_call_deaf():
    """THE regression."""
    h = CallHealth()
    h.record(_error(_FakeSTT()))
    assert h.deaf is True
    assert CONNECT_FAILURE in h.errors[0]


def test_a_non_stt_error_is_recorded_but_does_not_end_the_call():
    """Deliberately not "any error": a TTS hiccup is recoverable and a filler-clip failure
    is cosmetic. Only losing the STT makes the conversation structurally impossible."""
    h = CallHealth()
    h.record(_error(_NotSTT(), "Sarvam TTS websocket closed"))
    assert h.deaf is False
    assert len(h.errors) == 1


def test_the_stt_failure_is_logged_at_error_with_our_own_logger(caplog):
    """Pipecat logs it at WARNING under `pipecat.*` and carries on, which is why four calls
    could go by without anyone seeing it. It belongs in `roma.realtime`."""
    with caplog.at_level(logging.WARNING, logger="roma.realtime"):
        CallHealth().record(_error(_FakeSTT()))
    assert any("cannot hear the lead" in r.getMessage() for r in caplog.records)


def test_recording_a_malformed_frame_never_raises():
    """Health reporting sits on the error path. It must not be the thing that breaks."""
    h = CallHealth()
    h.record(object())
    assert h.deaf is False


def test_health_is_per_call_not_shared():
    """docs/08: concurrent calls must never report each other's failures."""
    a, b = CallHealth(), CallHealth()
    a.record(_error(_FakeSTT()))
    assert a.deaf is True and b.deaf is False


class _Recorder(CallCloser):
    """Captures what the closer pushed, without a pipeline."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pushed = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)


async def _send(closer, frame):
    await CallCloser.process_frame(closer, frame, FrameDirection.DOWNSTREAM)


def _ended(closer) -> bool:
    return any(isinstance(f, EndWorkerFrame) for f in closer.pushed)


def test_a_deaf_call_ends_on_romas_next_pause():
    """It ends on `BotStoppedSpeakingFrame`, not on the error itself, so Roma finishes the
    sentence she is on — a hangup mid-word is its own defect (the same reason the win path
    waits for this frame)."""

    async def run():
        c = _Recorder(lambda: False, lambda: 0.0, deaf_fn=lambda: True)
        await _send(c, BotStoppedSpeakingFrame())
        return c

    assert _ended(asyncio.run(run()))


def test_a_healthy_call_is_left_alone():
    async def run():
        c = _Recorder(lambda: False, lambda: 0.0, deaf_fn=lambda: False)
        await _send(c, BotStoppedSpeakingFrame())
        return c

    assert not _ended(asyncio.run(run()))


def test_an_unreadable_health_flag_does_not_end_the_call():
    """Fail-safe direction: a broken health check must not hang up on a working call."""

    def boom():
        raise RuntimeError("health unreadable")

    async def run():
        c = _Recorder(lambda: False, lambda: 0.0, deaf_fn=boom)
        await _send(c, BotStoppedSpeakingFrame())
        return c

    assert not _ended(asyncio.run(run()))


def test_the_closer_without_a_health_check_behaves_exactly_as_before():
    """`deaf_fn` defaults to None so every existing caller and test is unaffected."""

    async def run():
        c = _Recorder(lambda: False, lambda: 0.0)
        await _send(c, BotStoppedSpeakingFrame())
        return c

    assert not _ended(asyncio.run(run()))
