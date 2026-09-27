"""The pure deterministic stage transition (docs/03).

`next_stage(state, signals) -> Transition` reads the current stage + the turn's signals
and returns the next stage. It NEVER mutates state and makes NO I/O or model calls — the
whole point of docs/03 is that the machine, not the LLM, picks the stage. `turn.py` owns
the mutations (filling slots, incrementing counters, setting locked_slot) and calls this.

Counting contract: `state.objection_counts[obj]` already INCLUDES the current turn's
objection when `signals.objection` is set — `turn.py` increments before calling. So a
count of 2 here means "this is the second time we've heard this objection".
"""

from dataclasses import dataclass

from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import DISCOVERY_ORDER, CallState

DISCOVER_MAX_TURNS = len(DISCOVERY_ORDER)

VALUE_MAX_TURNS = 4

OBJECTION_ANSWER_CAP = 2


@dataclass(frozen=True)
class TurnSignals:
    """What the just-finished user turn told us (computed by turn.py from slot extraction
    + the objection classifier). All default to the 'nothing happened' value so a caller
    only sets what fired."""

    inquiry_confirmed: bool = False
    # Outbound only: they answered the permission question with "not right now". Not a
    # refusal and not an objection — the call is over for now (hard rule 6, one alternative
    # then close). It is recorded rather than acted on here so open simply does not advance;
    # what Roma SAYS to a deferral is the fragment's job.
    declined_now: bool = False
    discovery_slot_filled: bool = False
    # The lead asked what the COURSE is. In discover this outranks the discovery queue — see
    # `turn.asks_about_course` for the call that made it necessary.
    asks_about_course: bool = False
    # The lead has raised the visit time on two consecutive turns. In value/structure this outranks the
    # turn budget — see `turn.raises_the_visit_time` for the call that made it necessary.
    asks_to_book: bool = False
    slot_accepted: bool = False
    readback_confirmed: bool = False
    objection: "str | None" = None


@dataclass(frozen=True)
class Transition:
    """The machine's verdict for the next turn."""

    next_stage: ConversationStage
    hard_pivot: bool = False
    win: bool = False


def next_stage(state: CallState, signals: TurnSignals) -> Transition:
    """Deterministic next stage for docs/03. Pure — does not touch `state`."""
    obj = signals.objection
    if obj is not None:
        if state.objection_counts.get(obj, 0) >= OBJECTION_ANSWER_CAP:
            return Transition(ConversationStage.PIVOT, hard_pivot=True)
        return Transition(ConversationStage.OBJECTION)

    stage = state.stage

    # A lead who asks to book has said the one thing the whole call exists to produce, and
    # every stage before the offer is a way of EARNING that sentence. Honouring it wherever
    # the call happens to be beats walking someone who already decided through a pitch they
    # did not ask for.
    #
    # open INCLUDED, and it is the case that made this necessary. On bde258d1 the lead asked to
    # book twice while still in open; open was excluded, so the call advanced to discover instead, the
    # time-talk guard substituted the course line there, and the lead's reply was "आप क्यों
    # मुझे course के बारे में बता रहे हो". It took a third ask to get an offer.
    #
    # "Meeting fix karo" IS permission to talk — it is a stronger answer to "kya abhi 2 minute
    # baat ho sakti hai?" than "haan" is. Only a DEFERRAL still holds open: hard rule 6 gives a
    # busy lead one alternative and a warm close, never a slot.
    if (
        signals.asks_to_book
        and not signals.declined_now
        and stage
        in (
            ConversationStage.OPEN,
            ConversationStage.DISCOVER,
            ConversationStage.VALUE,
            ConversationStage.STRUCTURE,
            ConversationStage.OBJECTION,
        )
    ):
        return Transition(ConversationStage.PIVOT)

    if stage == ConversationStage.OPEN:
        return Transition(
            ConversationStage.DISCOVER if signals.inquiry_confirmed else ConversationStage.OPEN
        )

    if stage == ConversationStage.DISCOVER:
        # Asking what the course is IS the cue to go explain it. discover carries no course
        # content and forbids pitching, so holding the lead here to finish the slot queue
        # means answering the question badly, from the wrong prompt, for as long as the
        # timeout lasts (049f0dc1: six turns, five unanswered asks).
        if signals.asks_about_course:
            return Transition(ConversationStage.VALUE)
        done = state.filled_discovery_count() >= len(DISCOVERY_ORDER) or (
            state.stage_turn_count > DISCOVER_MAX_TURNS
        )
        return Transition(ConversationStage.VALUE if done else ConversationStage.DISCOVER)

    if stage == ConversationStage.VALUE:
        return Transition(
            ConversationStage.STRUCTURE
            if state.stage_turn_count >= VALUE_MAX_TURNS
            else ConversationStage.VALUE
        )

    if stage == ConversationStage.STRUCTURE:
        return Transition(ConversationStage.PIVOT)

    if stage == ConversationStage.PIVOT:
        return Transition(
            ConversationStage.CLOSE if signals.slot_accepted else ConversationStage.PIVOT
        )

    if stage == ConversationStage.OBJECTION:
        return Transition(ConversationStage.PIVOT)

    if stage == ConversationStage.CLOSE:
        if state.locked_slot is not None and signals.readback_confirmed:
            return Transition(ConversationStage.CLOSE, win=True)
        return Transition(ConversationStage.CLOSE)

    raise ValueError(f"unknown stage {stage!r}")


__all__ = [
    "next_stage",
    "TurnSignals",
    "Transition",
    "DISCOVER_MAX_TURNS",
    "VALUE_MAX_TURNS",
    "OBJECTION_ANSWER_CAP",
]
