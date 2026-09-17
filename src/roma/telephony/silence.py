"""Dead-air watchdog: an interruption that produces no reply must not leave silence.

## The bug this exists for

Live call CA8a85a4f (2026-07-28). The lead said, three times, some version of "koi bol
kyun nahi raha hai" — is anyone there. The teardown explains it: 13 LLM calls, 9 TTS
outputs. Four turns produced no speech at all, and one gap ran 21 seconds.

It is an interaction between two fixes that are each correct alone:

* Barge-in cancels Roma's in-flight TTS the moment the VAD hears 0.5s of sound. It has to
  be that fast, or interrupting her does not feel like interrupting a person.
* The noise gate drops sub-word and foreign-script transcripts so a breath cannot open a
  user turn (`opening.is_noise_transcript`).

Put together: a cough cancels Roma mid-sentence, the transcript is then correctly dropped,
no user turn starts, and **nothing ever restarts her**. She was cut off and the line she
was speaking is gone. On the same call a second flavour appeared — the lead talking in
quick bursts cancelled each generation before it reached TTS, several times running.

## Why a watchdog rather than a targeted fix

Making the noise gate re-prompt would cover the first flavour only. The failure is better
described by what the LEAD experiences: nobody has said anything for several seconds and
nobody is about to. That is one condition, it covers both flavours and any third we have
not seen yet, and it is checkable without knowing which processor swallowed the turn.

Same shape as `opening.PickupGreeter`, which solves the mirror-image problem at call start.

## What arms it, and why that is the whole design

ONLY an interruption. `_armed` is set when an `InterruptionFrame` passes — Roma was cut off
mid-sentence — and cleared the moment she produces audio again.

The first version armed on every line Roma spoke, which is wrong in a way that was obvious
the moment it went out: it made ordinary thinking time look like a fault. On CA79ed16d the
watchdog fired **twelve times** in about two minutes. Each nudge re-ran her turn, so she
re-asked the same question, and nine consecutive lines offered the identical pair of slots.
The lead's complaint was that she repeated herself — caused by the fix for the opposite
problem.

A lead who goes quiet for four seconds after a question is thinking. That is not dead air,
and a counsellor does not fill it. Dead air is specifically: she was interrupted, and
nothing came back. That is the only thing this watches for.

Arming on interruption alone was still not enough, and CA3bf8d51c proves it: `nudges=6`,
and four of Roma's turns were audibly duplicated — twice she asked the identical question
seconds apart, once she repeated the whole slot offer, and once she said "Aap pehle bol
lijiye. Main sun rahi hoon." two seconds after saying it. The lead's complaint, again, was
that she repeats herself.

The cause is arithmetic. On that call the gap from a finalized transcript to the TTS text
measured **p50 3.60s, max 10.93s, with 7 of 21 replies over 4.0s** — the reply is simply
slower than the limit on this network, so a third of interrupted turns got nudged while
their real answer was still in flight. Two answers, one turn.

The first attempt at this disarmed on `TranscriptionFrame`, and it was **dead code**:
`LLMUserAggregator` consumes transcripts and never pushes them downstream, so a processor
sitting after the output transport never sees one. CA4ba2a6b8 then went further than
repetition — the two overlapping generations tore down the TTS context mid-stream and the
call went silent for good. See `telephony/turnflight.py` for the log.

So the answer comes from `TurnFlight` now, written by the processor that actually sees a
turn begin, rather than inferred from a frame that never arrives. "Nothing came back" cannot
be timed against a clock slower than the pipeline, and it cannot be inferred from frames
that a dependency is free to swallow — it has to be told.

## What it will not do

It nudges at most once per silence — `_armed` is cleared when it fires, so a persistent
silence produces one `LLMRunFrame` rather than a stream. Pushing a second while the first is
still generating is how you get Roma talking over herself.

It also stays quiet while the lead is talking.
"""

import asyncio
import logging
import time

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMRunFrame,
    StartFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_log = logging.getLogger("roma.telephony")

SILENCE_LIMIT_SECS = 5.0

_POLL_SECS = 0.5


class SilenceWatchdog(FrameProcessor):
    """Push one `LLMRunFrame` when the call has gone quiet with no reply coming.

    Sits after the output transport, beside `CallCloser`, so `BotStoppedSpeakingFrame` means
    audio actually reached the wire rather than merely being queued.
    """

    def __init__(self, limit_secs: float = SILENCE_LIMIT_SECS, flight=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._limit = limit_secs
        self._flight = flight
        self._last_activity = time.monotonic()
        self._user_speaking = False
        self._armed = False
        self._task = None
        self.nudges = 0

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._touch()
            self._start()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            self._touch()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
            self._touch()
        elif isinstance(frame, InterruptionFrame):
            self._armed = True
            self._touch()
        elif isinstance(frame, (TTSAudioRawFrame, BotStoppedSpeakingFrame)):
            if isinstance(frame, TTSAudioRawFrame) and self._flight is not None:
                self._flight.first_audio()
            self._armed = False
            self._touch()

        await self.push_frame(frame, direction)

    def _start(self) -> None:
        if self._task is None:
            self._task = self.create_task(self._watch())

    async def _stop(self) -> None:
        if self._task is not None:
            await self.cancel_task(self._task)
            self._task = None

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(_POLL_SECS)
            if self._user_speaking or not self._armed:
                continue
            if self._flight is not None and self._flight.pending:
                continue
            if time.monotonic() - self._last_activity < self._limit:
                continue
            self._armed = False
            self.nudges += 1
            _log.info(
                "silence watchdog: %.1fs with nobody speaking; restarting Roma's turn",
                self._limit,
            )
            self._touch()
            await self.push_frame(LLMRunFrame(), FrameDirection.DOWNSTREAM)

    async def cleanup(self) -> None:
        await self._stop()
        await super().cleanup()


__all__ = ["SilenceWatchdog", "SILENCE_LIMIT_SECS"]
