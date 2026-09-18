"""The phase controller as a Pipecat processor (docs/03, docs/11).

Sits between the user aggregator and the LLM. On each finalized user turn it:

  1. runs `advance_turn` — extract slots / classify objection / pick the next phase
     (the machine decides, never the model) and checkpoint to the store;
  2. swaps the context's system message IN PLACE to the new phase's assembled prompt
     (in place, so the persona->hard_rules prefix stays byte-identical for prompt caching —
     never `LLMMessagesUpdateFrame`, which rewrites the whole context);
  3. pushes an `LLMUpdateSettingsFrame` setting `max_tokens` from the phase's word cap;

then forwards the context frame to the LLM. Forwarding only AFTER the swap is what makes
"the controller picks the phase" a frame-ordering invariant — the LLM never reads the
context before the mutation lands (docs/03).

The opening turn (an `LLMRunFrame` with no user message yet) carries no user text, so the
controller leaves the seeded P1 prompt + P1 max_tokens untouched. Any error inside the turn
logic is swallowed — Roma still speaks the prior phase's prompt (the guardrail always gates
the actual line); a controller bug must never drop the call.
"""

import asyncio
import contextlib
import hashlib
import logging
import time
from datetime import datetime

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMUpdateSettingsFrame,
    OutputAudioRawFrame,
    TextFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import LLMSettings

from roma.controller.facts import facts_in
from roma.controller.machine import P7_CLOSE
from roma.controller.offtopic import deflection_for
from roma.controller.shortcircuit import canned_reply
from roma.controller.state import CallState
from roma.controller.timeresolve import IST
from roma.controller.turn import advance_turn
from roma.llm.prompts import assemble_system_prompt, cache_prefix, phase_max_tokens
from roma.telephony.filler import SAMPLE_RATE as FILLER_RATE
from roma.telephony.filler import intent_for

_log = logging.getLogger("roma.telephony")


# How long a turn must be preparing before the line is HELD ("Ek second…").
#
# 9.0s, and raised twice because it kept catching turns that were merely SLOW.
#
# The numbers it has to sit between: an ordinary turn is ~4.2s end to end (median on
# 049f0dc1, range 2.3-5.7s), while a real stall is 18-64s (acf8e78f, c5672a23). At 1.1s it
# narrated every pause — 13 clips in 7 turns, and the lead asked "what is this manner". At
# 6.0s it still fired 3 times in 4 turns on ordinary discovery. 9.0s is clear of any turn
# that is going to arrive on its own, and still covers the silences that lose calls.
#
# Set to 1.1s first, and live call 1cd20b57 played 13 holds across 7 turns because every
# ordinary turn tripped it. The lead: "1 second जी 1 second जी लगे रखा है बार बार आप repeat
# कर रहे हो, what is this manner" — the same tic as the "accha accha" complaint the filler
# suppression exists to prevent, rebuilt by hand. Cover a stall; never narrate a pause.
HOLD_AFTER_SECS = 9.0

# Gap between held beats once holding has started.
HOLD_REPEAT_SECS = 4.0

# Held-line clips per turn. Two says "still here"; a third is Roma audibly stalling, and
# past that the honest outcome is ending the call, not more pretending.
PACER_MAX_CLIPS = 2


def _role(msg) -> "str | None":
    return msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)


def _last_user_text(context) -> "str | None":
    """The text of the most recent user message in the context, or None (e.g. the opening
    turn, which has only the system message)."""
    for msg in reversed(context.get_messages()):
        if _role(msg) == "user":
            content = (
                msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            )
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                return " ".join(t for t in parts if t)
            return None
    return None


def _swap_system_prompt(context, text: str) -> None:
    """Replace the system message content in place (preserving the cached prefix shape)."""
    for msg in context.get_messages():
        if _role(msg) == "system":
            if isinstance(msg, dict):
                msg["content"] = text
            else:  # pragma: no cover - seeded as a dict in media.py
                msg.content = text
            return
    context.get_messages().insert(0, {"role": "system", "content": text})


def _last_assistant_text(context) -> "str | None":
    """The text of Roma's most recent turn, or None before she has spoken.

    Read off the context rather than tapped out of the TTS path on purpose: what reaches
    the context is what she actually said, AFTER the pre-TTS filter may have substituted a
    line, and a fact she never spoke must not be marked as spent.
    """
    for msg in reversed(context.get_messages()):
        if _role(msg) == "assistant":
            content = (
                msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            )
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                return " ".join(t for t in parts if t)
            return None
    return None


class PhaseControllerProcessor(FrameProcessor):
    """Drive the 7-phase machine per user turn and set the phase prompt + max_tokens."""

    def __init__(
        self,
        state: CallState,
        *,
        client=None,
        store=None,
        now_fn=lambda: datetime.now(IST),
        elapsed_fn=None,
        fillers=None,
        holding=None,
        flight=None,
        health=None,
        **advance_kwargs,
    ) -> None:
        super().__init__()
        self.state = state
        self._client = client
        self._store = store
        self._now_fn = now_fn
        self._started_at = time.monotonic()
        self._elapsed_fn = elapsed_fn or (lambda: time.monotonic() - self._started_at)
        self._advance_kwargs = advance_kwargs
        self.won = False
        self._fillers = fillers
        self._holding = holding
        self._filler_last_turn = False
        self.filler_this_turn = False
        self.advance_secs: list[float] = []
        self._pacer = None
        self.pacer_clips = 0
        self._flight = flight
        self._health = health
        self._last_driven_text: str | None = None
        self.repeat_skips = 0
        # Diagnostics for the prompt cache (see `_note_prefix`). `prefix_hash` is read by
        # `_UsageLogger` so each spend row carries the prefix its request was billed against.
        self.prefix_hash: str | None = None
        self.prefix_changes = 0
        self.short_circuits = 0
        self.deflections = 0

    def _note_prefix(self, prompt_vars: dict, prompt: str, phase: str) -> None:
        """Record a digest of the cached span, and shout when it moves mid-call.

        `cache_prefix` (persona -> hard_rules) is what OpenAI's prompt cache keys on, and it
        is supposed to be byte-identical for the whole call. Measured across 296 gpt-4o
        requests it holds on most turns (93-96% cached input) but collapses to ~1920 cached
        tokens on roughly one turn in three — BELOW the prefix length, which two very
        different things could explain: something of ours varying, or OpenAI's cache evicting
        and re-routing. Those have opposite fixes, so this logs the evidence that tells them
        apart instead of guessing. A constant hash across a call with collapsing
        `cached` counts puts it on their side; a hash that moves puts it on ours and names
        the turn it moved.

        Never raises: this is a diagnostic on the live audio path.
        """
        try:
            digest = hashlib.sha256(cache_prefix(prompt_vars).encode("utf-8")).hexdigest()[:12]
            full = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
            if self.prefix_hash is not None and digest != self.prefix_hash:
                self.prefix_changes += 1
                _log.warning(
                    "prompt cache prefix CHANGED mid-call (%s -> %s) entering %s — every "
                    "turn from here re-pays the full prefix",
                    self.prefix_hash,
                    digest,
                    phase,
                )
            self.prefix_hash = digest
            _log.info("prompt: prefix=%s full=%s phase=%s", digest, full, phase)
        except Exception:  # noqa: BLE001 — instrumentation must never cost a turn
            _log.warning("prompt cache digest failed; continuing without it")

    async def _maybe_short_circuit(self, frame) -> bool:
        """Answer from the lexicon and skip the completion, or return False to ask the model.

        Runs AFTER `_drive`, so `canned_reply` reads the state the machine just decided —
        `lead_wants_out` and `slot_status` are only correct once `advance_turn` has landed.

        The line is emitted as the full response trio, exactly as `PickupGreeter` does for
        the opener (`opening.py:412-415`), so nothing downstream can tell the difference: the
        pre-TTS filter still keys `_first_of_turn` on the start frame and still runs
        `safe_output` on the text, and the assistant aggregator still records what Roma said
        into the context she reasons over next turn. **This skips the LLM, never the
        guardrail** — `pretts` sits below this processor, so the line goes through it on the
        way to Bulbul like any other.

        Returns True when it spoke, in which case the caller must NOT forward the context
        frame — forwarding it is what would make the LLM answer a turn already answered.
        """
        try:
            user_text = _last_user_text(frame.context)
            if user_text is None:
                return False
            line = canned_reply(self.state, user_text)
            if not line:
                # Off-topic turns are the fourth short-circuitable family: the reply does
                # not depend on what was asked, only that it was off the script. Checked
                # AFTER canned_reply so a pending readback or sign-off always outranks a
                # deflection.
                line = deflection_for(self.state, user_text)
                if line:
                    self.deflections += 1
            if not line:
                return False
            self.short_circuits += 1
            # Nothing left to mask: the reply is a string constant, not a round trip.
            await self._stop_pacer()
            await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
            await self.push_frame(TextFrame(line), FrameDirection.DOWNSTREAM)
            await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
            _log.info("short-circuit: answered without a completion (#%d)", self.short_circuits)
            return True
        except Exception:  # noqa: BLE001 — on ANY failure, fall through to the model
            _log.exception("short-circuit failed; forwarding the turn to the LLM")
            return False

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        # Roma has started speaking, so nothing needs covering any more. This — not the end
        # of `_drive` — is when the wait is actually over.
        if isinstance(frame, (BotStartedSpeakingFrame, TTSAudioRawFrame)):
            await self._stop_pacer()
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame):
            if self._flight is not None:
                self._flight.turn_started()
            await self._emit_filler(frame)
            self._start_pacer()
            # NO `finally: _stop_pacer()` here, deliberately. `_drive` is slot extraction
            # only (~0.9s); the LLM call happens AFTER this frame is forwarded downstream.
            # Cancelling here left the 18-64s window that actually stalls completely
            # uncovered — on acf8e78f and c5672a23 the lead heard a live, silent line for
            # 57s and 28s and hung up. The pacer now runs until Roma's audio starts, or
            # until the pipeline tears down (`cleanup`).
            await self._drive(frame)
            if await self._maybe_short_circuit(frame):
                return  # the context frame is NOT forwarded: the LLM never sees this turn
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await self._stop_pacer()
        await super().cleanup()

    def _should_fill(self, frame: LLMContextFrame) -> bool:
        """Whether this turn gets a latency mask.

        Live call CA3c7d3c7b (2026-07-28) was the first ever run with `enable_filler` on,
        and the lead's report was "repeatedly accha ji is coming which is annoying me".
        Every one of Roma's fourteen turns had opened with a clip. Rotation was working as
        designed — a clip on EVERY turn is the tic, independent of how many clips exist, so
        widening the pool alone would not have fixed it.

        Two suppressions, both deliberately cheap and stateless-ish:

        * Never twice running. Halves the rate and breaks the metronome, which is what makes
          it read as a verbal habit rather than a pause.
        * Never in P7. Closing turns are capped at twenty-five words, so the clip is a large
          fraction of the line it introduces, and "achha… Theek hai, milte hain" is two
          acknowledgements stacked on a goodbye.
        """
        if not self._fillers or _last_user_text(frame.context) is None:
            return False
        if self.state.phase == P7_CLOSE:
            return False
        return not self._filler_last_turn

    async def _emit_filler(self, frame: LLMContextFrame) -> None:
        """Play one cached filler clip downstream. Never raises, never blocks on I/O."""
        if not self._should_fill(frame):
            self._filler_last_turn = False
            self.filler_this_turn = False
            return
        self._filler_last_turn = True
        intent = intent_for(_last_user_text(frame.context), self.state.phase)
        clip = self._fillers.next(intent)
        if clip is None:
            self.filler_this_turn = False
            return
        self.filler_this_turn = True
        try:
            await self.push_frame(
                OutputAudioRawFrame(audio=clip.pcm, sample_rate=FILLER_RATE, num_channels=1),
                FrameDirection.DOWNSTREAM,
            )
            self._fillers.note_played()
            # Logged on SUCCESS, not only on failure. Without this the only trace a filler
            # ever played is the teardown counter, and a call that does not tear down
            # cleanly leaves no trace at all — so "was the lead's dead air masked?" became
            # unanswerable after call 98aa06a3. A silent success is not observability.
            _log.info("filler: played %s (%s, %s)", clip.name, intent.value, self.state.phase)
        except Exception:  # noqa: BLE001 — a latency mask must never cost the turn itself
            _log.warning("filler emit failed; continuing unmasked")
            if self._health is not None:
                self._health.degrade("filler_emit")

    def _start_pacer(self) -> None:
        # REMOVED 2026-08-02, at the lead's third and final complaint: "Just remove this bro.
        # What is this? एक second, एक minute रुकिए." Before that: "1 second जी 1 second जी लगे
        # रखा है बार बार आप repeat कर रहे हो, what is this manner", and "आप जो बोल रहे थे वो
        # ठीक ही बोल रहे थे सही flow में जा रहे थे, 1 second आपने क्यों बोला" — it interrupted
        # a conversation that was going WELL.
        #
        # It was added to cover 57s and 28s stalls, and it did. But it was re-tuned three
        # times (1.1s -> 6s -> 9s) and still fired 4 times in 6 turns, because on this link
        # ordinary turns run past 9s. A cover that fires on ordinary turns is not a cover,
        # it is a tic — the same lesson `_should_fill` already learned from "accha accha".
        #
        # The two causes of the ACTUAL stalls are fixed at the source now: the opener no
        # longer waits on the LLM (`llm.prompts.opening_line`) and a stalled completion is
        # capped and retried (`media.build_llm`). The remaining exposure is a long silence on
        # a genuinely broken link, which the lead has explicitly chosen over being narrated.
        #
        # The machinery stays — clips, picker, `_pace_filler`, tests — because the failure it
        # answers is real and will come back on a worse link. Set HOLD_AFTER_SECS from config
        # and re-enable here if it does. Do not re-enable it below ~9s.
        return

        """Begin covering the wait, whether or not an opening clip fired.

        No longer gated on `filler_this_turn`. That gate meant the turns with NO opening
        filler — every other turn, by `_should_fill`'s anti-tic rule — were also the turns
        with no cover at all if they stalled.

        NEVER in P7. The close is a readback and a goodbye over an already-locked visit, and
        on call e3237622 the lead heard "ek second ji" while Roma was confirming a booking he
        had just agreed to. Holding the line implies more is coming; at the close, nothing is.
        Same exclusion `_should_fill` already makes, for the same reason.
        """
        if self.state.phase == P7_CLOSE:
            return
        try:
            self._pacer = asyncio.create_task(self._pace_filler())
        except RuntimeError:  # pragma: no cover - no running loop (sync test driver)
            self._pacer = None

    async def _stop_pacer(self) -> None:
        pacer, self._pacer = self._pacer, None
        if pacer is None:
            return
        pacer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pacer

    async def _pace_filler(self) -> None:
        """Hold the line while the turn is still being prepared.

        Cancelled the moment Roma's audio starts (`process_frame`), so it is structurally
        incapable of talking over her. It survives `_drive` returning, which is the whole
        point: `_drive` is slot extraction, and the window that actually stalls is the LLM
        call after it.

        Two clips, then it stops. On acf8e78f the line was live and silent for 57 seconds;
        two held beats say "still here", and a third would be Roma audibly stalling. Past
        that the honest outcome is `CallCloser` ending the call, not more pretending.

        Uses HOLDING_LINES ("Ek second…"), never the acknowledgement fillers: "Achha…"
        arriving eight seconds late is an answer to something the lead has stopped waiting
        for, and reads worse than the silence it replaced.
        """
        try:
            await asyncio.sleep(HOLD_AFTER_SECS)
            for i in range(PACER_MAX_CLIPS):
                if i:
                    await asyncio.sleep(HOLD_REPEAT_SECS)
                clip = self._holding.next() if self._holding else None
                if clip is None:
                    return
                self.pacer_clips += 1
                _log.info("holding the line (%s) — turn still preparing", clip.name)
                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=clip.pcm, sample_rate=FILLER_RATE, num_channels=1
                    ),
                    FrameDirection.DOWNSTREAM,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a latency mask must never cost the turn itself
            _log.warning("filler pacer failed; continuing unmasked")
            if self._health is not None:
                self._health.degrade("filler_pacer")

    async def _drive(self, frame: LLMContextFrame) -> None:
        context = frame.context
        user_text = _last_user_text(context)
        if user_text is None:
            return
        if user_text == self._last_driven_text:
            self.repeat_skips += 1
            _log.info("phase controller: same utterance re-driven; not advancing again")
            return
        self._last_driven_text = user_text
        # Mark whatever Roma said LAST turn as spent, before this turn's prompt is built.
        # Done here rather than at TTS time so it reflects the line that actually reached
        # the context, and so a barge-in that truncates her turn does not mark the unsaid
        # half as said (`controller.facts`).
        try:
            spoken = _last_assistant_text(context)
            if spoken:
                self.state.record_facts(facts_in(spoken))
        except Exception:  # noqa: BLE001 — bookkeeping must never cost the turn
            _log.warning("could not record spoken facts; repetition guard is blind this turn")
        started = time.monotonic()
        try:
            transition = await advance_turn(
                self.state,
                user_text,
                client=self._client,
                now=self._now_fn(),
                store=self._store,
                elapsed_secs=self.elapsed(),
                **self._advance_kwargs,
            )
        except Exception:  # noqa: BLE001 — a phase-advance bug must not kill the call
            _log.exception("phase controller failed; holding phase %s", self.state.phase)
            if self._health is not None:
                self._health.degrade("phase_advance")
            return
        finally:
            self.advance_secs.append(time.monotonic() - started)

        phase = self.state.phase
        prompt_vars = self.state.as_prompt_vars()
        prompt = assemble_system_prompt(prompt_vars, phase)
        self._note_prefix(prompt_vars, prompt, phase)
        _swap_system_prompt(context, prompt)
        await self.push_frame(
            LLMUpdateSettingsFrame(delta=LLMSettings(max_tokens=phase_max_tokens(phase))),
            FrameDirection.DOWNSTREAM,
        )
        if transition.win and not self.won:
            self.won = True
            _log.info("phase controller: WIN — visit locked at %s", self.state.locked_slot)

    def elapsed(self) -> float:
        """Seconds since the call connected. Read by `CallCloser` for the hard deadline."""
        try:
            return max(0.0, float(self._elapsed_fn()))
        except Exception:  # noqa: BLE001 — a broken clock must not end or stall the call
            _log.warning("pacing clock unreadable; treating the call as just-started")
            if self._health is not None:
                self._health.degrade("pacing_clock")
            return 0.0


__all__ = ["PhaseControllerProcessor"]
