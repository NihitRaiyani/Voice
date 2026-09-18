"""Exhaustive coverage of the docs/03 transition table. The machine is pure, so every
row is a plain (state, signals) -> Transition assertion.

Counting contract (see machine.py): `objection_counts[obj]` already includes the current
turn's objection when `signals.objection` is set — turn.py increments before calling. So
count==1 means "first time heard" (answer it, P6); count>=2 means "heard again" (hard pivot).
"""

import dataclasses

from roma.domain.conversation.machine import (
    P1_OPEN,
    P2_DISCOVER,
    P2_MAX_TURNS,
    P3_MAX_TURNS,
    P3_VALUE,
    P4_STRUCTURE,
    P5_PIVOT,
    P6_OBJECTION,
    P7_CLOSE,
    TurnSignals,
    next_phase,
)
from roma.domain.conversation.state import CallState

FILLED = dict(
    lead_name="Asha",
    education="12th",
    passing_year="2020",
    current_status="student",
    city="Vadodara",
    timing_constraint="evenings",
)


def test_p1_inquiry_confirmed_advances_to_p2():
    s = CallState(phase=P1_OPEN)
    assert next_phase(s, TurnSignals(inquiry_confirmed=True)).next_phase == P2_DISCOVER


def test_p1_not_confirmed_stays():
    s = CallState(phase=P1_OPEN)
    assert next_phase(s, TurnSignals()).next_phase == P1_OPEN


def test_p2_all_five_slots_filled_advances_to_p3():
    s = CallState(phase=P2_DISCOVER, **FILLED)
    assert next_phase(s, TurnSignals()).next_phase == P3_VALUE


def test_p2_incomplete_stays():
    s = CallState(phase=P2_DISCOVER, education="12th", phase_turn_count=2)
    assert next_phase(s, TurnSignals()).next_phase == P2_DISCOVER


def test_p2_turn_budget_exceeded_advances_even_if_incomplete():
    s = CallState(phase=P2_DISCOVER, education="12th", phase_turn_count=7)
    assert next_phase(s, TurnSignals()).next_phase == P3_VALUE


def test_p2_turn_budget_boundary_holds_at_the_valve():
    """One turn per slot is enough for a cooperative lead, so the valve sits exactly at
    `len(DISCOVERY_ORDER)` — it must not fire ON that turn, only past it."""
    s = CallState(phase=P2_DISCOVER, education="12th", phase_turn_count=P2_MAX_TURNS)
    assert next_phase(s, TurnSignals()).next_phase == P2_DISCOVER
    s.phase_turn_count = P2_MAX_TURNS + 1
    assert next_phase(s, TurnSignals()).next_phase == P3_VALUE


def test_p3_holds_for_a_second_course_turn_then_advances():
    """P3 was single-turn, which gave the lead exactly ONE turn about the course before the
    booking push — the first of which is spent on the qualifying question. On CAfe5a00b
    they asked "course hai kis liye" and got half a sentence plus "aapne kab aana hai?".

    Two turns: ask, then actually answer. Bounded by P3_MAX_TURNS, and the five-minute
    budget still force-pivots out of P3 at three minutes."""
    first = CallState(phase=P3_VALUE, phase_turn_count=1)
    assert next_phase(first, TurnSignals()).next_phase == P3_VALUE

    second = CallState(phase=P3_VALUE, phase_turn_count=P3_MAX_TURNS)
    assert next_phase(second, TurnSignals()).next_phase == P4_STRUCTURE


def test_an_objection_still_leaves_p3_immediately():
    """The extra turn must not trap a lead who raises an objection — the short-circuit at
    the top of `next_phase` runs before any phase logic."""
    s = CallState(phase=P3_VALUE, phase_turn_count=1)
    assert next_phase(s, TurnSignals(objection="cost")).next_phase == "p6_objection"


def test_p4_advances_to_p5_after_one_turn():
    assert next_phase(CallState(phase=P4_STRUCTURE), TurnSignals()).next_phase == P5_PIVOT


def test_p5_slot_accepted_advances_to_p7():
    s = CallState(phase=P5_PIVOT)
    assert next_phase(s, TurnSignals(slot_accepted=True)).next_phase == P7_CLOSE


def test_p5_no_acceptance_stays():
    s = CallState(phase=P5_PIVOT)
    assert next_phase(s, TurnSignals()).next_phase == P5_PIVOT


def test_p5_objection_routes_to_p6():
    s = CallState(phase=P5_PIVOT, objection_counts={"fees": 1})
    t = next_phase(s, TurnSignals(objection="fees"))
    assert t.next_phase == P6_OBJECTION and not t.hard_pivot


def test_p6_objection_answered_returns_forward_to_p5():
    s = CallState(phase=P6_OBJECTION)
    assert next_phase(s, TurnSignals()).next_phase == P5_PIVOT


def test_p6_never_returns_backward_to_p7():
    s = CallState(phase=P6_OBJECTION, accepted_slot="Mon 6pm")
    assert next_phase(s, TurnSignals()).next_phase == P5_PIVOT


def test_p6_same_objection_twice_hard_pivots_to_p5_no_reanswer():
    s = CallState(phase=P6_OBJECTION, objection_counts={"fees": 2})
    t = next_phase(s, TurnSignals(objection="fees"))
    assert t.next_phase == P5_PIVOT and t.hard_pivot is True


def test_p6_different_objection_is_answered_in_p6():
    s = CallState(phase=P6_OBJECTION, objection_counts={"fees": 1, "no_time": 1})
    t = next_phase(s, TurnSignals(objection="no_time"))
    assert t.next_phase == P6_OBJECTION and not t.hard_pivot


def test_p7_objection_routes_to_p6():
    s = CallState(phase=P7_CLOSE, objection_counts={"no_time": 1})
    assert next_phase(s, TurnSignals(objection="no_time")).next_phase == P6_OBJECTION


def test_p7_readback_confirmed_with_locked_slot_wins():
    s = CallState(phase=P7_CLOSE, locked_slot="2026-07-27T18:00")
    t = next_phase(s, TurnSignals(readback_confirmed=True))
    assert t.next_phase == P7_CLOSE and t.win is True


def test_p7_readback_confirmed_without_locked_slot_does_not_win():
    s = CallState(phase=P7_CLOSE, locked_slot=None)
    assert next_phase(s, TurnSignals(readback_confirmed=True)).win is False


def test_p7_no_confirmation_stays_without_winning():
    s = CallState(phase=P7_CLOSE, locked_slot="2026-07-27T18:00")
    t = next_phase(s, TurnSignals())
    assert t.next_phase == P7_CLOSE and t.win is False


def test_next_phase_does_not_mutate_state():
    s = CallState(phase=P2_DISCOVER, **FILLED)
    before = dataclasses.asdict(s)
    next_phase(s, TurnSignals(inquiry_confirmed=True, objection="fees"))
    assert dataclasses.asdict(s) == before


def test_a_lead_pushing_for_a_time_in_p3_gets_the_offer_instead_of_another_course_question():
    """Call 0f09c8a3, `time_talk_holds=4`. P3 carries no OFFER line, so every attempt the
    lead made to name a time was caught by the time-talk guard and replaced with "course ke
    baare mein aur kya jaanna chahenge?" — four turns running, at a lead who was trying to
    book. The guard is right that ROMA must not raise a time in P3; it has no way to know
    the LEAD did. The machine does."""
    from roma.domain.conversation.machine import P5_PIVOT, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p3_value")
    state.phase_turn_count = 1
    assert next_phase(state, TurnSignals(asks_to_book=True)).next_phase == P5_PIVOT


def test_one_passing_mention_of_a_time_does_not_skip_the_pitch():
    """`asks_to_book` is only set after two consecutive turns; a single mention leaves P3
    alone. Pivoting on one would offer a slot to someone still deciding."""
    from roma.domain.conversation.machine import P3_VALUE, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p3_value")
    state.phase_turn_count = 1
    assert next_phase(state, TurnSignals(asks_to_book=False)).next_phase == P3_VALUE


def test_an_objection_still_outranks_the_booking_pivot():
    """Objection handling is checked first and must stay that way — a lead raising a doubt
    and a time in the same breath needs the doubt answered, not a slot."""
    from roma.domain.conversation.machine import P6_OBJECTION, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p3_value")
    sig = TurnSignals(asks_to_book=True, objection="placement_doubt")
    assert next_phase(state, sig).next_phase == P6_OBJECTION


def test_a_lead_who_asks_to_book_gets_the_offer_from_any_pre_offer_phase():
    """Call ca529641. The lead asked to schedule and Roma answered, three turns running:

        "par is waqt main course ke details share kar rahi hoon visit abhi schedule
         nahi kar rahi. Aapko pehle course ki value clear honi chahiye."
        "visit abhi schedule wahi hota hai jab mujhe prompt ho."

    She was obeying the phase; the phase was wrong. Someone who already knows what they want
    should not have to sit through the pitch to earn a slot. The earlier fix only jumped
    from P3, so a lead asking in P2 or P4 still got walked through it."""
    from roma.domain.conversation.machine import P5_PIVOT, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    for phase in ("p2_discover", "p3_value", "p4_structure", "p6_objection"):
        state = CallState(phase=phase)
        state.phase_turn_count = 1
        got = next_phase(state, TurnSignals(asks_to_book=True)).next_phase
        assert got == P5_PIVOT, f"{phase} refused to pivot (went to {got})"


def test_p1_holds_only_for_a_deferral_not_for_a_booking_ask():
    """SUPERSEDED by call bde258d1. This test used to assert that P1 never pivots, on the
    reasoning that nothing may happen before the lead agrees to talk. The call showed the
    reasoning was wrong: "meeting schedule fix karo" IS that agreement, and refusing to act
    on it sent the lead to P2 for a course pitch they had explicitly declined.

    What P1 must still hold for is a DEFERRAL — see the two tests at the end of this file."""
    from roma.domain.conversation.machine import P1_OPEN, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p1_open")
    sig = TurnSignals(asks_to_book=True, declined_now=True, inquiry_confirmed=False)
    assert next_phase(state, sig).next_phase == P1_OPEN


def test_asking_to_book_in_p1_is_permission_and_pivots():
    """Call bde258d1. The lead asked to book TWICE in P1 ("meeting schedule fix karo").
    P1 was excluded, so the call advanced to P2 instead, the time-talk guard substituted the
    course line there, and the lead answered "आप क्यों मुझे course के बारे में बता रहे हो".
    It took a third ask to get an offer.

    "Meeting fix karo" is a stronger answer to "kya abhi 2 minute baat ho sakti hai?" than
    "haan" is."""
    from roma.domain.conversation.machine import P5_PIVOT, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p1_open")
    sig = TurnSignals(asks_to_book=True, inquiry_confirmed=False)
    assert next_phase(state, sig).next_phase == P5_PIVOT


def test_a_deferral_still_holds_p1_even_with_a_booking_word():
    """Hard rule 6: a busy lead gets one alternative and a warm close, never a slot. This is
    the one thing P1's exclusion was protecting, and it is kept explicitly."""
    from roma.domain.conversation.machine import P1_OPEN, TurnSignals, next_phase
    from roma.domain.conversation.state import CallState

    state = CallState(phase="p1_open")
    sig = TurnSignals(asks_to_book=True, declined_now=True, inquiry_confirmed=False)
    assert next_phase(state, sig).next_phase == P1_OPEN
