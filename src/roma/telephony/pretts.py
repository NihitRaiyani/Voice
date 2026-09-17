"""Pre-TTS guardrail processor (docs/04, roma-guardrail contract).

Sits between the sentence aggregator and Bulbul TTS. Every sentence Roma is about to
speak is routed through `safe_output()` first — a router, not a censor: a blocked line
is replaced with a safe substitution, never left as dead air, and on any internal error
`safe_output` hard-fails to a canned safe line. **Raw LLM text must never reach TTS.**

Only Roma's own output is filtered. `TranscriptionFrame`/`InterimTranscriptionFrame` are
`TextFrame` subclasses (user speech) — they are explicitly excluded so a lead's words are
never rewritten. By pipeline position transcripts don't reach here anyway; the exclusion
is defensive on the one path that must not fail.

## The second gate

`confirmguard.safe_confirmation` runs on the same frames, for the same reason and in the
same shape: Roma must not announce a booking the controller did not make. It is separate
from `safe_output` because it is not a docs/04 concern — the filter screens what Roma says
about money and credentials, this screens what she says about the machine's own state —
and folding it into the lexicon would put call state inside a pure text filter.

Order matters: the confirmation gate runs FIRST. Its substitution is ordinary Hinglish with
nothing blockable in it, but it is still Roma's line, so it goes through `safe_output` like
every other. Nothing reaches TTS unscreened, whichever gate produced it.
"""

import logging
import re

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.controller.confirmguard import (
    safe_availability,
    safe_close,
    safe_confirmation,
    safe_time_talk,
)
from roma.guardrails import safe_output
from roma.telephony.filler import strip_leading_ack

_log = logging.getLogger("roma.telephony")

# "11:00 baje" -> "11 baje". Bulbul runs with `enable_preprocessing=True`, which reads a
# digit clock time as a full Hindi time — "11:00" becomes "gyaarah baje" — and then speaks
# the literal "baje" that follows it too. On call 8b9a9df3 the lead heard "gyaarah baje
# baje" and asked "बजे बजे दो बार क्यों बोला आपने".
#
# Fixed here rather than in the prompt because it is not a judgement call: "11:00 baje" is
# never what should reach the wire, whatever the model meant by it. `spoken_slot()` already
# emits "11:00 AM" and is not the source — this is the model writing the two forms together.
#
# On the hour the minutes are dropped, so "gyaarah" + "baje" reads correctly. Off the hour
# the trailing word goes instead, leaving the preprocessor to voice the whole time.
_CLOCK_ON_HOUR_BAJE = re.compile(r"\b(\d{1,2}):00\s*(baje|बजे|બજે)", re.IGNORECASE)
_CLOCK_OFF_HOUR_BAJE = re.compile(r"\b(\d{1,2}:[0-5]\d)\s*(?:baje|बजे|બજે)", re.IGNORECASE)


def strip_duplicate_baje(text: str) -> str:
    """Remove the "baje" that Bulbul's own time preprocessing would say a second time."""
    try:
        out = _CLOCK_ON_HOUR_BAJE.sub(r"\1 \2", text)
        return _CLOCK_OFF_HOUR_BAJE.sub(r"\1", out)
    except Exception:  # noqa: BLE001 — a cosmetic fix must never cost a turn
        return text


class PreTTSFilterProcessor(FrameProcessor):
    """Apply the confirmation gate and `safe_output()` to each sentence before TTS.

    `slot_status_fn` returns the controller's current verdict on the lead's proposed time.
    A callable, so it is read when the sentence is about to be spoken rather than when the
    pipeline was built. Omitting it disables the confirmation gate, which is what the
    docs/04 filter tests use.
    """

    def __init__(
        self,
        slot_status_fn=None,
        phase_fn=None,
        filler_fn=None,
        wants_out_fn=None,
        facts_said_fn=None,
        slot_fn=None,
    ) -> None:
        super().__init__()
        self._slot_status_fn = slot_status_fn
        self._phase_fn = phase_fn
        self._filler_fn = filler_fn
        self._wants_out_fn = wants_out_fn
        self._facts_said_fn = facts_said_fn
        self._slot_fn = slot_fn
        self._first_of_turn = True
        self._substituted_this_turn = False
        self._spoke_this_turn = False
        self.last_spoken: str | None = None
        self.dupe_drops = 0
        self.signoff_holds = 0
        self.time_talk_holds = 0
        self.denial_holds = 0

    def _slot_status(self) -> str:
        if self._slot_status_fn is None:
            return "accepted"
        try:
            return str(self._slot_status_fn())
        except Exception:  # noqa: BLE001 — unreadable state fails towards "do not confirm"
            _log.warning("confirmation gate could not read slot_status; assuming none")
            return "none"

    def _phase(self) -> str:
        """The phase Roma is speaking in. Read at frame time, like `_slot_status`. With no
        call state wired, report an OFFER phase so the gate never rewrites a unit test's
        line — same posture as `_slot_status` defaulting to "accepted"."""
        if self._phase_fn is None:
            return "p5_pivot"
        try:
            return str(self._phase_fn())
        except Exception:  # noqa: BLE001 — unreadable state fails towards "do not schedule"
            _log.warning("time-talk gate could not read the phase; assuming the course")
            return "p3_value"

    def _wants_out(self) -> bool:
        """Has the lead asked the call to stop? Read at frame time like the others.

        Unreadable state fails towards NOT holding the sign-off: the cost of a wrong `True`
        is Roma closing a turn early, the cost of a wrong `False` is the machine pushing a
        lead who asked it to go away. The second is the bug this exists to prevent.
        """
        if self._wants_out_fn is None:
            return False
        try:
            return bool(self._wants_out_fn())
        except Exception:  # noqa: BLE001
            _log.warning("sign-off guard could not read lead_wants_out; allowing the close")
            return True

    def _slot(self) -> str:
        """The accepted slot as Roma would say it, so the hold can read it back instead of
        re-asking for a time she already has. Empty on any failure, which routes
        `hold_line` to the ask — the pre-existing behaviour."""
        if self._slot_fn is None:
            return ""
        try:
            return str(self._slot_fn() or "")
        except Exception:  # noqa: BLE001 — never let this decide whether a turn happens
            return ""

    def _facts_said(self) -> "list[str] | None":
        """Which course facts are already spent, so the steer does not re-spend one.

        `None` on any failure, which routes `steer_line` to the fact-bearing default —
        the pre-existing behaviour, and the safe direction: an unreadable fact list must
        not silently turn the steer into a line that answers nothing.
        """
        if self._facts_said_fn is None:
            return None
        try:
            return list(self._facts_said_fn())
        except Exception:  # noqa: BLE001 — never let this decide whether a turn happens
            return None

    def _filler_played(self) -> bool:
        if self._filler_fn is None:
            return False
        try:
            return bool(self._filler_fn())
        except Exception:  # noqa: BLE001 — never let this decide whether a turn happens
            return False

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._first_of_turn = True
            self._substituted_this_turn = False
            self._spoke_this_turn = False
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TextFrame)
            and not isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
        ):
            line = frame.text
            if self._first_of_turn and self._filler_played():
                line = strip_leading_ack(line)
            self._first_of_turn = False
            after_ack = line
            status = self._slot_status()
            line = safe_confirmation(line, status, self._phase())
            denied = safe_availability(line, status)
            if denied != line:
                self.denial_holds += 1
            line = denied
            held = safe_close(
                line,
                status,
                self.signoff_holds,
                wants_out=self._wants_out(),
                slot=self._slot(),
            )
            if held != line:
                self.signoff_holds += 1
            steered = safe_time_talk(
                held, self._phase(), self._facts_said(), self.time_talk_holds
            )
            if steered != held:
                self.time_talk_holds += 1
            safe = safe_output(steered)
            if safe != after_ack:
                _log.info("pre-TTS filter substituted a blocked line")
                if self._substituted_this_turn:
                    self.dupe_drops += 1
                    _log.info("pre-TTS filter dropped a second substitution in one turn")
                    return
                # A substitution that carries no question, landing after Roma has already
                # spoken this turn, adds nothing and can contradict her. Call 02ef08d7:
                #
                #   "Tuesday 4 August ko subah 11 baje theek rahega? Abhi wo time final
                #    nahi hua hai."
                #
                # She asked, then the guard's statement said the time was not settled. The
                # blocked sentence still must not air — dropping it is the STRONGEST form of
                # that, and docs/04's rule is about dead air, which cannot happen when the
                # turn has already produced audible text.
                if self._spoke_this_turn and "?" not in safe:
                    self.dupe_drops += 1
                    _log.info("pre-TTS filter dropped a trailing statement substitution")
                    return
                self._substituted_this_turn = True
                safe = " " + safe.strip() + " "
            self._spoke_this_turn = True
            # Last, on whatever text actually won — a canned substitution can carry a clock
            # time too, and this must apply to the bytes that reach Bulbul, not to a draft.
            safe = strip_duplicate_baje(safe)
            frame.text = safe
            self.last_spoken = safe
        await self.push_frame(frame, direction)


__all__ = ["PreTTSFilterProcessor"]
