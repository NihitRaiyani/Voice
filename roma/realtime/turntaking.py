"""Pipecat turn strategies for Step 5B barge-in (docs/05 Layers 2 and 3).

Two strategies, both consuming the pure lexicon in `roma.realtime.backchannel`:

- `BackchannelAwareUserTurnStartStrategy` — decides whether the lead talking over Roma
  is a real turn-take or just a nod.
- `AdaptiveEndpointStopStrategy` — varies the turn-final silence 500/850/1300ms.

Both are only wired in when `ENABLE_BARGE_IN` is on (see `media.build_user_params`). With
the flag off, `media.py` keeps Step 3's configuration byte-for-byte.

## Why the guard is two-armed

docs/05's rule is: span < 600ms AND in the backchannel lexicon AND Roma mid-utterance =>
not a turn-take. The obvious implementation gates the whole decision on a transcript. That
does not work with our STT:

`SarvamSTTService` emits **zero** `InterimTranscriptionFrame`s and flushes its socket only
on `VADUserStoppedSpeakingFrame`; pipecat's own p99 TTFS for Sarvam is 1.17s. A purely
transcript-gated interruption would therefore fire ~1.2-2.1s after speech onset — after the
lead has already stopped talking. That is not barge-in.

So the two conditions are evaluated by whichever signal can answer them first:

- **Fast arm (VAD only).** A span of >=600ms fails docs/05 condition (a) on its own,
  *whatever* the words turn out to be. VAD can prove that without STT: arm a 600ms timer at
  speech onset, and if it survives, barge in. Roma is cut off ~800ms after onset
  (start_secs 0.2 + 600ms) instead of ~1.5s.
- **Slow arm (transcript).** Only reachable for sub-600ms utterances, because anything
  longer already barged in above. Here the words decide.

## Suppression keeps the text

A suppressed backchannel logs and returns CONTINUE. It must NOT call
`trigger_reset_aggregation()` — that handler wipes the *entire* aggregation buffer, not the
last segment, and docs/05 only ever says "log it, keep talking". Only the *interruption* is
suppressed; the text stays. Three concrete regressions this avoids: `achha` is the P6->P5
objection-answered signal, `hmm` is a whole user turn in the P2 budget test, and a lone `હા`
answering P1 while Roma finishes the question would be silently dropped — which is exactly
the live P1 stall the Gujarati affirmation fix was written to cure.
"""

import asyncio
import logging
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)

from roma.realtime.backchannel import (
    BACKCHANNEL_MAX_SECS,
    BARGE_IN_ATTEMPTS,
    ENDPOINT_CONTINUATION_SECS,
    ENDPOINT_DEFAULT_SECS,
    ENDPOINT_TERMINAL_SECS,
    endpoint_timeout_for,
    is_backchannel,
    vad_span_secs,
)

_log = logging.getLogger("roma.realtime")


class BackchannelAwareUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """Start a user turn unless the lead is merely backchannelling over Roma.

    This REPLACES pipecat's `VADUserTurnStartStrategy` rather than sitting beside it.
    `UserTurnController._trigger_user_turn_start` opens with `if self._user_turn: return`,
    so the first strategy to fire in a turn wins outright and every later trigger is
    discarded. `VADUserTurnStartStrategy` fires at speech onset, before any transcript
    exists — leave it in the list and it permanently pre-empts any word-aware decision,
    making this guard dead code that still looks wired up.
    """

    def __init__(self, *, backchannel_max_secs: float = BACKCHANNEL_MAX_SECS, **kwargs):
        """Initialize the guard.

        Args:
            backchannel_max_secs: docs/05 condition (a). A user-speech span at or above
                this is a turn-take regardless of the words spoken.
            **kwargs: Passed through to `BaseUserTurnStartStrategy` (notably
                `enable_interruptions`).
        """
        super().__init__(**kwargs)
        self._backchannel_max_secs = backchannel_max_secs
        self._interrupt_attempts = 0
        self._bot_speaking = False
        self._bot_pending = True
        self._bot_has_spoken = False
        self._turn_active = False
        self._vad_start: VADUserStartedSpeakingFrame | None = None
        self._last_span: float | None = None
        self._arm_task: asyncio.Task | None = None

    @property
    def _bot_busy(self) -> bool:
        """Roma is speaking, or is about to. Either way there is something to interrupt.

        ## The bug this exists to stop

        Live call CA032cec3 (2026-07-27), the first with a working VAD. The lead said
        NOTHING; line noise tripped the VAD, and every trip destroyed Roma's turn:

            00:05:23.009  VADProcessor: User started speaking     <- noise, 0.5s after pickup
            00:05:23.196  LLMUserAggregator: broadcasting interruption
            00:05:32.033  SarvamTTS: Generating TTS [Hi ji, Roma baat kar rahi hoon ...]
            00:05:34.539  broadcasting interruption               <- noise again
            00:05:34.551  SarvamTTS TTFB: 2.521s                  <- audio was ready
            00:05:34.554  Disconnecting from Sarvam               <- and was cancelled

        Roma's greeting was generated, synthesised, and killed three milliseconds before it
        reached the wire. Every cycle, for the whole call. The lead heard silence.

        `_bot_speaking` only becomes True once audio is on the wire, so it was False for the
        entire generate-then-synthesise window — 5.5 seconds on that call (3.0s LLM TTFB +
        2.5s TTS TTFB). A VAD onset in that window took the "nobody to interrupt" path and
        triggered a turn, cancelling the reply it was interrupting.

        The window is invisible without VAD, which is why five earlier calls never showed
        it: with no VAD frames only transcripts started turns, and `OpeningTurnGuard` held
        those. Fixing the VAD exposed it immediately.

        So the guard's question is not "is Roma talking" but "is Roma mid-turn" — from the
        moment she owes a reply until she has finished delivering it.
        """
        return self._bot_speaking or self._bot_pending

    async def handle_user_turn_started(self):
        """A turn is now running — the guard has nothing left to decide until it ends."""
        self._turn_active = True
        self._bot_speaking = False
        self._bot_pending = False
        self._last_span = None
        self._vad_start = None
        await self._cancel_arm()

    async def handle_user_turn_stopped(self):
        """Turn over — re-arm for the next one.

        The lead has stopped, so Roma now owes a reply. That is the start of the window
        `_bot_busy` protects: everything from here until she has finished speaking is one
        turn of hers, and a noise blip inside it must not cancel it.
        """
        self._turn_active = False
        self._bot_pending = True
        self._last_span = None
        self._vad_start = None
        await self._cancel_arm()

    async def cleanup(self):
        """Drop the pending fast-arm timer when the call tears down."""
        await self._cancel_arm()
        await super().cleanup()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        """Route the frame to the arm that can decide on it.

        Returns:
            STOP once a user turn has been triggered, CONTINUE otherwise.
        """
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._bot_has_spoken = True
            # Per bot turn, not per call: two attempts spread over a whole call are two
            # ordinary backchannels, and counting them together would make Roma jumpy.
            self._interrupt_attempts = 0
        elif isinstance(frame, BotStoppedSpeakingFrame):
            return await self._handle_bot_stopped_speaking()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            return await self._handle_vad_started(frame)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._handle_vad_stopped(frame)
        elif isinstance(frame, TranscriptionFrame):
            return await self._handle_transcription(frame)

        return ProcessFrameResult.CONTINUE

    async def _handle_bot_stopped_speaking(self) -> ProcessFrameResult:
        """Roma went quiet. Whatever the lead is saying is a normal turn, not a barge-in.

        The pending fast arm is now pointless — condition (c) has failed — so start the
        turn immediately rather than making the lead wait out the remaining timer.
        """
        self._bot_speaking = False
        self._bot_pending = False
        self._bot_has_spoken = True
        if self._arm_task is not None:
            await self._cancel_arm()
            if not self._turn_active:
                await self.trigger_user_turn_started()
                return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_started(
        self, frame: VADUserStartedSpeakingFrame
    ) -> ProcessFrameResult:
        """Speech onset: start the turn now, or arm the fast arm if Roma is talking."""
        self._vad_start = frame
        self._last_span = None

        if self._turn_active:
            return ProcessFrameResult.CONTINUE

        if not self._bot_has_spoken:
            return ProcessFrameResult.CONTINUE

        if not self._bot_busy:
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP

        # PERSISTENCE, not just duration. The fast arm only fires on ONE continuous span
        # past `backchannel_max_secs`, so short repeated attempts never reach it: "wait" is
        # about 300ms, and each burst cancels the timer before it expires. On call b67b25da
        # the lead said "wait wait wait wait wait ... मैं कब का बोल रहा हूँ फिर भी बोले जा रहे
        # हो" and Roma talked straight through — 3 barge-ins in a four-minute call.
        #
        # One short sound over Roma is an acknowledgement, which is exactly what the 600ms
        # gate exists to protect. A SECOND one inside the same bot turn is not: nobody
        # backchannels twice while being talked over. That is someone trying to stop her.
        self._interrupt_attempts += 1
        if self._interrupt_attempts >= BARGE_IN_ATTEMPTS:
            await self._cancel_arm()
            _log.info(
                "barge-in: %d speech attempts over Roma in one turn", self._interrupt_attempts
            )
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP

        await self._arm()
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_stopped(self, frame: VADUserStoppedSpeakingFrame):
        """Speech ended inside the 600ms window: record the span, hand off to the words."""
        await self._cancel_arm()
        self._last_span = vad_span_secs(self._vad_start, frame)

    async def _handle_transcription(self, frame: TranscriptionFrame) -> ProcessFrameResult:
        """Slow arm — only reached for sub-600ms utterances (longer ones already barged in)."""
        if self._turn_active:
            return ProcessFrameResult.CONTINUE

        span = self._last_span
        suppress = (
            self._bot_speaking
            and span is not None
            and span < self._backchannel_max_secs
            and is_backchannel(frame.text)
        )
        if suppress:
            _log.info("backchannel suppressed (span=%.2fs) — not a turn-take", span)
            return ProcessFrameResult.CONTINUE

        await self.trigger_user_turn_started()
        return ProcessFrameResult.STOP

    async def _arm(self):
        """Start the fast arm. Idempotent — a re-arm supersedes any pending timer."""
        await self._cancel_arm()
        self._arm_task = self.create_task(self._arm_handler())

    async def _cancel_arm(self):
        """Cancel the fast arm if pending. Safe to call repeatedly and concurrently."""
        task = self._arm_task
        if task is None:
            return
        self._arm_task = None
        await self.cancel_task(task)

    async def _arm_handler(self):
        """Barge in if the lead is still speaking after `backchannel_max_secs`.

        Surviving the sleep proves the span is at or above the threshold, so docs/05
        condition (a) has failed and this is a real turn-take — no transcript needed, and
        none would arrive in time anyway.
        """
        try:
            await asyncio.sleep(self._backchannel_max_secs)
        except asyncio.CancelledError:
            return
        finally:
            self._arm_task = None

        if self._turn_active or not self._bot_busy:
            return
        _log.info("barge-in: user speech exceeded %.2fs over Roma", self._backchannel_max_secs)
        await self.trigger_user_turn_started()


class AdaptiveEndpointStopStrategy(SpeechTimeoutUserTurnStopStrategy):
    """Turn-final silence that varies with what the lead just said (docs/05 Layer 2).

    500ms after a clear terminal answer, 850ms by default, 1300ms after a continuation
    marker. See `roma.realtime.backchannel.endpoint_timeout_for` for the lexicons.

    **Known limitation — Sarvam's latency eats part of the win.** Turn-end waits on two
    timers, and the words only exist once STT returns: pipecat's p99 TTFS for Sarvam is
    1.17s, measured from end-of-speech, while the VAD stop lands at end-of-speech + 0.85s.
    So a transcript typically arrives with only ~0.3s left on the default timer, and when
    it arrives *later* than that the timer has already fired and there is nothing left to
    adapt — that turn runs at the 850ms default whatever the lead said. The adaptive
    values are real when the transcript beats the timer (see `_rearm_to_deadline`), and
    inert when it does not. docs/10 Step 7 ("tune 850ms / VAD sensitivity on real Twilio
    calls") is where the numbers get measured on real audio. Written down rather than
    hidden so nobody later reads a partly-flat latency graph as "adaptive endpointing is
    broken".

    **Handle `Settings.vad_stop_secs` with care.** It is now settable for Step 7 tuning, but
    0.85 there does triple duty — endpoint floor, Sarvam flush trigger, and the
    `effective_stt_wait = stt_timeout - stop_secs` subtraction in the base class. Lowering
    it trips pipecat's mismatch warning, re-expands the STT safety net, and fires VAD stop
    inside the 0.3-0.5s natural pauses that are 52% of the corpus. It is exposed so it can
    be measured against, not because it is a free dial: move it one step at a time and read
    the `endpoint timing:` teardown line each run.
    """

    def __init__(
        self,
        *,
        user_speech_timeout: float = ENDPOINT_DEFAULT_SECS,
        terminal_secs: float = ENDPOINT_TERMINAL_SECS,
        continuation_secs: float = ENDPOINT_CONTINUATION_SECS,
        **kwargs,
    ):
        """Initialize with docs/05's 850ms default rather than pipecat's 600ms.

        All three adaptive values are injectable so Step 7 can tune them from `Settings`
        without a code edit. They are forwarded to `endpoint_timeout_for` on every
        transcript — holding them here and passing them down keeps one owner for the
        numbers, rather than a strategy whose default disagrees with the lexicon's.
        """
        super().__init__(user_speech_timeout=user_speech_timeout, **kwargs)
        self._default_user_speech_timeout = user_speech_timeout
        self._terminal_secs = terminal_secs
        self._continuation_secs = continuation_secs

    async def handle_user_turn_started(self):
        """Reset to the per-turn default so a previous turn's 1300ms can't leak into this
        one. The default is whatever was injected at construction, not the constant."""
        await super().handle_user_turn_started()
        self._user_speech_timeout = self._default_user_speech_timeout

    async def _handle_transcription(self, frame: TranscriptionFrame):
        """Re-derive the timeout from the turn's text, and re-arm the running timer to it.

        The transcript is the only place the words exist, so this is the seam. But setting
        `_user_speech_timeout` here is NOT enough on its own, and the reason is the whole
        substance of this override:

        `_restart_user_speech_timer` arms the timer as
        `create_task(self._user_speech_timeout_handler(self._user_speech_timeout))` — the
        float is **bound as an argument at arm time**. On our stack the VAD stop always
        lands first (Sarvam flushes its socket only on `VADUserStoppedSpeakingFrame`), so
        by the time a transcript arrives the timer is already sleeping on the old value,
        and the base class's own re-arm branch is gated behind `_vad_stopped_time is None`
        — never true here. Mutating the attribute alone is a silent no-op: every turn
        would quietly run at the 850ms default no matter what the lead said, with no error
        and a green test suite.

        So after the base class has run we re-arm explicitly — to the *remaining* time to
        the deadline, `_vad_stopped_time + timeout`, not to the full timeout again.
        Sleeping the new value from now would push a 500ms terminal answer out to roughly
        transcript-arrival + 500ms, i.e. LATER than the 850ms it was meant to beat.
        `_user_speech_timeout` is the policy floor measured from end-of-speech; honouring
        it means measuring from the VAD stop that marked it.
        """
        self._user_speech_timeout = endpoint_timeout_for(
            f"{self._text} {frame.text}",
            terminal_secs=self._terminal_secs,
            default_secs=self._default_user_speech_timeout,
            continuation_secs=self._continuation_secs,
        )
        armed = self._user_speech_timeout_task
        await super()._handle_transcription(frame)
        await self._rearm_to_deadline(armed)

    async def _rearm_to_deadline(self, armed):
        """Re-arm the stale user-speech timer to the adaptive deadline. No-op if moot.

        Args:
            armed: the timer task as it was BEFORE the base class ran. If the handle
                changed, the base re-armed it and already picked up the new value.
        """
        if (
            self._vad_stopped_time is None
            or self._user_speech_wait_done
            or self._user_speech_timeout_task is None
            or self._user_speech_timeout_task is not armed
        ):
            return

        remaining = max(0.0, self._vad_stopped_time + self._user_speech_timeout - time.time())
        policy = self._user_speech_timeout
        self._user_speech_timeout = remaining
        try:
            await self._restart_user_speech_timer()
        finally:
            self._user_speech_timeout = policy


__all__ = ["BackchannelAwareUserTurnStartStrategy", "AdaptiveEndpointStopStrategy"]
