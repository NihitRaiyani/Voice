"""Roma conversation controller (docs/03 stage machine, docs/06 call-state).

The machine decides WHAT happens next; the LLM only decides HOW to say it. It never
picks the stage, the slot, or "objection handled" — that is what makes the flow fixed.

Public surface:
- `CallState` / stage + discovery constants — the per-call datum (docs/03).
- `next_stage` / `TurnSignals` / `Transition` — the pure deterministic transition (docs/03).
"""

from roma.domain.conversation.machine import Transition, TurnSignals, next_stage
from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import (
    DISCOVERY_ORDER,
    STAGES,
    CallState,
)
from roma.domain.conversation.state_machine import (
    CallStateStore,
    InMemoryCallStateStore,
    InMemoryConversationStateStore,
    can_transition,
    get_state,
    handle_interruption,
    handle_missing_information,
    handle_objection,
    restore_state,
    save_state,
    transition,
)
from roma.domain.conversation.turn import advance_turn

__all__ = [
    "CallState",
    "STAGES",
    "DISCOVERY_ORDER",
    "ConversationStage",
    "next_stage",
    "transition",
    "get_state",
    "can_transition",
    "save_state",
    "restore_state",
    "handle_interruption",
    "handle_objection",
    "handle_missing_information",
    "TurnSignals",
    "Transition",
    "advance_turn",
    "CallStateStore",
    "InMemoryCallStateStore",
    "InMemoryConversationStateStore",
]
