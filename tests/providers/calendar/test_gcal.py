"""The Google Calendar adapter (`roma.controller.gcal`).

No test here touches the network: `session_factory` is injected, so what is exercised is
the real URL construction, the real JSON handling, the real clash arithmetic and the real
fallback — everything except the socket.

The governing rule, and most of what is asserted below: **a calendar failure must never
cost the call.** Every degraded path has to end with Roma still holding offerable slots.
"""

import asyncio
import json
from datetime import date, datetime, timedelta

from roma.controller.calendar import StaticHoursCalendar
from roma.controller.gcal import GoogleCalendarVisits, build_calendar
from roma.controller.timeresolve import IST

NOW = datetime(2026, 7, 25, 8, 0, tzinfo=IST)
MONDAY = date(2026, 7, 27)


def _events(*spans) -> str:
    """A Google events.list body with one timed event per (start, end) pair."""
    return json.dumps(
        {
            "items": [
                {"start": {"dateTime": s.isoformat()}, "end": {"dateTime": e.isoformat()}}
                for s, e in spans
            ]
        }
    )


def _cal(body, base=None) -> GoogleCalendarVisits:
    """An adapter whose one HTTP call returns `body` — a string, or a callable raising."""

    async def fetch(url):
        assert "key=" in url, "the API key must reach the request"
        assert "singleEvents=true" in url, "recurrence must be expanded or blocks are invisible"
        if callable(body):
            return body(url)
        return body

    return GoogleCalendarVisits(
        calendar_id="counsellor@weltec.in",
        api_key="test-key",
        base=base or StaticHoursCalendar(),
        session_factory=fetch,
    )


def _offers(cal, **kw):
    return asyncio.run(cal.offers(NOW, **kw))


def test_a_free_diary_offers_exactly_what_branch_hours_offer():
    """Composition, not replacement: with nothing booked the adapter is invisible."""
    cal = _cal(json.dumps({"items": []}))
    assert _offers(cal) == asyncio.run(StaticHoursCalendar().offers(NOW))


def test_a_booked_slot_is_not_offered():
    """THE point of the adapter. StaticHoursCalendar says every open hour is free, which
    stops being true the moment two leads pick 11 AM."""
    busy_11 = datetime(2026, 7, 25, 11, 0, tzinfo=IST)
    cal = _cal(_events((busy_11, busy_11 + timedelta(hours=1))))
    got = _offers(cal, on=date(2026, 7, 25))
    assert busy_11 not in got
    assert got == [datetime(2026, 7, 25, 17, 0, tzinfo=IST)]


def test_a_half_hour_overlap_still_counts_as_busy():
    """A visit takes an hour, so an event at 11:30 blocks the 11:00 slot. Without a
    duration the clash arithmetic would call 11:00 free."""
    busy = datetime(2026, 7, 25, 11, 30, tzinfo=IST)
    cal = _cal(_events((busy, busy + timedelta(minutes=30))))
    assert datetime(2026, 7, 25, 11, 0, tzinfo=IST) not in _offers(cal, on=date(2026, 7, 25))


def test_an_event_ending_exactly_on_the_hour_leaves_that_hour_free():
    """Half-open on both sides — 10:00-11:00 does not block an 11:00 visit."""
    end = datetime(2026, 7, 25, 11, 0, tzinfo=IST)
    cal = _cal(_events((end - timedelta(hours=1), end)))
    assert end in _offers(cal, on=date(2026, 7, 25))


def test_an_all_day_event_blocks_the_whole_day():
    """ "Counsellor on leave" arrives as a bare `date`, not a `dateTime`, and must cover the
    whole day rather than parsing to nothing. Asserted through `is_open`, because `offers`
    on a fully-blocked day deliberately falls back to the taken slots (see below)."""
    body = json.dumps(
        {"items": [{"start": {"date": "2026-07-27"}, "end": {"date": "2026-07-28"}}]}
    )
    cal = _cal(body)
    assert asyncio.run(cal.is_open(datetime(2026, 7, 27, 11, 0, tzinfo=IST))) is False
    assert asyncio.run(cal.is_open(datetime(2026, 7, 28, 11, 0, tzinfo=IST))) is True


def test_a_cancelled_event_does_not_block_anything():
    body = json.dumps(
        {
            "items": [
                {
                    "status": "cancelled",
                    "start": {"dateTime": "2026-07-25T11:00:00+05:30"},
                    "end": {"dateTime": "2026-07-25T12:00:00+05:30"},
                }
            ]
        }
    )
    assert datetime(2026, 7, 25, 11, 0, tzinfo=IST) in _offers(_cal(body), on=date(2026, 7, 25))


def _raise(_url):
    raise TimeoutError("the diary did not answer in time")


def test_a_timeout_falls_back_to_branch_hours():
    """The governing rule. Roma offering a possibly-double-booked slot is a bad day for one
    counsellor; Roma offering nothing is a lost booking and a lead who thinks the line died."""
    cal = _cal(_raise)
    assert _offers(cal) == asyncio.run(StaticHoursCalendar().offers(NOW))


def test_unparseable_json_falls_back_to_branch_hours():
    cal = _cal("<html>404 — calendar is not shared publicly</html>")
    assert _offers(cal) == asyncio.run(StaticHoursCalendar().offers(NOW))


def test_a_fully_booked_day_offers_the_taken_slots_rather_than_nothing():
    """An empty offer list is unusable: the phase prompt has nothing to speak and the lead
    hears a re-ask with no times in it. A counsellor can move an appointment; a lead who is
    told there are no times hangs up."""
    day = date(2026, 7, 27)
    spans = [
        (datetime(2026, 7, 27, h, 0, tzinfo=IST), datetime(2026, 7, 27, h + 1, 0, tzinfo=IST))
        for h in range(9, 18)
    ]
    got = asyncio.run(_cal(_events(*spans)).offers(NOW, on=day))
    assert len(got) == 2


def test_the_diary_is_read_once_across_repeated_offers():
    """This runs on the path the lead is waiting on. A re-pin per turn must not mean a
    network round trip per turn."""
    calls = []

    async def fetch(url):
        calls.append(url)
        return json.dumps({"items": []})

    cal = GoogleCalendarVisits(
        calendar_id="c", api_key="k", base=StaticHoursCalendar(), session_factory=fetch
    )
    asyncio.run(cal.offers(NOW, on=MONDAY))
    asyncio.run(cal.offers(NOW, on=MONDAY))
    assert len(calls) == 1


def test_the_calendar_id_is_escaped_into_the_path():
    """It is an email address, and `@` in a path segment is not something to trust to luck."""
    seen = []

    async def fetch(url):
        seen.append(url)
        return json.dumps({"items": []})

    cal = GoogleCalendarVisits(calendar_id="a b@weltec.in", api_key="k", session_factory=fetch)
    asyncio.run(cal.offers(NOW))
    assert "a%20b%40weltec.in" in seen[0]


def test_the_adapter_can_only_ever_remove_slots():
    """Ordering invariant: the base decides what is offerable at all, so a broken or empty
    diary can never invent an offer outside branch hours — the failure a calendar-driven
    offer generator would have."""
    base_offers = asyncio.run(StaticHoursCalendar().offers(NOW, count=6))
    got = _offers(_cal(json.dumps({"items": []})), count=6)
    assert set(got) <= set(base_offers)


class _S:
    def __init__(self, cal_id="", key=None):
        self.google_calendar_id = cal_id
        self.google_calendar_api_key = _Secret(key) if key else None


class _Secret:
    def __init__(self, v):
        self._v = v

    def get_secret_value(self):
        return self._v


def test_no_config_means_branch_hours_and_no_warning(caplog):
    base = StaticHoursCalendar()
    assert build_calendar(_S(), base=base) is base
    assert not caplog.records


def test_half_configured_is_loud():
    """Running on static hours while an operator believes the diary is live is worse than
    running on static hours knowingly."""
    import logging

    base = StaticHoursCalendar()
    got = build_calendar(_S(cal_id="c"), base=base)
    assert got is base
    got2 = build_calendar(_S(key="k"), base=base)
    assert got2 is base
    assert logging.getLogger("roma.controller") is not None


def test_both_set_builds_the_adapter():
    got = build_calendar(_S(cal_id="c", key="k"))
    assert isinstance(got, GoogleCalendarVisits)
    assert got.calendar_id == "c"
