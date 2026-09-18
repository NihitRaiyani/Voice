"""Ending the call after the sign-off (live regression, CAfa2a011 on 2026-07-26).

Roma delivered her sign-off at 12:34:07 and the media stream stayed open until 12:39:48 —
five minutes forty of dead air, ended by pipecat's idle watchdog rather than by us. The
prompt was right (hard rule seven: after a sign-off the call is over, say nothing more);
nothing hung up the phone.

Driven inline with `enable_direct_mode`, the same pattern as `test_opening_guard.py`: the
closer emits an extra frame of its own, which `run_test` cannot express cleanly.
"""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from roma.domain.conversation.pacing import OVER_GRACE_SECS, OVER_SECS
from roma.realtime.closing import CallCloser


def _closer(won=False):
    c = CallCloser(lambda: won, enable_direct_mode=True)
    c.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        c.captured.append(frame)

    c.push_frame = _capture
    return c


def _send(closer, frames):
    async def run():
        for f in frames:
            await closer.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return closer.captured


def _ends(closer):
    return [f for f in closer.captured if isinstance(f, EndWorkerFrame)]


def test_the_call_ends_after_the_signoff_once_the_visit_is_locked():
    """THE regression."""
    c = _closer(won=True)
    _send(c, [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1
    assert c.ended


def test_a_call_with_no_lock_is_never_ended_here():
    """A lead who did not book is still on the phone. Hanging up on them would turn a soft
    'dekhta hoon' into a dropped call."""
    c = _closer(won=False)
    _send(c, [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
    assert _ends(c) == []
    assert not c.ended


def test_the_end_frame_comes_AFTER_the_frame_that_triggered_it():
    """`EndWorkerFrame` flushes what is queued ahead of it. Pushing it before forwarding
    the `BotStoppedSpeakingFrame` would race the audio it is meant to wait for."""
    c = _closer(won=True)
    out = _send(c, [BotStoppedSpeakingFrame()])
    assert isinstance(out[0], BotStoppedSpeakingFrame)
    assert isinstance(out[1], EndWorkerFrame)


def test_it_only_fires_once():
    """Roma may stop speaking again during teardown; a second EndWorkerFrame is noise at
    best and a double-teardown at worst."""
    c = _closer(won=True)
    _send(c, [BotStoppedSpeakingFrame(), BotStoppedSpeakingFrame(), BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1


def test_every_other_frame_passes_straight_through_untouched():
    c = _closer(won=True)
    out = _send(c, [TranscriptionFrame("haan", "lead", "t1"), BotStartedSpeakingFrame()])
    assert [type(f) for f in out] == [TranscriptionFrame, BotStartedSpeakingFrame]


def test_the_win_is_read_at_frame_time_not_at_construction():
    """The closer is built before the phase controller has seen a single turn, so a value
    snapshotted at construction would always be False and the call would never end."""
    state = {"won": False}
    c = CallCloser(lambda: state["won"], enable_direct_mode=True)
    c.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        c.captured.append(frame)

    c.push_frame = _capture

    _send(c, [BotStoppedSpeakingFrame()])
    assert _ends(c) == []

    state["won"] = True
    _send(c, [BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1


def test_a_raising_win_check_leaves_the_call_open_rather_than_crashing_it():
    """Teardown paths must never fail (docs/08). Worst case here is a call that idles —
    strictly better than one that dies mid-sentence."""

    def _boom():
        raise RuntimeError("phase controller is gone")

    c = CallCloser(_boom, enable_direct_mode=True)
    c.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        c.captured.append(frame)

    c.push_frame = _capture
    _send(c, [BotStoppedSpeakingFrame()])
    assert _ends(c) == []
    assert not c.ended


def _timed_closer(elapsed, won=False):
    c = CallCloser(lambda: won, lambda: elapsed, enable_direct_mode=True)
    c.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        c.captured.append(frame)

    c.push_frame = _capture
    return c


def test_a_call_inside_the_budget_is_left_alone():
    c = _timed_closer(OVER_SECS - 1)
    _send(
        c,
        [
            BotStartedSpeakingFrame(),
            BotStoppedSpeakingFrame(),
            TranscriptionFrame("hm", "l", ""),
        ],
    )
    assert _ends(c) == []


def test_the_call_ends_at_the_deadline_even_with_no_booking():
    """This is the point of the ceiling: an unbooked lead at five minutes gets a callback,
    not a longer call."""
    c = _timed_closer(OVER_SECS, won=False)
    _send(c, [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1


def test_the_deadline_waits_for_roma_to_finish_her_sentence():
    """A deadline that fires mid-syllable is its own defect. Inside the grace window only a
    BotStoppedSpeakingFrame ends the call — transcripts and audio frames pass through."""
    c = _timed_closer(OVER_SECS + 1)
    _send(c, [TranscriptionFrame("abhi soch raha hoon", "lead", ""), BotStartedSpeakingFrame()])
    assert _ends(c) == []
    _send(c, [BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1


def test_past_the_grace_window_any_frame_ends_the_call():
    """The backstop for the case with no next bot frame — the lead is monologuing, or
    generation has stalled. Without it the ceiling is only advisory."""
    c = _timed_closer(OVER_SECS + OVER_GRACE_SECS)
    _send(c, [TranscriptionFrame("aur ek baat", "lead", "")])
    assert len(_ends(c)) == 1


def test_the_call_is_only_ended_once():
    c = _timed_closer(OVER_SECS + OVER_GRACE_SECS)
    _send(
        c,
        [
            TranscriptionFrame("a", "l", ""),
            TranscriptionFrame("b", "l", ""),
            BotStoppedSpeakingFrame(),
        ],
    )
    assert len(_ends(c)) == 1


def test_every_frame_is_still_forwarded_when_the_deadline_fires():
    """The closer is on the audio path; swallowing the frame it fires on would clip the
    very sentence it is waiting for."""
    c = _timed_closer(OVER_SECS)
    out = _send(c, [BotStoppedSpeakingFrame()])
    assert any(isinstance(f, BotStoppedSpeakingFrame) for f in out)


def test_a_broken_clock_leaves_the_call_open_rather_than_ending_it_early():
    """Failing towards a call that runs long is recoverable; failing towards a call that
    hangs up on a lead mid-sentence is not."""

    def _boom():
        raise RuntimeError("no clock")

    c = CallCloser(lambda: False, _boom, enable_direct_mode=True)
    c.captured = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        c.captured.append(frame)

    c.push_frame = _capture
    _send(c, [BotStoppedSpeakingFrame()])
    assert _ends(c) == []


def test_the_win_path_is_unchanged_when_no_clock_is_supplied():
    """`elapsed_fn=None` disables the deadline entirely — the original behaviour."""
    c = _closer(won=True)
    _send(c, [BotStoppedSpeakingFrame()])
    assert len(_ends(c)) == 1


def test_the_watchdog_sleeps_to_the_deadline_then_ends_without_any_frame():
    """The real `_watch` body, with the clock wound forward so the sleeps are zero."""
    captured = []

    async def run():
        c = CallCloser(lambda: False, lambda: OVER_SECS + OVER_GRACE_SECS)

        async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
            captured.append(frame)

        c.push_frame = _capture
        await c._watch()

    asyncio.run(run())
    assert [f for f in captured if isinstance(f, EndWorkerFrame)]


def test_the_watchdog_stands_down_if_the_frame_path_already_ended_the_call():
    """Belt and braces must not double-end: a win at 4:30 ends the call on the sign-off,
    and the watchdog must notice rather than push a second EndWorkerFrame."""
    captured = []

    async def run():
        c = CallCloser(lambda: True, lambda: OVER_SECS)

        async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
            captured.append(frame)

        c.push_frame = _capture
        c.ended = True
        await c._watch()

    asyncio.run(run())
    assert not [f for f in captured if isinstance(f, EndWorkerFrame)]


def test_a_watchdog_that_cannot_start_leaves_no_dangling_coroutine():
    """Direct-mode tests have no task manager. The failure must be quiet and clean — a
    leaked coroutine here would warn on every single frame of every call."""
    c = CallCloser(lambda: False, lambda: 0.0, enable_direct_mode=True)
    c._start_watchdog()
    assert c._watchdog is None
    assert c._watchdog_unavailable is True
    c._start_watchdog()
    assert c._watchdog is None


def test_cleanup_is_safe_when_no_watchdog_was_ever_started():
    """Every direct-mode test takes this path; it must not raise."""

    async def run():
        c = CallCloser(lambda: False, lambda: 0.0, enable_direct_mode=True)
        await c.cleanup()
        assert c._watchdog is None

    asyncio.run(run())
