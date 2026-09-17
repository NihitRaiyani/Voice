"""Hold the lead's first words until Roma has actually opened (docs/02, docs/03 P1).

## The bug this exists to stop

An outbound call opens with `LLMRunFrame()` queued at connect, which generates Roma's P1
greeting. But the lead has a phone to their ear and says "Hello" the moment they pick up —
so a `TranscriptionFrame` lands ~1s later, while that first generation is still in flight.
The user aggregator treats it as a complete user turn and fires a SECOND generation.

Observed on the first live tuning call (2026-07-26, CA21667bf):

    10:29:06.948  generate  [system]                 <- LLMRunFrame, the real opening
    10:29:07.284  User started speaking              <- "Hello"
    10:29:10.535  TTS  "Hi ji, Roma baat kar rahi hoon ... Kya ab baat karna theek hai?"
    10:29:19.022  TTS  "Hi ji, Roma baat kar rahi hoon ... Kya yeh baat karne ka achha samay hai?"

Roma greeted twice, back to back, and the lead's actual reply queued up behind eight
seconds of duplicate audio. From the lead's side that is an agent talking to itself.

## Why here and not in the aggregator

The aggregator is pipecat's and the turn strategies are shared with barge-in; the rule
"a lead cannot take a turn before the agent has spoken" is a property of an OUTBOUND call,
not of turn-taking in general. It belongs in one small processor on the input path.

## Why not just drop the frames

The text is kept and re-emitted once Roma finishes. A lead who answers the phone with
"Haan boliye" has told us something real — docs/03's P1 confirm reads exactly that kind of
reply — and silently discarding it would make Roma re-ask a question already answered.
Suppressing the *turn* is not the same as discarding the *words*.
"""

import asyncio
import logging
from typing import NamedTuple

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
    TextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.dialer.openerstore import SAMPLE_RATE as OPENER_RATE
from roma.guardrails.normalize import tokens

_log = logging.getLogger("roma.telephony")

_EXPECTED_SCRIPT_RANGES = (
    (0x0041, 0x005A),
    (0x0061, 0x007A),
    (0x0900, 0x097F),
    (0x0A80, 0x0AFF),
)


def _script_split(text: str) -> "tuple[int, int]":
    """(expected letters, other-script letters). Digits, spaces and punctuation are
    neutral and counted as neither — "7" and "।" say nothing about what language this is."""
    expected = other = 0
    for ch in text:
        code = ord(ch)
        if any(lo <= code <= hi for lo, hi in _EXPECTED_SCRIPT_RANGES):
            expected += 1
        elif ch.isalpha():
            other += 1
    return expected, other


def is_noise_transcript(text: str) -> bool:
    """True if this 'transcript' is line noise rather than something the lead said.

    Live evidence (call CA52acb68, 2026-07-26): STT returned finals of 1 and 2 characters
    between real answers. Each one opened a user turn, so Roma answered a breath — and
    because there was nothing to answer she filled the turn with "Samajh gayi" or
    "Mujhe maaf kijiye agar kuch galat samjhi" and then RE-ASKED the question the lead had
    already answered. That is what "she talks to herself" sounds like from the lead's side:
    not a monologue bug, a conversation with the noise floor.

    Three rules, all conservative, because dropping a real answer is far worse than
    answering a cough:

    * No surviving tokens (punctuation, stray marks) -> noise.
    * A SINGLE character -> noise. This is the line that needs care: `હા` and `ha` are real
      two-character affirmations that decide the P1 confirm, so the threshold sits below
      them deliberately. One character is never a word the lead meant to say.
    * A script Roma's leads do not speak -> noise. Added after CA3c7d3c7b (2026-07-28),
      where auto-detect STT decoded breaths as Tamil. The length rule caught three of them
      (`ஆ`) and missed `ஆமா`, which opened a user turn and got answered; the lead's next
      words were "पर मैंने तो कुछ बोला ही नहीं आपको" — "I didn't say anything to you at
      all". Auto-detect is still right (a code-switching lead needs it), but it means a
      non-speech sound is decoded as whatever language fits it best rather than rejected.

      STRICTLY MORE other-script letters than expected ones, so anything genuinely mixed
      survives. A lead saying "मैंने अभी B.Tech किया है" is the modal case here and must
      never be dropped.
    """
    if not text or not text.strip():
        return True
    if not tokens(text):
        return True
    if len(text.strip()) < 2:
        return True
    expected, other = _script_split(text)
    return other > expected


_PICKUP_TOKENS = {
    "hello",
    "hallo",
    "helo",
    "halo",
    "hi",
    "hey",
    "yello",
    "namaste",
    "namaskar",
    "kem",
    "cho",
    "हैलो",
    "हेलो",
    "नमस्ते",
    "नमस्कार",
    "હેલો",
    "હલો",
    "નમસ્તે",
    "નમસ્કાર",
    "કેમ",
    "છો",
}


def is_pickup_token(text: str) -> bool:
    """True if this is only the noise of answering a phone, with no content in it.

    Live evidence (CAfa2a011, 2026-07-26). `OpeningTurnGuard` correctly held the lead's
    pickup "Hello" while Roma greeted — and then released it as a user turn 4ms after she
    stopped, which fired a SECOND generation. Roma greeted, then immediately answered
    herself with "Hello, sun rahe hain? Kya aap digital marketing ke liye visit fix karne
    mein interested hain?". From the lead's side that is an agent talking to itself, which
    is precisely what the guard was written to prevent — it fixed the overlap and left the
    duplicate.

    Suppressing the turn was right; RELEASING the words was the mistake, because "Hello"
    carries nothing to respond to. Anything with content in it — "Haan boliye", "kaun bol
    raha hai?" — has a token outside `_PICKUP_TOKENS` (or is an affirmation, which is not
    in the set at all) and is still released, so the guard's original reason for keeping
    the text is intact.
    """
    toks = tokens(text)
    if not toks:
        return True
    return all(t.casefold() in _PICKUP_TOKENS for t in toks)


def _looks_affirmative(text: str) -> bool:
    """Whether the controller will read this as the P1 confirm — for LOGGING only.

    Imported lazily so this module keeps no import-time dependency on the controller: the
    telephony layer drives the controller, not the other way round.
    """
    from roma.controller.turn import is_affirmation

    return is_affirmation(text)


class NoiseGate(FrameProcessor):
    """Drop sub-word transcripts before they can open a user turn.

    Sits with the opening guard on the input path, for the same reason: "this is not a
    turn" is a property of the call, and the aggregator downstream has no way to tell a
    one-character final from a real `હા`.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dropped = 0

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and is_noise_transcript(frame.text):
            self.dropped += 1
            _log.info("noise gate: dropped a %d-char transcript", len(frame.text))
            _log.debug("noise gate dropped: %r", frame.text)
            return
        await self.push_frame(frame, direction)


class OpeningTurnGuard(FrameProcessor):
    """Withhold user transcripts until Roma's opening utterance has finished.

    Sits between STT and the user aggregator. Once the first `BotStoppedSpeakingFrame`
    arrives the guard is permanently open and every frame passes straight through — this
    costs one boolean check per frame for the rest of the call.
    """

    def __init__(self, *, opener_is_raw_audio: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        # A RAW-AUDIO opener means there is nothing to guard, and guarding anyway is fatal.
        #
        # Named for the property that actually matters — "this opener emits no
        # `BotStoppedSpeakingFrame`" — and not for how the clip was obtained. Until
        # 2026-08-05 both this and `PickupGreeter` took a flag called `opening_is_canned`,
        # and the two meant DIFFERENT things: here "nothing will open the gate", there
        # "someone else already spoke, stand down". A pre-rendered outbound opener is the
        # case where those answers diverge, and `media.py` fed one value to both. See
        # `PickupGreeter.__init__` for what that cost.
        #
        # This gate opens on `BotStoppedSpeakingFrame`, which the TTS service emits when a
        # GENERATED utterance finishes. `canned.opening_line()` is queued at connect as raw
        # `OutputAudioRawFrame`, so that frame never arrives — the gate stays shut for the
        # whole call and every word the caller says is held and never released. Roma says
        # "Hello, Weltec Institute" and then never speaks again.
        #
        # Measured on the first working live call (6cb580df, 2026-07-31): STT transcribed
        # "Hello" and then "कुछ तो बोलिए आगे" — the lead literally asking her to say
        # something — while the log repeated `held a transcript ... Roma still opening`.
        #
        # Same shape as the dead watchdog disarm: a guard keyed on a frame the new
        # architecture stopped producing. Nothing to wait for, so start open.
        self.opened = opener_is_raw_audio
        self.held: list[str] = []

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if not self.opened and isinstance(frame, BotStoppedSpeakingFrame):
            self.opened = True
            await self.push_frame(frame, direction)
            held, self.held = self.held, []
            if held:
                merged = " ".join(held)
                if is_pickup_token(merged):
                    _log.info("opening guard: dropped a pickup-only transcript")
                    _log.debug("opening guard dropped: %r", merged)
                else:
                    _log.info(
                        "opening guard: releasing %d held transcript(s) "
                        "(tokens=%d, pickup=False, affirmation=%s)",
                        len(held),
                        len(tokens(merged)),
                        _looks_affirmative(merged),
                    )
                    _log.debug("opening guard released: %r", merged)
                    await self.push_frame(
                        TranscriptionFrame(merged, "lead", ""), FrameDirection.DOWNSTREAM
                    )
            return

        if not self.opened and isinstance(frame, TranscriptionFrame):
            self.held.append(frame.text)
            _log.info(
                "opening guard: held a transcript (%d chars) — Roma still opening",
                len(frame.text),
            )
            _log.debug("opening guard held: %r", frame.text)
            return

        if not self.opened and isinstance(frame, InterimTranscriptionFrame):
            return

        await self.push_frame(frame, direction)


class OpeningPosture(NamedTuple):
    """How the two opening processors must be configured for one particular call.

    Named fields, not a bare `(bool, bool)`, because the entire point of this type is that
    the two values are NOT interchangeable and were once passed as a single flag.
    """

    already_spoken: bool
    """Something else spoke the opener at connect — `PickupGreeter` must stand down."""

    opener_is_raw_audio: bool
    """The opener emits no `BotStoppedSpeakingFrame` — `OpeningTurnGuard` must start open."""


def opening_posture(
    *, is_outbound: bool, has_canned_line: bool, has_prerendered_opener: bool
) -> OpeningPosture:
    """Decide both opening flags from the three facts that determine them.

    A function, and not three lines inline in `media.py`, for one reason: inline in a
    websocket handler these were UNTESTABLE, and the bug that put them here was a wiring
    bug that every unit test in `test_opener_cache.py` and `test_opening_guard.py` passed
    straight through. Those tests hand the processors literal booleans, so they prove each
    processor behaves correctly WHEN TOLD CORRECTLY — never that anything told it.

    The row that matters is outbound + pre-rendered, where the two answers DIVERGE:

        | call      | canned line | pre-rendered | already_spoken | raw_audio |
        |-----------|-------------|--------------|----------------|-----------|
        | inbound   | yes         | n/a          | True           | True      |
        | outbound  | n/a         | no           | False          | False     |
        | outbound  | n/a         | YES          | False          | TRUE      |

    That last row is call 0ce455b0 (2026-08-04). One flag carried both answers, so the
    greeter was told the opener was handled (it stood down, and NOTHING was ever played:
    `outbound_frames=0`) while the guard was never told it was raw audio (it stayed shut and
    held all six transcripts). Roma neither spoke nor listened for five minutes and thirteen
    seconds while the lead said "बोलिए" into the silence.
    """
    already_spoken = has_canned_line and not is_outbound
    return OpeningPosture(
        already_spoken=already_spoken,
        opener_is_raw_audio=already_spoken or has_prerendered_opener,
    )


__all__ = [
    "OpeningTurnGuard",
    "NoiseGate",
    "OpeningPosture",
    "PickupGreeter",
    "is_noise_transcript",
    "is_pickup_token",
    "opening_posture",
]


class PickupGreeter(FrameProcessor):
    """Hold Roma's opening until the lead has said something — or briefly, until they don't.

    ## What this changes

    An outbound call used to queue `LLMRunFrame()` the instant the media stream connected,
    so the sequence was: the lead answers, then three to four seconds of silence while the
    greeting is generated and synthesised, then Roma talks over whatever they said in the
    meantime. Measured on CAfe5a00b: LLM TTFB 3.28s on the cold first turn plus TTS.

    A real call does not work like that. The person who answers says "Hello?" and the
    caller replies. So the greeting now waits for the lead's first sound, and the silence
    becomes what silence on a phone call normally is — the other person's turn — rather
    than a line that appears to be broken.

    ## The fallback is not optional

    Plenty of people answer and say nothing at all, and a greeting that waits forever for
    a sound that never comes is a dead call. `max_wait_secs` bounds it: speak anyway, and
    log which of the two paths fired so the split is measurable rather than assumed.

    ## Why VAD and not the transcript

    The transcript arrives roughly a second after the words (Sarvam p50 was 0.954s on the
    same call), and that second is spent doing nothing. `VADUserStartedSpeakingFrame` fires
    at the onset of speech, which is the moment a person would have started replying.
    `TranscriptionFrame` is accepted too, as a backstop for a call with no VAD at all —
    the offline tests run that way.
    """

    def __init__(
        self,
        # 2.0 -> 1.0 on 2026-08-01. This timeout is NOT the whole delay: it starts when the
        # first frame reaches this processor, which on call e3237622 was 1.8s after the media
        # stream opened (STT connect, pipeline spin-up). 2.0 + 1.8 put Roma's "Hello" 4.4s
        # after pickup, and the lead's report was a late greeting.
        #
        # The wait exists so Roma does not talk over a callee still saying "hello" — one
        # second covers that, and it no longer has to cover LLM latency at all now that the
        # opener is spoken from the template (`llm.prompts.opening_line`).
        max_wait_secs: float = 1.0,
        *,
        opening_already_spoken: bool = False,
        opening_text_fn=None,
        opening_audio_fn=None,
        connected_at: "float | None" = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._max_wait_secs = max_wait_secs
        # `time.monotonic()` at the moment the media stream opened, so the wait can measure
        # what its docstring has always claimed it measures. Without it the timer starts when
        # `StartFrame` reaches this processor, which is AFTER pipeline construction — 0.789s
        # on call 8517d576 — and the callee heard 1.797s of silence for a 1.0s wait, with
        # the opener bytes sitting ready since 0.267s. None keeps the old behaviour for the
        # offline tests that build this processor directly.
        self._connected_at = connected_at
        # "Something ELSE already said the opener at connect, so stand down" — INBOUND only.
        #
        # This is NOT "the opener is canned audio", which is what it used to be called, and
        # the difference is the whole of call 0ce455b0: five minutes, `outbound_frames=0`,
        # the lead saying "बोलिए" into total silence. A pre-rendered OUTBOUND opener is
        # canned audio, so the old name read true — but nothing had spoken it. This
        # processor OWNS it, and standing down meant it was never played at all.
        #
        # `media.py` compounded that by feeding the same value to `OpeningTurnGuard`, which
        # needed the opposite answer, so Roma simultaneously never spoke and never listened.
        # Two names now, because there were always two questions.
        self._opening_already_spoken = opening_already_spoken
        self._opening_text_fn = opening_text_fn
        self._opening_audio_fn = opening_audio_fn
        self.greeted = False
        self._timer = None
        self._timer_unavailable = False

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        # INBOUND: everything above describes an OUTBOUND call, where Roma is the one who
        # dialled and must not talk over a callee still saying "hello". Roma is now the one
        # who ANSWERS, so she speaks first and instantly, from `canned.opening_line()` — and
        # this processor has nothing left to do. Worse than nothing, in fact: firing
        # `LLMRunFrame` here would generate a second unprompted line on top of the canned
        # one, so a caller who stayed quiet for two seconds would hear Roma greet them and
        # then start talking to herself. The caller's own first utterance drives the next
        # turn through the normal path; a caller who never speaks is the silence watchdog's
        # problem, not this one's.
        #
        # Reached ONLY when someone else already spoke. An outbound call with a pre-rendered
        # opener falls through to the timer and the speech trigger below, exactly as an
        # outbound call without one does — the clip changes how `_greet` speaks, never
        # whether it runs.
        if self._opening_already_spoken:
            return

        # Arm on StartFrame, which reaches every processor at pipeline start — BEFORE STT
        # connects. This processor sits AFTER SarvamSTTService, so waiting for any other
        # frame means waiting for STT to warm up first: on call baf9cbe9 the socket opened at
        # 40.037 and Sarvam's first result did not land until 43.4s, so a 1.0s timer could
        # not even begin until 3.4s in and the greeting was 4.3s late. The timeout must
        # measure "since the call connected", not "since STT started working".
        self._start_timer()

        if self.greeted:
            return
        if isinstance(frame, (VADUserStartedSpeakingFrame, TranscriptionFrame)):
            await self._greet("the lead spoke")

    def _start_timer(self) -> None:
        if self._timer is not None or self._timer_unavailable or self.greeted:
            return
        coro = self._wait_then_greet()
        try:
            self._timer = self.create_task(coro)
        except Exception:  # noqa: BLE001 — no task manager (direct-mode tests)
            coro.close()
            self._timer_unavailable = True
            _log.warning("could not start the greeting timer; greeting on speech only")

    def _remaining_wait(self) -> float:
        """What is LEFT of the wait, counting from when the call connected.

        The wait exists to avoid talking over a callee still saying "hello", and that
        window opens at pickup — not when this processor happens to receive its first
        frame. Time already spent building the pipeline has already given the callee their
        chance to speak, so spending it again is pure dead air.
        """
        if self._connected_at is None:
            return self._max_wait_secs
        import time

        return max(0.0, self._max_wait_secs - (time.monotonic() - self._connected_at))

    async def _wait_then_greet(self) -> None:
        try:
            await asyncio.sleep(self._remaining_wait())
            if not self.greeted:
                await self._greet(f"no sound from the lead in {self._max_wait_secs:.1f}s")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the timer must never take the call down
            _log.exception("greeting timer failed; the call may open in silence")

    def _prerendered_opener(self) -> "bytes | None":
        """The opener as bytes, if it was rendered during the ring (`dialer.openerstore`).

        None on absence OR on any failure, which routes `_greet` to the template-through-TTS
        path it used before this existed. That ordering is the whole safety story: the fast
        path is additive, and every way it can go wrong lands on the path that already works.

        No `safe_output` here, and that is not an omission — these bytes are no longer text
        and cannot be screened. The filter ran at RENDER time, in
        `openerstore.render_opener_audio`, which is why that function takes the text and not
        a pre-made clip.
        """
        if self._opening_audio_fn is None:
            return None
        try:
            pcm = self._opening_audio_fn()
        except Exception:  # noqa: BLE001 — a cache miss must never cost the greeting
            _log.warning("pre-rendered opener unavailable; synthesizing instead")
            return None
        return pcm if isinstance(pcm, (bytes, bytearray)) and pcm else None

    def _spoken_opener(self) -> "str | None":
        if self._opening_text_fn is None:
            return None
        try:
            text = self._opening_text_fn()
        except Exception:  # noqa: BLE001 — fall back to the LLM, never drop the greeting
            _log.warning("could not render the opener; falling back to generating it")
            return None
        return text.strip() or None if isinstance(text, str) else None

    async def _greet(self, reason: str) -> None:
        """Kick off Roma's opening turn exactly once.

        The opener is SPOKEN, not generated, whenever `opening_text_fn` yields a line.
        `phases/p1_open.md` orders the model to say one exact sentence, so asking for it costs
        a network round trip to be told what we already know — and on calls acf8e78f and
        c5672a23 (2026-08-01) that round trip stalled (64s, 18.6s) and both calls were silent
        end to end. The lead had just picked up and heard nothing.

        Emitted as the full response trio rather than a bare `TextFrame`, so everything
        downstream behaves exactly as it does for a generated turn: `PreTTSFilterProcessor`
        keys `_first_of_turn` on the start frame (and still runs `safe_output` on the line —
        this skips the LLM, never the guardrail), and the assistant aggregator records the
        greeting into the context, without which Roma's own opener would be missing from the
        history she reasons over for the rest of the call.

        Any failure to render falls back to `LLMRunFrame`, the old path: a slow greeting is
        worse than an instant one, and both are far better than none.
        """
        if self.greeted:
            return
        self.greeted = True
        pcm = self._prerendered_opener()
        if pcm is not None:
            _log.info("opening: greeting now (%s) — pre-rendered audio, no TTS", reason)
            await self.push_frame(
                OutputAudioRawFrame(audio=pcm, sample_rate=OPENER_RATE, num_channels=1),
                FrameDirection.DOWNSTREAM,
            )
            return
        line = self._spoken_opener()
        if line is None:
            _log.info("opening: greeting now (%s) — generating", reason)
            await self.push_frame(LLMRunFrame(), FrameDirection.DOWNSTREAM)
            return
        _log.info("opening: greeting now (%s) — spoken from template, no LLM", reason)
        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await self.push_frame(TextFrame(line), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    async def cleanup(self) -> None:
        if self._timer is not None:
            await self.cancel_task(self._timer)
            self._timer = None
        await super().cleanup()
