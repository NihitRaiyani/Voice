"""Relative → absolute visit time, resolved IN CODE (docs/03).

docs/03: "Resolve relative→absolute in code, Asia/Kolkata, with today's date injected.
Never let the model do date arithmetic." `slots.extract_time_slot` gives us the language
the lead used (a relative day + a wall-clock time); this module turns it into a concrete
timezone-aware datetime. Below the confidence threshold, or when the day/time is
underdetermined, it returns None so the caller re-asks rather than guessing.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

from roma.controller.slots import TimeSlot

_log = logging.getLogger("roma.controller")

IST = ZoneInfo("Asia/Kolkata")
CONFIDENCE_THRESHOLD = 0.7

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_PM_PERIODS = {"afternoon", "evening", "night"}

VISIT_HOUR_START = 10
VISIT_HOUR_END = 18

VISIT_MIN_LEAD = timedelta(minutes=90)

MAX_DAY_OFFSET = 14

_BARE_HOUR_PM_MAX = 7


def _weekday_date(slot: TimeSlot, today):
    """The next today-or-later occurrence of `slot.weekday`, or None."""
    if not slot.weekday:
        return None
    target = _WEEKDAYS.get(slot.weekday.strip().casefold())
    if target is None:
        return None
    return today + timedelta(days=(target - today.weekday()) % 7)


def _resolve_date(slot: TimeSlot, today):
    """The calendar date, from a named weekday or a relative day_offset. None if neither.

    ## Why the weekday wins when both are present

    `slots.py` tells the extractor to send one or the other — "null if a weekday was named",
    and "NEVER GUESS A DAY THE LEAD DID NOT SAY". gpt-4o-mini sends both anyway, and this
    used to check `day_offset` first and short-circuit, with no test that the two agreed.

    Live call CA00417672. The lead asked for two o'clock on the Wednesday under discussion
    and the verdict came back:

        slot verdict: reason=in_past accepted=True day_offset=0 weekday=wednesday \
            anchor=2026-07-29 hour=14 resolved=- status=in_past

    `day_offset=0` and `weekday=wednesday` cannot both be true — the call was on the Tuesday.
    `day_offset` silently won, 2pm resolved to Tuesday 2pm, that was in the past, and a
    perfectly bookable Wednesday 2pm was refused. Roma then told the lead it was booked
    anyway, which is a separate bug in `confirmguard`, but the slot should never have been
    refused in the first place.

    A named weekday is a thing the lead SAID. `day_offset=0` is what a model emits when it is
    filling a field it has no answer for, and `0` in particular is the cheapest possible
    guess. So when they disagree, the weekday is believed and the disagreement is logged —
    `turn.py` reports it, because a silent correction is how this class of bug hides.
    """
    wd = _weekday_date(slot, today)
    if wd is not None and slot.day_offset is not None:
        if wd != today + timedelta(days=slot.day_offset):
            _log.info(
                "slot fields disagree: weekday=%s day_offset=%s — believing the weekday",
                slot.weekday,
                slot.day_offset,
            )
        return wd
    if slot.day_offset is not None:
        return today + timedelta(days=slot.day_offset)
    return wd


def _resolve_hour(slot: TimeSlot) -> "int | None":
    """The 24h hour, applying a period cue to a 12h clock reading. None if unusable."""
    h = slot.hour
    if h is None:
        return None
    period = (slot.period or "").strip().casefold()
    if 1 <= h <= 11 and period in _PM_PERIODS:
        h += 12
    elif h == 12:
        if period == "night":
            h = 0
    elif not period and 1 <= h <= _BARE_HOUR_PM_MAX:
        h += 12
    if 0 <= h <= 23:
        return h
    return None


def resolve_time_slot_reason(slot: TimeSlot, now: datetime) -> "tuple[str, datetime | None]":
    """Resolve the slot AND say why it failed: `(reason, resolved_or_None)`.

    `resolve_time_slot` throws the reason away, and a live call showed that costs a lead.
    On 2026-07-26 (CAfa2a011) the lead asked for "kal subah chhe baje"; 06:00 is outside
    visiting hours so this module correctly returned None and `accepted_slot` stayed unset —
    but nothing told the LLM, which read the lead's words straight out of the context and
    said "Aapki visit confirm ho gayi kal subah chhe baje" three times before signing off.
    The teardown line said `won=False`. The lead hung up believing in an appointment that
    exists nowhere.

    So the rejection needs a *reason*, not just a None: "outside branch hours" needs Roma to
    say the branch is shut then and offer alternatives, while "I could not parse a time"
    needs her to simply re-ask. `roma.controller.turn` puts the reason into call state and
    `state.as_prompt_vars()` renders it into the phase prompt.

    Reasons: `ok` | `out_of_hours` | `in_past` | `unclear`.

    `now` is injected (never read from the clock here) so the resolution is deterministic
    and testable.
    """
    now_ist = now.astimezone(IST) if now.tzinfo is not None else now.replace(tzinfo=IST)

    if slot.confidence < CONFIDENCE_THRESHOLD:
        return ("unclear", None)
    if slot.day_offset is not None and not 0 <= slot.day_offset <= MAX_DAY_OFFSET:
        return ("unclear", None)
    date = _resolve_date(slot, now_ist.date())
    if date is None:
        return ("unclear", None)
    hour = _resolve_hour(slot)
    if hour is None:
        return ("unclear", None)
    resolved = datetime(date.year, date.month, date.day, hour, slot.minute, tzinfo=IST)

    if resolved <= now_ist and slot.day_offset is None and slot.weekday:
        resolved += timedelta(days=7)

    if resolved <= now_ist:
        return ("in_past", None)
    if not VISIT_HOUR_START <= resolved.hour < VISIT_HOUR_END:
        return ("out_of_hours", None)
    return ("ok", resolved)


def resolve_time_slot(slot: TimeSlot, now: datetime) -> "datetime | None":
    """Absolute Asia/Kolkata datetime for the visit, or None to re-ask.

    The reason-free view of `resolve_time_slot_reason`, kept because every caller that only
    needs the datetime reads better without unpacking a tuple.
    """
    return resolve_time_slot_reason(slot, now)[1]


@dataclass(frozen=True)
class SlotVerdict:
    """What the machine decided about the lead's proposed time.

    Wider than the `(reason, datetime)` tuple in one specific way, and it is the way that
    cost a booking: `day` survives even when the hour did not resolve. A lead who says
    "Tuesday" and nothing else has told us something real, and the old code threw it away —
    `_resolve_date` found the Tuesday, `_resolve_hour` returned None, and the whole verdict
    collapsed to "unclear", which tells Roma to re-ask from scratch a day the lead already
    named.
    """

    reason: str
    slot: "datetime | None" = None
    day: "date | None" = None


def resolve_visit_slot(
    slot: TimeSlot,
    now: datetime,
    *,
    offered: "Sequence[datetime] | None" = None,
    anchor_day: "date | None" = None,
) -> SlotVerdict:
    """The full verdict, with the offered slots as context.

    Resolution order, most-certain first:

    1. `chose_offer` — the lead picked one of the slots Roma actually proposed. No date
       arithmetic at all: the answer is a datetime the calendar already vouched for. This is
       the path that makes "Tuesday", "pehla wala" and "wahi" bookable.
    2. Ordinary resolution of day + hour, exactly as before.
    3. An hour with NO day, on `anchor_day` — the day already under discussion. See below.
    4. A day with no hour. If exactly one offered slot falls on that day, the lead has in
       effect chosen it — "Tuesday" when only one Tuesday was offered is not ambiguous.
       Otherwise `day_only`, carrying the date so Roma can offer times ON that day rather
       than starting over.

    `anchor_day` is the conversation's memory of which day is being discussed
    (`CallState.pending_day`). A phone conversation names the day once and then talks only
    in hours: "Wednesday" … "nahi, main teen baje aaunga". Without an anchor that second
    utterance has no day in it at all, and on live call CA9933275 (2026-07-27) the extractor
    filled the hole by copying the stale Monday out of the offer list — so "3 baje" on a
    Wednesday booked Monday 15:00, and Roma then told the lead the branch was closed then.
    """
    now_ist = now.astimezone(IST) if now.tzinfo is not None else now.replace(tzinfo=IST)

    if slot.chose_offer is not None and offered:
        idx = slot.chose_offer - 1
        if 0 <= idx < len(offered):
            chosen = offered[idx]
            if chosen > now_ist:
                return SlotVerdict("ok", chosen, chosen.date())
            return SlotVerdict("in_past", None, chosen.date())

    reason, resolved = resolve_time_slot_reason(slot, now)
    if resolved is not None:
        return SlotVerdict("ok", resolved, resolved.date())

    if reason == "in_past" and anchor_day is not None:
        hour_ = _resolve_hour(slot)
        if hour_ is not None:
            retry = datetime(
                anchor_day.year,
                anchor_day.month,
                anchor_day.day,
                hour_,
                slot.minute,
                tzinfo=IST,
            )
            if retry > now_ist and VISIT_HOUR_START <= retry.hour < VISIT_HOUR_END:
                _log.info(
                    "slot recovered against the day under discussion: %s -> %s",
                    reason,
                    retry.isoformat(),
                )
                return SlotVerdict("ok", retry, retry.date())

    if reason == "unclear" and slot.confidence >= CONFIDENCE_THRESHOLD:
        date_ = _resolve_date(slot, now_ist.date())
        hour_ = _resolve_hour(slot)

        if date_ is None and hour_ is not None and anchor_day is not None:
            resolved = datetime(
                anchor_day.year,
                anchor_day.month,
                anchor_day.day,
                hour_,
                slot.minute,
                tzinfo=IST,
            )
            if resolved <= now_ist:
                return SlotVerdict("in_past", None, anchor_day)
            if not VISIT_HOUR_START <= resolved.hour < VISIT_HOUR_END:
                return SlotVerdict("out_of_hours", None, anchor_day)
            return SlotVerdict("ok", resolved, anchor_day)

        if date_ is not None and hour_ is None:
            same_day = [d for d in (offered or []) if d.date() == date_ and d > now_ist]
            if len(same_day) == 1:
                return SlotVerdict("ok", same_day[0], date_)
            return SlotVerdict("day_only", None, date_)

    return SlotVerdict(reason, None, None)


__all__ = [
    "resolve_time_slot",
    "resolve_time_slot_reason",
    "resolve_visit_slot",
    "SlotVerdict",
    "IST",
    "CONFIDENCE_THRESHOLD",
    "VISIT_HOUR_START",
    "VISIT_HOUR_END",
    "VISIT_MIN_LEAD",
    "MAX_DAY_OFFSET",
]
