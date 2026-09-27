"""Exhaustive coverage of the docs/03 transition table. The machine is pure, so every
row is a plain (state, signals) -> Transition assertion.

Counting contract (see machine.py): `objection_counts[obj]` already includes the current
turn's objection when `signals.objection` is set — turn.py increments before calling. So
count==1 means "first time heard" (answer it, objection); count>=2 means "heard again" (hard pivot).
"""

import dataclasses

from roma.domain.conversation.machine import (
    DISCOVER_MAX_TURNS,
    VALUE_MAX_TURNS,
    TurnSignals,
    next_stage,
)
from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState

FILLED = dict(
    lead_name="Asha",
    education="12th",
    passing_year="2020",
    current_status="student",
    city="Vadodara",
    timing_constraint="evenings",
)


def test_open_inquiry_confirmed_advances_to_discover():
    s = CallState(stage=ConversationStage.OPEN)
    assert (
        next_stage(s, TurnSignals(inquiry_confirmed=True)).next_stage
        == ConversationStage.DISCOVER
    )


def test_open_not_confirmed_stays():
    s = CallState(stage=ConversationStage.OPEN)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.OPEN


def test_discover_all_five_slots_filled_advances_to_value():
    s = CallState(stage=ConversationStage.DISCOVER, **FILLED)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.VALUE


def test_discover_incomplete_stays():
    s = CallState(stage=ConversationStage.DISCOVER, education="12th", stage_turn_count=2)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.DISCOVER


def test_discover_turn_budget_exceeded_advances_even_if_incomplete():
    s = CallState(stage=ConversationStage.DISCOVER, education="12th", stage_turn_count=7)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.VALUE


def test_discover_turn_budget_boundary_holds_at_the_valve():
    """One turn per slot is enough for a cooperative lead, so the valve sits exactly at
    `len(DISCOVERY_ORDER)` — it must not fire ON that turn, only past it."""
    s = CallState(
        stage=ConversationStage.DISCOVER, education="12th", stage_turn_count=DISCOVER_MAX_TURNS
    )
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.DISCOVER
    s.stage_turn_count = DISCOVER_MAX_TURNS + 1
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.VALUE


def test_value_holds_for_a_second_course_turn_then_advances():
    """value was single-turn, which gave the lead exactly ONE turn about the course before the
    booking push — the first of which is spent on the qualifying question. On CAfe5a00b
    they asked "course hai kis liye" and got half a sentence plus "aapne kab aana hai?".

    Two turns: ask, then actually answer. Bounded by VALUE_MAX_TURNS, and the five-minute
    budget still force-pivots out of value at three minutes."""
    first = CallState(stage=ConversationStage.VALUE, stage_turn_count=1)
    assert next_stage(first, TurnSignals()).next_stage == ConversationStage.VALUE

    second = CallState(stage=ConversationStage.VALUE, stage_turn_count=VALUE_MAX_TURNS)
    assert next_stage(second, TurnSignals()).next_stage == ConversationStage.STRUCTURE


def test_an_objection_still_leaves_value_immediately():
    """The extra turn must not trap a lead who raises an objection — the short-circuit at
    the top of `next_stage` runs before any stage logic."""
    s = CallState(stage=ConversationStage.VALUE, stage_turn_count=1)
    assert next_stage(s, TurnSignals(objection="cost")).next_stage == "objection"


def test_structure_advances_to_pivot_after_one_turn():
    assert (
        next_stage(CallState(stage=ConversationStage.STRUCTURE), TurnSignals()).next_stage
        == ConversationStage.PIVOT
    )


def test_pivot_slot_accepted_advances_to_close():
    s = CallState(stage=ConversationStage.PIVOT)
    assert next_stage(s, TurnSignals(slot_accepted=True)).next_stage == ConversationStage.CLOSE


def test_pivot_no_acceptance_stays():
    s = CallState(stage=ConversationStage.PIVOT)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.PIVOT


def test_pivot_objection_routes_to_objection():
    s = CallState(stage=ConversationStage.PIVOT, objection_counts={"fees": 1})
    t = next_stage(s, TurnSignals(objection="fees"))
    assert t.next_stage == ConversationStage.OBJECTION and not t.hard_pivot


def test_objection_answered_returns_forward_to_pivot():
    s = CallState(stage=ConversationStage.OBJECTION)
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.PIVOT


def test_objection_never_returns_backward_to_close():
    s = CallState(stage=ConversationStage.OBJECTION, accepted_slot="Mon 6pm")
    assert next_stage(s, TurnSignals()).next_stage == ConversationStage.PIVOT


def test_objection_same_objection_twice_hard_pivots_to_pivot_no_reanswer():
    s = CallState(stage=ConversationStage.OBJECTION, objection_counts={"fees": 2})
    t = next_stage(s, TurnSignals(objection="fees"))
    assert t.next_stage == ConversationStage.PIVOT and t.hard_pivot is True


def test_objection_different_objection_is_answered_in_objection():
    s = CallState(stage=ConversationStage.OBJECTION, objection_counts={"fees": 1, "no_time": 1})
    t = next_stage(s, TurnSignals(objection="no_time"))
    assert t.next_stage == ConversationStage.OBJECTION and not t.hard_pivot


def test_close_objection_routes_to_objection():
    s = CallState(stage=ConversationStage.CLOSE, objection_counts={"no_time": 1})
    assert (
        next_stage(s, TurnSignals(objection="no_time")).next_stage
        == ConversationStage.OBJECTION
    )


def test_close_readback_confirmed_with_locked_slot_wins():
    s = CallState(stage=ConversationStage.CLOSE, locked_slot="2026-07-27T18:00")
    t = next_stage(s, TurnSignals(readback_confirmed=True))
    assert t.next_stage == ConversationStage.CLOSE and t.win is True


def test_close_readback_confirmed_without_locked_slot_does_not_win():
    s = CallState(stage=ConversationStage.CLOSE, locked_slot=None)
    assert next_stage(s, TurnSignals(readback_confirmed=True)).win is False


def test_close_no_confirmation_stays_without_winning():
    s = CallState(stage=ConversationStage.CLOSE, locked_slot="2026-07-27T18:00")
    t = next_stage(s, TurnSignals())
    assert t.next_stage == ConversationStage.CLOSE and t.win is False


def test_next_stage_does_not_mutate_state():
    s = CallState(stage=ConversationStage.DISCOVER, **FILLED)
    before = dataclasses.asdict(s)
    next_stage(s, TurnSignals(inquiry_confirmed=True, objection="fees"))
    assert dataclasses.asdict(s) == before


def test_a_lead_pushing_for_a_time_in_value_gets_the_offer_instead_of_another_course_question():
    """Call 0f09c8a3, `time_talk_holds=4`. value carries no OFFER line, so every attempt the
    lead made to name a time was caught by the time-talk guard and replaced with "course ke
    baare mein aur kya jaanna chahenge?" — four turns running, at a lead who was trying to
    book. The guard is right that ROMA must not raise a time in value; it has no way to know
    the LEAD did. The machine does."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="value")
    state.stage_turn_count = 1
    assert (
        next_stage(state, TurnSignals(asks_to_book=True)).next_stage == ConversationStage.PIVOT
    )


def test_one_passing_mention_of_a_time_does_not_skip_the_pitch():
    """`asks_to_book` is only set after two consecutive turns; a single mention leaves value
    alone. Pivoting on one would offer a slot to someone still deciding."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="value")
    state.stage_turn_count = 1
    assert (
        next_stage(state, TurnSignals(asks_to_book=False)).next_stage == ConversationStage.VALUE
    )


def test_an_objection_still_outranks_the_booking_pivot():
    """Objection handling is checked first and must stay that way — a lead raising a doubt
    and a time in the same breath needs the doubt answered, not a slot."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="value")
    sig = TurnSignals(asks_to_book=True, objection="placement_doubt")
    assert next_stage(state, sig).next_stage == ConversationStage.OBJECTION


def test_a_lead_who_asks_to_book_gets_the_offer_from_any_pre_offer_stage():
    """Call ca529641. The lead asked to schedule and Roma answered, three turns running:

        "par is waqt main course ke details share kar rahi hoon visit abhi schedule
         nahi kar rahi. Aapko pehle course ki value clear honi chahiye."
        "visit abhi schedule wahi hota hai jab mujhe prompt ho."

    She was obeying the stage; the stage was wrong. Someone who already knows what they want
    should not have to sit through the pitch to earn a slot. The earlier fix only jumped
    from value, so a lead asking in discover or structure still got walked through it."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    for stage in ("discover", "value", "structure", "objection"):
        state = CallState(stage=stage)
        state.stage_turn_count = 1
        got = next_stage(state, TurnSignals(asks_to_book=True)).next_stage
        assert got == ConversationStage.PIVOT, f"{stage} refused to pivot (went to {got})"


def test_open_holds_only_for_a_deferral_not_for_a_booking_ask():
    """SUPERSEDED by call bde258d1. This test used to assert that open never pivots, on the
    reasoning that nothing may happen before the lead agrees to talk. The call showed the
    reasoning was wrong: "meeting schedule fix karo" IS that agreement, and refusing to act
    on it sent the lead to discover for a course pitch they had explicitly declined.

    What open must still hold for is a DEFERRAL — see the two tests at the end of this file."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="open")
    sig = TurnSignals(asks_to_book=True, declined_now=True, inquiry_confirmed=False)
    assert next_stage(state, sig).next_stage == ConversationStage.OPEN


def test_asking_to_book_in_open_is_permission_and_pivots():
    """Call bde258d1. The lead asked to book TWICE in open ("meeting schedule fix karo").
    open was excluded, so the call advanced to discover instead, the time-talk guard substituted the
    course line there, and the lead answered "आप क्यों मुझे course के बारे में बता रहे हो".
    It took a third ask to get an offer.

    "Meeting fix karo" is a stronger answer to "kya abhi 2 minute baat ho sakti hai?" than
    "haan" is."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="open")
    sig = TurnSignals(asks_to_book=True, inquiry_confirmed=False)
    assert next_stage(state, sig).next_stage == ConversationStage.PIVOT


def test_a_deferral_still_holds_open_even_with_a_booking_word():
    """Hard rule 6: a busy lead gets one alternative and a warm close, never a slot. This is
    the one thing open's exclusion was protecting, and it is kept explicitly."""
    from roma.domain.conversation.machine import TurnSignals, next_stage
    from roma.domain.conversation.state import CallState

    state = CallState(stage="open")
    sig = TurnSignals(asks_to_book=True, declined_now=True, inquiry_confirmed=False)
    assert next_stage(state, sig).next_stage == ConversationStage.OPEN
