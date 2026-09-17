"""End the call once Roma has signed off (hard rule seven).

## The bug this exists to stop

Live call CAfa2a011 (2026-07-26). Roma delivered her sign-off at 12:34:07 and the media
stream stayed open until **12:39:48** — five minutes and forty seconds of nothing, ended by
pipecat's idle-timeout watchdog rather than by us:

    12:34:07  TTS  "Dhanyavaad! Milte hain kal ..."
    12:39:48  WARNING pipecat.pipeline.worker: Idle timeout detected.
    12:39:48  WARNING ...and cancelling the runner.

`hard_rules.md` rule seven already says the call is over after a sign-off line, and the
prompt holds Roma to it — she correctly said nothing more. Nothing hung up the phone. From
the lead's side that is dead air they have to end themselves, and every second of it is
billed by Twilio.

## Why it waits for `BotStoppedSpeakingFrame`

`EndWorkerFrame` flushes what is queued ahead of it, but only what has already been queued.
The win is decided in `PhaseControllerProcessor` BEFORE the closing line has been generated
— ending there would cut Roma off mid-goodbye, which is a worse bug than the one being
fixed. So this sits after `transport.output()`, where `BotStoppedSpeakingFrame` means the
audio has actually been written to the wire, and ends the call on the first one after the
win.

## The five-minute deadline

The same processor enforces the call's time ceiling (`roma.controller.pacing`). By
`OVER_SECS` the pacing lines have been telling Roma to sign off for two minutes; if the
call is still running, something has gone wrong and no prompt is going to fix it — a lead
who will not be booked in five minutes gets a callback, not a longer call.

Two thresholds, because a deadline that fires mid-syllable is its own defect:

* at `OVER_SECS`, end on the next `BotStoppedSpeakingFrame` — Roma finishes her sentence;
* at `OVER_SECS + OVER_GRACE_SECS`, end on ANY frame. This is the backstop for the case
  that has no next bot frame: the lead is monologuing, or generation has stalled.

## Why a timer as well, and not only frames

Live call CA1bf16a (2026-07-26) is why. Roma's last words were at 14:42:32; the media
stream was torn down at **15:02:12** — nearly twenty minutes later, and again not by us.
Both thresholds above are evaluated inside `process_frame`, so both need a frame to arrive.
Twilio stopped delivering inbound audio at 132 seconds (`inbound_frames=6636` at 20ms a
frame) and the deadline was never so much as tested.

A deadline that only fires while something else is already happening is not a deadline. So
a watchdog task starts with the call and ends it on its own schedule; the frame checks stay
because they are what lets Roma finish her sentence when frames ARE flowing.
"""

import asyncio
import logging

from pipecat.frames.frames import BotStoppedSpeakingFrame, EndWorkerFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.controller.pacing import OVER_GRACE_SECS, OVER_SECS

_log = logging.getLogger("roma.telephony")


class CallCloser(FrameProcessor):
    """End the call on a locked visit, or when the five-minute budget runs out.

    Reads the win from the phase controller rather than tracking it here: the machine owns
    the win condition (docs/03) and a second copy of it would be a second thing to get
    wrong. `won_fn` is a callable so the value is read at frame time, not at construction.

    `elapsed_fn` is the same shape and returns seconds since the call connected. Passing
    None disables the deadline entirely, which is what the win-only tests use.
    """

    def __init__(self, won_fn, elapsed_fn=None, deaf_fn=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._won_fn = won_fn
        self._elapsed_fn = elapsed_fn
        self._deaf_fn = deaf_fn
        self._watchdog = None
        self._watchdog_unavailable = False
        self.ended = False

    async def _watch(self) -> None:
        """End the call at the deadline whether or not anything else is happening.

        Sleeps to `OVER_SECS`, gives one grace window for an in-flight sentence to finish
        (the frame path ends it sooner if a `BotStoppedSpeakingFrame` arrives), then ends
        regardless. Woken by nothing and needing no frames — that is the whole point.
        """
        try:
            await asyncio.sleep(max(0.0, OVER_SECS - self._elapsed()))
            if self.ended:
                return
            await asyncio.sleep(max(0.0, OVER_SECS + OVER_GRACE_SECS - self._elapsed()))
            if not self.ended:
                await self._end(f"five-minute deadline ({self._elapsed():.0f}s), no frames")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the watchdog must never take the call down with it
            _log.exception("call-deadline watchdog failed; the call may run long")

    def _start_watchdog(self) -> None:
        if self._watchdog is not None or self._watchdog_unavailable or self._elapsed_fn is None:
            return
        coro = self._watch()
        try:
            self._watchdog = self.create_task(coro)
        except Exception:  # noqa: BLE001 — no task manager (direct-mode tests); frames still guard
            coro.close()
            self._watchdog_unavailable = True
            _log.warning("could not start the call-deadline watchdog; frame path only")

    async def cleanup(self) -> None:
        if self._watchdog is not None:
            await self.cancel_task(self._watchdog)
            self._watchdog = None
        await super().cleanup()

    def _deaf(self) -> bool:
        if self._deaf_fn is None:
            return False
        try:
            return bool(self._deaf_fn())
        except Exception:  # noqa: BLE001 — an unreadable health flag must not end the call
            _log.warning("call closer could not read the call health; leaving the call open")
            return False

    def _elapsed(self) -> float:
        if self._elapsed_fn is None:
            return 0.0
        try:
            return float(self._elapsed_fn())
        except Exception:  # noqa: BLE001 — an unreadable clock must not end the call early
            _log.warning("call closer could not read the pacing clock; deadline disabled")
            return 0.0

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        self._start_watchdog()

        if self.ended:
            return

        elapsed = self._elapsed()
        if elapsed >= OVER_SECS + OVER_GRACE_SECS:
            await self._end(f"time budget exceeded by {elapsed - OVER_SECS:.0f}s")
            return

        if not isinstance(frame, BotStoppedSpeakingFrame):
            return

        if elapsed >= OVER_SECS:
            await self._end(f"five-minute budget reached ({elapsed:.0f}s)")
            return

        if self._deaf():
            await self._end("STT is down — the call cannot hear the lead")
            return

        try:
            won = bool(self._won_fn())
        except Exception:  # noqa: BLE001 — never let the closer be the thing that breaks
            _log.warning("call closer could not read the win state; leaving the call open")
            return
        if won:
            await self._end("visit locked")

    async def _end(self, reason: str) -> None:
        self.ended = True
        _log.info("ending the call: %s", reason)
        await self.push_frame(EndWorkerFrame(reason=reason), FrameDirection.DOWNSTREAM)


__all__ = ["CallCloser"]
