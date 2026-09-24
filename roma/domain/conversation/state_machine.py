"""Formal facade for Roma's software-owned conversation state machine.

This module is the narrow interface application code should use when it needs stage semantics.
It wraps the existing pure transition table and checkpoint store without importing FastAPI,
provider SDKs, SQLAlchemy, or Redis clients.
"""

from __future__ import annotations

from typing import Protocol

from roma.domain.conversation.machine import Transition, TurnSignals, next_phase
from roma.domain.conversation.stage import ConversationStage, phase_for, stage_for
from roma.domain.conversation.state import CallState


class ConversationStateStore(Protocol):
    async def load(self, call_sid: str) -> CallState | None: ...

    async def save(self, state: CallState) -> None: ...


_ALLOWED_TRANSITIONS: dict[ConversationStage, frozenset[ConversationStage]] = {
    ConversationStage.OPEN: frozenset(
        {ConversationStage.OPEN, ConversationStage.DISCOVER, ConversationStage.PIVOT}
    ),
    ConversationStage.DISCOVER: frozenset(
        {
            ConversationStage.DISCOVER,
            ConversationStage.VALUE,
            ConversationStage.PIVOT,
            ConversationStage.OBJECTION,
        }
    ),
    ConversationStage.VALUE: frozenset(
        {
            ConversationStage.VALUE,
            ConversationStage.STRUCTURE,
            ConversationStage.PIVOT,
            ConversationStage.OBJECTION,
        }
    ),
    ConversationStage.STRUCTURE: frozenset(
        {ConversationStage.PIVOT, ConversationStage.OBJECTION}
    ),
    ConversationStage.PIVOT: frozenset(
        {ConversationStage.PIVOT, ConversationStage.OBJECTION, ConversationStage.CLOSE}
    ),
    ConversationStage.OBJECTION: frozenset(
        {ConversationStage.OBJECTION, ConversationStage.PIVOT}
    ),
    ConversationStage.CLOSE: frozenset({ConversationStage.CLOSE, ConversationStage.OBJECTION}),
}


def _coerce_stage(stage: ConversationStage | str) -> ConversationStage:
    if isinstance(stage, ConversationStage):
        return stage
    if stage.startswith("p"):
        return stage_for(stage)
    return ConversationStage(stage)


def get_state(state: CallState) -> ConversationStage:
    """Return the public business stage for a call checkpoint."""
    return stage_for(state.phase)


def can_transition(current: ConversationStage | str, target: ConversationStage | str) -> bool:
    """Return whether the target stage is allowed by the finite-state graph."""
    current_stage = _coerce_stage(current)
    target_stage = _coerce_stage(target)
    return target_stage in _ALLOWED_TRANSITIONS[current_stage]


def transition(state: CallState, signals: TurnSignals) -> Transition:
    """Compute the next machine transition without mutating the call state."""
    result = next_phase(state, signals)
    if not can_transition(state.phase, result.next_phase):
        raise ValueError(f"invalid transition {state.phase!r} -> {result.next_phase!r}")
    return result


async def save_state(store: ConversationStateStore, state: CallState) -> None:
    """Persist a checkpoint through the configured state-store adapter."""
    await store.save(state)


async def restore_state(store: ConversationStateStore, call_sid: str) -> CallState | None:
    """Restore a checkpoint for a resumed call."""
    return await store.load(call_sid)


def handle_interruption(state: CallState) -> Transition:
    """Barge-in/interruption does not advance the business stage."""
    return Transition(phase_for(get_state(state)))


def handle_objection(state: CallState, objection: str) -> Transition:
    """Record an objection occurrence and route through the objection transition table."""
    state.objection_counts[objection] = state.objection_counts.get(objection, 0) + 1
    return transition(state, TurnSignals(objection=objection))


def handle_missing_information(state: CallState) -> Transition:
    """Record a failed discovery attempt and let the machine decide whether to keep asking."""
    if get_state(state) == ConversationStage.DISCOVER:
        slot = state.next_discovery_slot()
        if slot is not None:
            state.record_slot_attempt(slot)
    return transition(state, TurnSignals())


__all__ = [
    "ConversationStage",
    "ConversationStateStore",
    "can_transition",
    "get_state",
    "handle_interruption",
    "handle_missing_information",
    "handle_objection",
    "restore_state",
    "save_state",
    "transition",
]
