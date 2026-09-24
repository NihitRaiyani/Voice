import asyncio

import pytest
from roma.domain.conversation import (
    ConversationStage,
    InMemoryCallStateStore,
    TurnSignals,
    can_transition,
    get_state,
    handle_interruption,
    handle_missing_information,
    handle_objection,
    restore_state,
    save_state,
    transition,
)
from roma.domain.conversation.stage import phase_for, stage_for
from roma.domain.conversation.state import CallState


def test_public_stage_enum_maps_to_existing_prompt_phases():
    assert phase_for(ConversationStage.OPEN) == "p1_open"
    assert phase_for(ConversationStage.DISCOVER) == "p2_discover"
    assert phase_for(ConversationStage.VALUE) == "p3_value"
    assert phase_for(ConversationStage.STRUCTURE) == "p4_structure"
    assert phase_for(ConversationStage.PIVOT) == "p5_pivot"
    assert phase_for(ConversationStage.OBJECTION) == "p6_objection"
    assert phase_for(ConversationStage.CLOSE) == "p7_close"
    assert stage_for("p5_pivot") == ConversationStage.PIVOT


def test_get_state_returns_public_stage_name():
    assert get_state(CallState(phase="p2_discover")) == ConversationStage.DISCOVER


def test_can_transition_exposes_the_finite_state_graph():
    assert can_transition(ConversationStage.OPEN, ConversationStage.DISCOVER)
    assert can_transition("p5_pivot", ConversationStage.CLOSE)
    assert can_transition(ConversationStage.CLOSE, ConversationStage.OBJECTION)
    assert not can_transition(ConversationStage.CLOSE, ConversationStage.DISCOVER)


def test_transition_keeps_the_machine_as_the_authority():
    state = CallState(phase="p1_open")
    result = transition(state, TurnSignals(inquiry_confirmed=True))
    assert result.next_phase == "p2_discover"
    assert get_state(state) == ConversationStage.OPEN


def test_transition_rejects_unknown_current_phase():
    with pytest.raises(ValueError, match="unknown phase"):
        transition(CallState(phase="made_up"), TurnSignals())


def test_handle_interruption_holds_the_current_stage():
    state = CallState(phase="p3_value", phase_turn_count=2)
    result = handle_interruption(state)
    assert result.next_phase == "p3_value"
    assert state.phase == "p3_value"
    assert state.phase_turn_count == 2


def test_handle_objection_records_and_routes_to_objection_stage():
    state = CallState(phase="p5_pivot")
    result = handle_objection(state, "fees")
    assert state.objection_counts == {"fees": 1}
    assert result.next_phase == "p6_objection"


def test_repeated_objection_hard_pivots_without_reanswering():
    state = CallState(phase="p6_objection", objection_counts={"fees": 1})
    result = handle_objection(state, "fees")
    assert state.objection_counts == {"fees": 2}
    assert result.next_phase == "p5_pivot"
    assert result.hard_pivot is True


def test_handle_missing_information_records_discovery_attempt():
    state = CallState(phase="p2_discover")
    result = handle_missing_information(state)
    assert state.slot_attempts == {"lead_name": 1}
    assert result.next_phase == "p2_discover"


def test_save_and_restore_state_uses_the_store_boundary():
    async def run() -> None:
        store = InMemoryCallStateStore()
        state = CallState(call_sid="CA123", phase="p4_structure", lead_name="Asha")
        await save_state(store, state)
        restored = await restore_state(store, "CA123")
        assert restored is not None
        assert restored.call_sid == "CA123"
        assert restored.phase == "p4_structure"
        assert restored.lead_name == "Asha"

    asyncio.run(run())
