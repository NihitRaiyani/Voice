"""Relative→absolute time resolution (docs/03), all in code with an injected `now` so it is
deterministic. Asia/Kolkata; below-threshold / underdetermined → None (re-ask)."""

from datetime import date, datetime

import pytest
from roma.domain.appointments.slots import TimeSlot
from roma.domain.appointments.timeresolve import (
    IST,
    MAX_DAY_OFFSET,
    resolve_time_slot,
    resolve_time_slot_reason,
    resolve_visit_slot,
)

NOW = datetime(2026, 7, 25, 10, 0, tzinfo=IST)


def test_kal_evening_resolves_to_tomorrow_pm():
    slot = TimeSlot(day_offset=1, hour=5, period="evening", confidence=0.9)
    got = resolve_time_slot(slot, NOW)
    assert got == datetime(2026, 7, 26, 17, 0, tzinfo=IST)


def test_aaj_morning_resolves_to_today_am():
    slot = TimeSlot(day_offset=0, hour=11, period="morning", confidence=0.9)
    assert resolve_time_slot(slot, NOW) == datetime(2026, 7, 25, 11, 0, tzinfo=IST)


def test_idiomatic_dhai_baje_saanjhe_is_1430():
    slot = TimeSlot(hour=2, minute=30, period="evening", day_offset=1, confidence=0.85)
    assert resolve_time_slot(slot, NOW) == datetime(2026, 7, 26, 14, 30, tzinfo=IST)


def test_named_weekday_resolves_to_next_occurrence():
    slot = TimeSlot(weekday="monday", hour=11, period="morning", confidence=0.9)
    got = resolve_time_slot(slot, NOW)
    assert got == datetime(2026, 7, 27, 11, 0, tzinfo=IST)


def test_named_weekday_today_resolves_to_today():
    slot = TimeSlot(weekday="saturday", hour=5, period="evening", confidence=0.9)
    got = resolve_time_slot(slot, NOW)
    assert got == datetime(2026, 7, 25, 17, 0, tzinfo=IST)


def test_low_confidence_returns_none():
    slot = TimeSlot(day_offset=1, hour=6, period="evening", confidence=0.4)
    assert resolve_time_slot(slot, NOW) is None


def test_underdetermined_date_returns_none():
    slot = TimeSlot(hour=6, period="evening", confidence=0.95)
    assert resolve_time_slot(slot, NOW) is None


def test_missing_hour_returns_none():
    slot = TimeSlot(day_offset=1, period="evening", confidence=0.95)
    assert resolve_time_slot(slot, NOW) is None


def test_result_is_timezone_aware_kolkata():
    slot = TimeSlot(day_offset=2, hour=4, period="afternoon", confidence=0.9)
    got = resolve_time_slot(slot, NOW)
    assert got.tzinfo is IST and got.hour == 16


AFTERNOON = datetime(2026, 7, 26, 16, 0, tzinfo=IST)


def test_bare_hour_resolves_to_afternoon_not_dawn():
    """`kal teen baje` is 3 PM for a counselling visit. It resolved to 03:00 — and the
    branch is shut at 3 AM, so the "win" was a slot nobody could keep."""
    slot = TimeSlot(day_offset=1, hour=3, period=None, confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) == datetime(2026, 7, 27, 15, 0, tzinfo=IST)


def test_bare_late_morning_hour_stays_am():
    """Only 1-7 flips. `kal das baje` stays 10:00 — not 22:00.

    Was `nau baje`/09:00 until Weltec corrected the window to 10-6 (2026-07-27); nine is
    now before opening and correctly refused, which would have tested the wrong thing."""
    slot = TimeSlot(day_offset=1, hour=10, period=None, confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) == datetime(2026, 7, 27, 10, 0, tzinfo=IST)


def test_nine_in_the_morning_is_now_before_opening():
    """The window is 10-6 (Weltec, 2026-07-27). Nine is shut."""
    slot = TimeSlot(day_offset=1, hour=9, period="morning", confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) is None


def test_twelve_at_night_is_midnight_and_therefore_rejected():
    """`night` is in _PM_PERIODS but the h==12 branch only tested _AM_PERIODS, so
    "12 baje raat" became NOON. It is midnight — and midnight is outside visiting hours,
    so the honest answer is None (re-ask), not a lunchtime booking."""
    slot = TimeSlot(day_offset=1, hour=12, period="night", confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) is None


def test_noon_still_resolves_to_noon():
    slot = TimeSlot(day_offset=1, hour=12, period=None, confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) == datetime(2026, 7, 27, 12, 0, tzinfo=IST)


def test_a_time_already_past_is_rejected():
    """ "aaj 10 baje subah" said at 4 PM booked a visit six hours ago."""
    slot = TimeSlot(day_offset=0, hour=10, period="morning", confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) is None


def test_later_today_still_resolves():
    slot = TimeSlot(day_offset=0, hour=5, period="evening", confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) == datetime(2026, 7, 26, 17, 0, tzinfo=IST)


@pytest.mark.parametrize("offset", [-1, -7, MAX_DAY_OFFSET + 1, 900])
def test_out_of_range_day_offset_is_rejected(offset):
    """A hallucinated offset used to resolve happily — `-1` booked yesterday."""
    slot = TimeSlot(day_offset=offset, hour=11, period=None, confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) is None


@pytest.mark.parametrize("hour,period", [(8, "night"), (7, "morning"), (6, "morning")])
def test_hours_outside_the_visiting_window_are_rejected(hour, period):
    """18:00 is exclusive and 09:00 inclusive, so 8 PM, 7 AM and 6 AM are all out."""
    slot = TimeSlot(day_offset=1, hour=hour, period=period, confidence=0.9)
    assert resolve_time_slot(slot, AFTERNOON) is None


def test_six_pm_is_closing_time_not_a_bookable_slot():
    """Weltec confirmed 9-6 on 2026-07-26. The branch shuts AT six, so a visit cannot
    start then — and `p5_pivot.md`'s example line had to move off "shaam chhe baje"
    with it, or Roma would keep offering a slot her own resolver refuses."""
    slot = TimeSlot(day_offset=1, hour=6, period="evening", confidence=0.9)
    assert resolve_time_slot(slot, NOW) is None


def test_five_pm_is_the_last_slot_that_resolves():
    slot = TimeSlot(day_offset=1, hour=5, period="evening", confidence=0.9)
    assert resolve_time_slot(slot, NOW) == datetime(2026, 7, 26, 17, 0, tzinfo=IST)


def test_a_six_am_request_reports_out_of_hours_not_merely_None():
    """THE regression. Roma cannot refuse a time politely if she is not told it was
    refused, or why."""
    slot = TimeSlot(day_offset=1, hour=6, period="morning", confidence=0.9)
    assert resolve_time_slot_reason(slot, NOW) == ("out_of_hours", None)


def test_a_past_time_is_in_past_not_out_of_hours():
    """Different reasons need different replies: "us waqt branch band hoti hai" is wrong
    and confusing for a slot that was merely five minutes ago."""
    slot = TimeSlot(day_offset=0, hour=10, period="morning", confidence=0.9)
    assert resolve_time_slot_reason(slot, AFTERNOON) == ("in_past", None)


@pytest.mark.parametrize(
    "slot",
    [
        TimeSlot(day_offset=1, hour=11, period=None, confidence=0.1),
        TimeSlot(day_offset=None, weekday=None, hour=11, confidence=0.9),
        TimeSlot(day_offset=1, hour=None, confidence=0.9),
        TimeSlot(day_offset=-1, hour=11, confidence=0.9),
    ],
)
def test_everything_underdetermined_reports_unclear(slot):
    assert resolve_time_slot_reason(slot, NOW) == ("unclear", None)


def test_a_good_slot_reports_ok_with_the_datetime():
    slot = TimeSlot(day_offset=1, hour=11, period="morning", confidence=0.9)
    assert resolve_time_slot_reason(slot, NOW) == (
        "ok",
        datetime(2026, 7, 26, 11, 0, tzinfo=IST),
    )


def test_resolve_time_slot_is_exactly_the_reason_functions_second_element():
    """The plain wrapper must never drift from the reason-carrying version — every
    existing caller reads the wrapper."""
    for slot in [
        TimeSlot(day_offset=1, hour=11, period="morning", confidence=0.9),
        TimeSlot(day_offset=1, hour=6, period="morning", confidence=0.9),
        TimeSlot(day_offset=1, hour=11, confidence=0.1),
    ]:
        assert resolve_time_slot(slot, NOW) == resolve_time_slot_reason(slot, NOW)[1]


def test_naive_now_is_read_as_ist_not_system_local():
    """The module's whole contract is Asia/Kolkata; a naive `now` must not silently be
    interpreted in whatever timezone the server happens to run in."""
    naive = datetime(2026, 7, 26, 16, 0)
    slot = TimeSlot(day_offset=1, hour=3, period=None, confidence=0.9)
    assert resolve_time_slot(slot, naive) == datetime(2026, 7, 27, 15, 0, tzinfo=IST)


TUESDAY_4PM = datetime(2026, 7, 28, 16, 0, tzinfo=IST)


def test_a_weekday_already_past_today_rolls_to_next_week():
    slot = TimeSlot(weekday="tuesday", hour=11, period="morning", confidence=0.9)
    got = resolve_time_slot(slot, TUESDAY_4PM)
    assert got == datetime(2026, 8, 4, 11, 0, tzinfo=IST)


def test_a_weekday_still_ahead_today_stays_today():
    """The roll must not fire when the slot is genuinely later the same day, or every
    same-day booking silently slips a week."""
    slot = TimeSlot(weekday="tuesday", hour=5, period="evening", confidence=0.9)
    assert resolve_time_slot(slot, TUESDAY_4PM) == datetime(2026, 7, 28, 17, 0, tzinfo=IST)


def test_an_explicit_day_offset_never_rolls():
    """ "aaj" means today. A today that has passed is in the past, not next week —
    silently moving it a week would book a visit the lead never agreed to."""
    slot = TimeSlot(day_offset=0, hour=10, period="morning", confidence=0.9)
    assert resolve_time_slot_reason(slot, TUESDAY_4PM) == ("in_past", None)


def test_the_rolled_slot_is_still_held_to_branch_hours():
    """Rolling forward must not become a way around the visiting window."""
    slot = TimeSlot(weekday="tuesday", hour=8, period="night", confidence=0.9)
    assert resolve_time_slot_reason(slot, TUESDAY_4PM)[0] == "out_of_hours"


def test_twelve_in_the_morning_is_noon_not_midnight():
    """Indian usage: `subah` stretches to midday and midnight is always said as `raat`.
    Mapping morning to 00:00 cost a live booking (CA9933275) — the lead asked twice for
    "12 baje subah ke", it resolved to a past midnight, and Roma told them the branch does
    not open at noon while they quoted her own opening hours back at her."""
    slot = TimeSlot(day_offset=1, hour=12, period="morning", confidence=0.9)
    assert resolve_time_slot(slot, NOW) == datetime(2026, 7, 26, 12, 0, tzinfo=IST)


def test_twelve_at_night_is_still_midnight():
    """The one reading that IS 00:00 must survive the fix above."""
    slot = TimeSlot(day_offset=1, hour=12, period="night", confidence=0.9)
    assert resolve_time_slot(slot, NOW) is None


def test_an_hour_with_no_day_resolves_on_the_anchor_day():
    """A phone conversation names the day once and then talks in hours. Without the anchor
    "teen baje" has no day in it at all and the extractor fills the hole from the stale
    offer list — which booked Monday for a lead who had moved to Wednesday."""
    slot = TimeSlot(hour=3, confidence=0.9)
    v = resolve_visit_slot(slot, NOW, anchor_day=date(2026, 7, 29))
    assert v.reason == "ok"
    assert v.slot == datetime(2026, 7, 29, 15, 0, tzinfo=IST)


def test_the_anchor_day_never_smuggles_in_a_past_slot():
    slot = TimeSlot(hour=9, confidence=0.9)
    v = resolve_visit_slot(slot, NOW, anchor_day=NOW.date())
    assert v.reason == "in_past" and v.slot is None


def test_the_anchor_day_never_smuggles_in_an_out_of_hours_slot():
    slot = TimeSlot(hour=8, period="morning", confidence=0.9)
    v = resolve_visit_slot(slot, NOW, anchor_day=date(2026, 7, 29))
    assert v.reason == "out_of_hours" and v.slot is None


def test_without_an_anchor_an_hour_alone_stays_unclear():
    """The anchor is memory, not a guess: with no day ever named there is nothing to
    resolve against and the caller must re-ask."""
    slot = TimeSlot(hour=3, confidence=0.9)
    assert resolve_visit_slot(slot, NOW).reason == "unclear"


def test_a_named_day_still_beats_the_anchor():
    """The anchor only fills a HOLE. A day the lead actually said always wins."""
    slot = TimeSlot(weekday="tuesday", hour=11, confidence=0.9)
    v = resolve_visit_slot(slot, NOW, anchor_day=date(2026, 7, 29))
    assert v.slot == datetime(2026, 7, 28, 11, 0, tzinfo=IST)


def _tue_1530():
    """Tuesday 2026-07-28, 15:30 IST — the moment of the live call."""
    from roma.domain.appointments.timeresolve import IST

    return datetime(2026, 7, 28, 15, 30, tzinfo=IST)


def test_a_named_weekday_beats_a_contradictory_day_offset():
    """Live call CA00417672, the exact verdict:

        reason=in_past day_offset=0 weekday=wednesday anchor=2026-07-29 hour=14

    Both fields cannot be true — the call was on the Tuesday. `_resolve_date` checked
    `day_offset` first and short-circuited, so 2pm resolved to Tuesday 2pm (past) and a
    bookable Wednesday 2pm was refused. `slots.py` tells the extractor to send one or the
    other; gpt-4o-mini sends both, and nothing downstream checked."""
    from roma.domain.appointments.slots import TimeSlot
    from roma.domain.appointments.timeresolve import resolve_visit_slot

    slot = TimeSlot(
        day_offset=0, weekday="wednesday", hour=2, period="afternoon", confidence=0.9
    )
    v = resolve_visit_slot(slot, _tue_1530())
    assert v.reason == "ok", f"still refused: {v.reason}"
    assert v.slot.date() == date(2026, 7, 29)
    assert v.slot.hour == 14


def test_day_offset_alone_is_still_believed():
    """The precedence only applies when BOTH are present. "kal" is a day_offset and nothing
    else, and it must keep resolving exactly as it did."""
    from roma.domain.appointments.slots import TimeSlot
    from roma.domain.appointments.timeresolve import resolve_visit_slot

    v = resolve_visit_slot(TimeSlot(day_offset=1, hour=11, confidence=0.9), _tue_1530())
    assert v.reason == "ok"
    assert v.slot.date() == date(2026, 7, 29)


def test_an_hour_that_resolved_backwards_is_retried_against_the_day_on_the_table():
    """The general shape behind the same bug: the lead names an HOUR while a day is already
    under discussion, the model attaches a day of its own, and the pair resolves into the
    past. The anchor is the day they were actually talking about, and it used to be reachable
    only from the `unclear` branch — so a wrong day that RESOLVED skipped the correction."""
    from roma.domain.appointments.slots import TimeSlot
    from roma.domain.appointments.timeresolve import resolve_visit_slot

    slot = TimeSlot(day_offset=0, hour=11, confidence=0.9)
    v = resolve_visit_slot(slot, _tue_1530(), anchor_day=date(2026, 7, 29))
    assert v.reason == "ok"
    assert v.slot == datetime(2026, 7, 29, 11, 0, tzinfo=_tue_1530().tzinfo)


def test_a_genuinely_past_time_is_still_refused():
    """The recovery must not become a way of booking anything at all. With no day under
    discussion there is nothing to re-read it against."""
    from roma.domain.appointments.slots import TimeSlot
    from roma.domain.appointments.timeresolve import resolve_visit_slot

    v = resolve_visit_slot(TimeSlot(day_offset=0, hour=11, confidence=0.9), _tue_1530())
    assert v.reason == "in_past"
    assert v.slot is None


def test_the_recovery_will_not_book_outside_branch_hours():
    """An anchor cannot smuggle in a time the branch is shut for — the same bound every
    other resolution in this module carries."""
    from roma.domain.appointments.slots import TimeSlot
    from roma.domain.appointments.timeresolve import resolve_visit_slot

    slot = TimeSlot(day_offset=0, hour=7, period="morning", confidence=0.9)
    v = resolve_visit_slot(slot, _tue_1530(), anchor_day=date(2026, 7, 29))
    assert v.reason != "ok"
