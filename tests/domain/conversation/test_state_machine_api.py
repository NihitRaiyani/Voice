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
from roma.domain.conversation.state import CallState


def test_public_stage_enum_is_the_single_conversation_vocabulary():
    assert tuple(stage.value for stage in ConversationStage) == (
        "open",
        "discover",
        "value",
        "structure",
        "pivot",
        "objection",
        "close",
    )


def test_get_state_returns_public_stage_name():
    assert get_state(CallState(stage="discover")) == ConversationStage.DISCOVER


def test_can_transition_exposes_the_finite_state_graph():
    assert can_transition(ConversationStage.OPEN, ConversationStage.DISCOVER)
    assert can_transition("pivot", ConversationStage.CLOSE)
    assert can_transition(ConversationStage.CLOSE, ConversationStage.OBJECTION)
    assert not can_transition(ConversationStage.CLOSE, ConversationStage.DISCOVER)


def test_transition_keeps_the_machine_as_the_authority():
    state = CallState(stage="open")
    result = transition(state, TurnSignals(inquiry_confirmed=True))
    assert result.next_stage == "discover"
    assert get_state(state) == ConversationStage.OPEN


def test_call_state_rejects_unknown_stage():
    with pytest.raises(ValueError, match="made_up"):
        CallState(stage="made_up")


def test_handle_interruption_holds_the_current_stage():
    state = CallState(stage="value", stage_turn_count=2)
    result = handle_interruption(state)
    assert result.next_stage == "value"
    assert state.stage == "value"
    assert state.stage_turn_count == 2


@pytest.mark.parametrize("stage", list(ConversationStage))
def test_handle_objection_records_and_routes_to_objection_stage(stage):
    state = CallState(stage=stage)
    result = handle_objection(state, "fees")
    assert state.objection_counts == {"fees": 1}
    assert result.next_stage == "objection"
    assert can_transition(stage, ConversationStage.OBJECTION)
    assert state.stage == stage


def test_repeated_objection_hard_pivots_without_reanswering():
    state = CallState(stage="objection", objection_counts={"fees": 1})
    result = handle_objection(state, "fees")
    assert state.objection_counts == {"fees": 2}
    assert result.next_stage == "pivot"
    assert result.hard_pivot is True


def test_handle_missing_information_records_discovery_attempt():
    state = CallState(stage="discover")
    result = handle_missing_information(state)
    assert state.slot_attempts == {"lead_name": 1}
    assert result.next_stage == "discover"


def test_save_and_restore_state_uses_the_store_boundary():
    async def run() -> None:
        store = InMemoryCallStateStore()
        state = CallState(call_sid="CA123", stage="structure", lead_name="Asha")
        await save_state(store, state)
        restored = await restore_state(store, "CA123")
        assert restored is not None
        assert restored.call_sid == "CA123"
        assert restored.stage == "structure"
        assert restored.lead_name == "Asha"

    asyncio.run(run())
