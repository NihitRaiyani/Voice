"""What Roma is allowed to offer (roma.controller.calendar).

The controller picks the visit slots; Roma only speaks them. She used to invent two each
turn by copying the example in `p5_pivot.md`, so nothing was ever on record and a lead
answering "મેં Tuesday કુ આઉંગા" had nothing to be resolved against.

The invariant that matters most here is the last one: an offer the resolver would refuse is
worse than no offer at all, because Roma proposes it, the lead agrees, and the booking then
fails silently.
"""

import asyncio
from datetime import date, datetime

from roma.controller.calendar import DEFAULT_CALENDAR, StaticHoursCalendar, VisitCalendar
from roma.controller.timeresolve import (
    IST,
    VISIT_HOUR_END,
    VISIT_HOUR_START,
    TimeSlot,
    resolve_time_slot_reason,
)

CAL = StaticHoursCalendar()
MONDAY_8AM = datetime(2026, 7, 27, 8, 0, tzinfo=IST)
MONDAY_1PM = datetime(2026, 7, 27, 13, 0, tzinfo=IST)
MONDAY_9PM = datetime(2026, 7, 27, 21, 0, tzinfo=IST)


def _offers(now, **kw):
    return asyncio.run(CAL.offers(now, **kw))


def test_the_static_calendar_satisfies_the_protocol():
    """The seam the real Google Calendar drops into. `runtime_checkable` only verifies the
    method names exist, which is exactly the shape check worth having here."""
    assert isinstance(CAL, VisitCalendar)
    assert isinstance(DEFAULT_CALENDAR, VisitCalendar)


def test_it_offers_two_slots_by_default():
    assert len(_offers(MONDAY_8AM)) == 2


def test_every_offer_is_strictly_in_the_future():
    """An offer in the past is refused by the resolver as `in_past`, so Roma would propose
    a time she cannot book."""
    for now in (MONDAY_8AM, MONDAY_1PM, MONDAY_9PM):
        for slot in _offers(now):
            assert slot > now, (now, slot)


def test_no_offer_is_sooner_than_the_lead_could_possibly_get_there():
    """Live call CA3c7d3c7b (2026-07-28). At 10:50:45 Roma offered "Tuesday 28 July,
    11:00 AM" — a real Tuesday, inside branch hours, and NINE MINUTES AWAY. Every existing
    invariant here passed it: strictly future, inside hours, resolver would accept it.

    "Strictly in the future" is the wrong floor for a counselling visit somebody has to
    travel to. A slot the lead cannot physically reach is not an offer, it is a way of
    making them say no."""
    from datetime import timedelta

    from roma.controller.timeresolve import VISIT_MIN_LEAD

    for now in (MONDAY_8AM, MONDAY_1PM, MONDAY_9PM, datetime(2026, 7, 28, 10, 50, tzinfo=IST)):
        for slot in _offers(now):
            assert slot - now >= VISIT_MIN_LEAD, f"offered {slot} at {now}"
    assert VISIT_MIN_LEAD >= timedelta(minutes=60), "an hour is the least that is credible"


def test_a_day_pinned_by_the_lead_also_respects_the_lead_time():
    """The `on=` branch is a separate code path and had the same `> now` floor."""
    from roma.controller.timeresolve import VISIT_MIN_LEAD

    now = datetime(2026, 7, 28, 10, 50, tzinfo=IST)
    for slot in _offers(now, on=now.date()):
        assert slot - now >= VISIT_MIN_LEAD, f"offered {slot} at {now}"


def test_every_offer_is_inside_branch_hours():
    for now in (MONDAY_8AM, MONDAY_1PM, MONDAY_9PM):
        for slot in _offers(now):
            assert VISIT_HOUR_START <= slot.hour < VISIT_HOUR_END, slot


def test_no_offer_is_one_the_resolver_would_refuse():
    """THE invariant, asserted against the real resolver rather than restating its rules.

    Roma once offered "subah chaar baje" — 4 AM — and the resolver refused it. Any drift
    between what the calendar proposes and what `timeresolve` accepts reproduces that
    class of bug, so the two are checked against each other directly."""
    for now in (MONDAY_8AM, MONDAY_1PM, MONDAY_9PM):
        for slot in _offers(now):
            got = resolve_time_slot_reason(
                TimeSlot(
                    day_offset=(slot.date() - now.date()).days,
                    hour=slot.hour,
                    period="morning" if slot.hour < 12 else "evening",
                    confidence=1.0,
                ),
                now,
            )
            assert got[0] == "ok", f"calendar offered {slot}, resolver said {got[0]}"


def test_a_day_that_still_has_room_offers_today():
    assert _offers(MONDAY_8AM)[0].date() == MONDAY_8AM.date()


def test_a_day_that_is_used_up_rolls_to_tomorrow():
    """At 9 PM both of today's hours are gone; offering them anyway is the `in_past`
    failure."""
    for slot in _offers(MONDAY_9PM):
        assert slot.date() > MONDAY_9PM.date()


def test_a_half_spent_day_mixes_today_and_tomorrow():
    """1 PM: the 11 AM is gone, the 5 PM is not."""
    got = _offers(MONDAY_1PM)
    assert got[0] == datetime(2026, 7, 27, 17, 0, tzinfo=IST)
    assert got[1] == datetime(2026, 7, 28, 11, 0, tzinfo=IST)


def test_pinning_to_a_day_returns_only_that_day():
    """The case that started all of this: the lead named a day and no time."""
    got = _offers(MONDAY_8AM, on=date(2026, 7, 28))
    assert got and all(s.date() == date(2026, 7, 28) for s in got)


def test_pinning_to_a_day_still_excludes_hours_already_gone():
    got = _offers(MONDAY_1PM, on=MONDAY_1PM.date())
    assert got == [datetime(2026, 7, 27, 17, 0, tzinfo=IST)]


def test_pinning_to_a_finished_day_returns_nothing_rather_than_lying():
    """An empty list is a real answer — "you said today, and today is done". The caller
    decides whether to roll; the calendar must not quietly substitute another day."""
    assert _offers(MONDAY_9PM, on=MONDAY_9PM.date()) == []


def test_offers_are_deterministic():
    """The replay harness runs on a frozen `now`; a calendar that varied would make every
    fixture flaky."""
    assert _offers(MONDAY_8AM) == _offers(MONDAY_8AM)


def test_a_naive_now_is_read_as_ist():
    """Same rule as `timeresolve`: the module must not depend on the caller remembering."""
    naive = datetime(2026, 7, 27, 8, 0)
    assert _offers(naive) == _offers(MONDAY_8AM)


def test_the_offer_hours_are_one_morning_and_one_evening():
    """Not a style point: one of each is what lets a working lead and a student both say
    yes without Roma having to ask first."""
    assert any(h < 12 for h in CAL.offer_hours)
    assert any(h >= 12 for h in CAL.offer_hours)


def test_is_open_tracks_the_branch_window():
    assert asyncio.run(CAL.is_open(datetime(2026, 7, 27, 11, 0, tzinfo=IST))) is True
    assert asyncio.run(CAL.is_open(datetime(2026, 7, 27, 6, 0, tzinfo=IST))) is False
    assert asyncio.run(CAL.is_open(datetime(2026, 7, 27, 18, 0, tzinfo=IST))) is False
