"""What Roma is allowed to offer (docs/03 P5).

## Why this exists

Roma's two visit slots used to be invented by the LLM, fresh each turn, by copying the
literal example in `p5_pivot.md`. Nothing recorded them. So when a lead answered "મેં
Tuesday કુ આઉંગા" — "I'll come Tuesday" — there was nothing to resolve "Tuesday" against:
no hour was named, no offer was on record, and the acceptance evaporated. `won=False`, no
log, no checkpoint.

Ownership is therefore inverted: **the controller picks the slots and Roma only speaks
them.** Nothing needs capturing back out of her output, because nothing is invented. It
costs no extra model call.

## The seam

`VisitCalendar` is the interface; `StaticHoursCalendar` is today's rule written down
instead of implied. The real Weltec calendar API replaces the implementation and nothing
else — `advance_turn` takes the calendar as an injected dependency exactly like the
extractors and the classifier.

`offers()` is async even though the static implementation never awaits anything. That is
the one decision here that is expensive to change later: the real API is network I/O,
`advance_turn` is already a coroutine, and retrofitting async through the call chain after
the fact is a far larger diff than an `await` that currently returns immediately.

## What this deliberately does NOT do

No availability, no double-booking check, no capacity. Two concurrent calls can both be
offered — and both lock — the same 11 AM. That is a real limitation of the static rule and
it is the first thing the real calendar fixes; it is not worth a fake implementation here,
because a made-up availability model would have to be unwritten rather than replaced.

The visiting-hours numbers live in `timeresolve` and are imported, not restated. Two
sources for "when is the branch open" is how a resolver starts refusing the slots its own
offer generator produced.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

from roma.domain.appointments.timeresolve import (
    IST,
    VISIT_HOUR_END,
    VISIT_HOUR_START,
    VISIT_MIN_LEAD,
)


@runtime_checkable
class VisitCalendar(Protocol):
    """The branch's bookable time, as far as Roma is concerned."""

    async def offers(
        self, now: datetime, *, count: int = 2, on: "date | None" = None
    ) -> "list[datetime]":
        """`count` slots to offer the lead, all strictly after `now`.

        `on` pins them to one date — for the lead who named a day but no time, which is the
        case that started all of this. Returns fewer than `count` (possibly none) when the
        day has no room left; the caller must handle a short list rather than assume two.
        """
        ...

    async def is_open(self, when: datetime) -> bool:
        """Whether the branch is open at `when`."""
        ...


@dataclass(frozen=True)
class StaticHoursCalendar:
    """Every open hour on every day is bookable — today's actual rule, made explicit.

    `offer_hours` are the two Roma proposes: 11 AM and 5 PM. One morning and one evening,
    which covers a working lead and a student without asking, and both sit inside
    [VISIT_HOUR_START, VISIT_HOUR_END) so the resolver can never refuse an offer Roma just
    made. A test pins that invariant, because breaking it would have Roma proposing times
    she cannot book — the exact failure that produced the 4 AM offer.
    """

    offer_hours: tuple = (11, 17)

    async def offers(
        self, now: datetime, *, count: int = 2, on: "date | None" = None
    ) -> "list[datetime]":
        now_ist = now.astimezone(IST) if now.tzinfo is not None else now.replace(tzinfo=IST)
        earliest = now_ist + VISIT_MIN_LEAD

        if on is not None:
            return [
                slot
                for h in self.offer_hours
                if (slot := datetime(on.year, on.month, on.day, h, tzinfo=IST)) >= earliest
            ][:count]

        out: list[datetime] = []
        day = now_ist.date()
        for _ in range(count + 1):
            for h in self.offer_hours:
                slot = datetime(day.year, day.month, day.day, h, tzinfo=IST)
                if slot >= earliest:
                    out.append(slot)
                if len(out) == count:
                    return out
            day += timedelta(days=1)
        return out

    async def is_open(self, when: datetime) -> bool:
        when_ist = when.astimezone(IST) if when.tzinfo is not None else when.replace(tzinfo=IST)
        return VISIT_HOUR_START <= when_ist.hour < VISIT_HOUR_END


DEFAULT_CALENDAR = StaticHoursCalendar()


__all__ = ["VisitCalendar", "StaticHoursCalendar", "DEFAULT_CALENDAR"]
