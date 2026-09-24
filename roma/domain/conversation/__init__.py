"""Roma conversation controller (docs/03 phase machine, docs/06 call-state).

The machine decides WHAT happens next; the LLM only decides HOW to say it. It never
picks the phase, the slot, or "objection handled" — that is what makes the flow fixed.

Public surface:
- `CallState` / phase + discovery constants — the per-call datum (docs/03).
- `next_phase` / `TurnSignals` / `Transition` — the pure deterministic transition (docs/03).
"""

from roma.domain.conversation.machine import Transition, TurnSignals, next_phase
from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import (
    DISCOVERY_ORDER,
    PHASES,
    CallState,
)
from roma.domain.conversation.state_machine import (
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
from roma.repositories.redis.conversation_state import (
    CallStateStore,
    InMemoryCallStateStore,
    RedisCallStateStore,
)

__all__ = [
    "CallState",
    "PHASES",
    "DISCOVERY_ORDER",
    "ConversationStage",
    "next_phase",
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
    "RedisCallStateStore",
]
