"""The pure deterministic phase transition (docs/03).

`next_phase(state, signals) -> Transition` reads the current phase + the turn's signals
and returns the next phase. It NEVER mutates state and makes NO I/O or model calls — the
whole point of docs/03 is that the machine, not the LLM, picks the phase. `turn.py` owns
the mutations (filling slots, incrementing counters, setting locked_slot) and calls this.

Counting contract: `state.objection_counts[obj]` already INCLUDES the current turn's
objection when `signals.objection` is set — `turn.py` increments before calling. So a
count of 2 here means "this is the second time we've heard this objection".
"""

from dataclasses import dataclass

from roma.controller.state import DISCOVERY_ORDER, CallState

P1_OPEN = "p1_open"
P2_DISCOVER = "p2_discover"
P3_VALUE = "p3_value"
P4_STRUCTURE = "p4_structure"
P5_PIVOT = "p5_pivot"
P6_OBJECTION = "p6_objection"
P7_CLOSE = "p7_close"

P2_MAX_TURNS = len(DISCOVERY_ORDER)

P3_MAX_TURNS = 4

OBJECTION_ANSWER_CAP = 2


@dataclass(frozen=True)
class TurnSignals:
    """What the just-finished user turn told us (computed by turn.py from slot extraction
    + the objection classifier). All default to the 'nothing happened' value so a caller
    only sets what fired."""

    inquiry_confirmed: bool = False
    # Outbound only: they answered the permission question with "not right now". Not a
    # refusal and not an objection — the call is over for now (hard rule 6, one alternative
    # then close). It is recorded rather than acted on here so P1 simply does not advance;
    # what Roma SAYS to a deferral is the fragment's job.
    declined_now: bool = False
    discovery_slot_filled: bool = False
    # The lead asked what the COURSE is. In P2 this outranks the discovery queue — see
    # `turn.asks_about_course` for the call that made it necessary.
    asks_about_course: bool = False
    # The lead has raised the visit time on two consecutive turns. In P3/P4 this outranks the
    # turn budget — see `turn.raises_the_visit_time` for the call that made it necessary.
    asks_to_book: bool = False
    slot_accepted: bool = False
    readback_confirmed: bool = False
    objection: "str | None" = None


@dataclass(frozen=True)
class Transition:
    """The machine's verdict for the next turn."""

    next_phase: str
    hard_pivot: bool = False
    win: bool = False


def next_phase(state: CallState, signals: TurnSignals) -> Transition:
    """Deterministic next phase for docs/03. Pure — does not touch `state`."""
    obj = signals.objection
    if obj is not None:
        if state.objection_counts.get(obj, 0) >= OBJECTION_ANSWER_CAP:
            return Transition(P5_PIVOT, hard_pivot=True)
        return Transition(P6_OBJECTION)

    phase = state.phase

    # A lead who asks to book has said the one thing the whole call exists to produce, and
    # every phase before the offer is a way of EARNING that sentence. Honouring it wherever
    # the call happens to be beats walking someone who already decided through a pitch they
    # did not ask for.
    #
    # P1 INCLUDED, and it is the case that made this necessary. On bde258d1 the lead asked to
    # book twice while still in P1; P1 was excluded, so the call advanced to P2 instead, the
    # time-talk guard substituted the course line there, and the lead's reply was "आप क्यों
    # मुझे course के बारे में बता रहे हो". It took a third ask to get an offer.
    #
    # "Meeting fix karo" IS permission to talk — it is a stronger answer to "kya abhi 2 minute
    # baat ho sakti hai?" than "haan" is. Only a DEFERRAL still holds P1: hard rule 6 gives a
    # busy lead one alternative and a warm close, never a slot.
    if (
        signals.asks_to_book
        and not signals.declined_now
        and phase in (P1_OPEN, P2_DISCOVER, P3_VALUE, P4_STRUCTURE, P6_OBJECTION)
    ):
        return Transition(P5_PIVOT)

    if phase == P1_OPEN:
        return Transition(P2_DISCOVER if signals.inquiry_confirmed else P1_OPEN)

    if phase == P2_DISCOVER:
        # Asking what the course is IS the cue to go explain it. P2 carries no course
        # content and forbids pitching, so holding the lead here to finish the slot queue
        # means answering the question badly, from the wrong prompt, for as long as the
        # timeout lasts (049f0dc1: six turns, five unanswered asks).
        if signals.asks_about_course:
            return Transition(P3_VALUE)
        done = state.filled_discovery_count() >= len(DISCOVERY_ORDER) or (
            state.phase_turn_count > P2_MAX_TURNS
        )
        return Transition(P3_VALUE if done else P2_DISCOVER)

    if phase == P3_VALUE:
        return Transition(P4_STRUCTURE if state.phase_turn_count >= P3_MAX_TURNS else P3_VALUE)

    if phase == P4_STRUCTURE:
        return Transition(P5_PIVOT)

    if phase == P5_PIVOT:
        return Transition(P7_CLOSE if signals.slot_accepted else P5_PIVOT)

    if phase == P6_OBJECTION:
        return Transition(P5_PIVOT)

    if phase == P7_CLOSE:
        if state.locked_slot is not None and signals.readback_confirmed:
            return Transition(P7_CLOSE, win=True)
        return Transition(P7_CLOSE)

    raise ValueError(f"unknown phase {phase!r}")


__all__ = [
    "next_phase",
    "TurnSignals",
    "Transition",
    "P1_OPEN",
    "P2_DISCOVER",
    "P3_VALUE",
    "P4_STRUCTURE",
    "P5_PIVOT",
    "P6_OBJECTION",
    "P7_CLOSE",
    "P2_MAX_TURNS",
    "P3_MAX_TURNS",
    "OBJECTION_ANSWER_CAP",
]
