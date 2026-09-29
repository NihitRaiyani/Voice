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
# Turn 1 → ask name
# Turn 2 → ask current status
# Turn 3 → ask education
# Turn 4 → ask passing year
# Turn 5 → ask city

VALUE_MAX_TURNS = 4
# means Roma can stay in the value stage for at most 4 turns.
# VALUE turn 1 → explain course benefit
# VALUE turn 2 → talk about placement
# VALUE turn 3 → explain practical learning
# VALUE turn 4 → final value point

OBJECTION_ANSWER_CAP = 2
# lapp ne 2 var aj tackle karse
# Lead: Fees bahut zyada hai.

# Roma answers objection       → 1

# Lead: Still expensive hai.

# Roma answers again           → 2



@dataclass(frozen=True)
class TurnSignals:
    """What the just-finished user turn told us (computed by turn.py from slot extraction
    + the objection classifier). All default to the 'nothing happened' value so a caller
    only sets what fired."""

    inquiry_confirmed: bool = False
    
    declined_now: bool = False
    
    discovery_slot_filled: bool = False
    
    asks_about_course: bool = False
    
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
