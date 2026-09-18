"""Google Calendar as a `VisitCalendar` — the counsellor's real diary, read-only.

## What this can and cannot do, precisely

The credential Weltec supplied is a **browser API key**. A Google API key authenticates
the *project*, not a *user*, so it can read a calendar that has been shared publicly and
it can do nothing else. It cannot create, move or delete an event, and no amount of
request shaping changes that — writing to someone's diary requires OAuth or a service
account with the calendar shared to it.

So this adapter does the half that is actually available, and it is the half that fixes
the visible bug: **it stops Roma offering a time the counsellor is already booked for.**
`StaticHoursCalendar` says every open hour is free, which is a fiction the moment two
leads pick 11 AM. This reads the diary and removes what is taken.

Writing the confirmed visit back is `postcall` work, not turn work (docs/02: "async queue
→ CRM/calendar, never inside the turn"), and it needs the service-account credential. When
that arrives it slots in beside this class; nothing else moves.

## The rule this module obeys

**A calendar failure must never cost the call.** Network I/O now sits inside the turn, so
every path here — timeout, HTTP error, malformed JSON, missing config — degrades to the
wrapped calendar's answer and logs. Roma offering a possibly-double-booked slot is a bad
day for one counsellor; Roma silently failing to offer any slot is a lost booking and a
lead who thinks the line went dead.

The budget is deliberately tight (`_TIMEOUT_SECS`) and results are cached for the length
of a turn or two (`_CACHE_TTL_SECS`), because this runs on the path the lead is waiting on.

## Configuration

Both `GOOGLE_CALENDAR_ID` and `GOOGLE_CALENDAR_API_KEY` must be set or the adapter is not
built at all and Roma runs on `StaticHoursCalendar` exactly as before. The key is a secret:
env-only, never in code, never logged (docs/07, and the redacting logger covers the rest).
The calendar must be shared as "Make available to public" for a key to read it — if it is
not, every request 404s, this degrades to static hours, and the log says so once.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from roma.domain.appointments.calendar import DEFAULT_CALENDAR, VisitCalendar
from roma.domain.appointments.timeresolve import IST

_log = logging.getLogger("roma.domain.conversation")

_API_BASE = "https://www.googleapis.com/calendar/v3/calendars"

_TIMEOUT_SECS = 1.5

_CACHE_TTL_SECS = 30.0

_VISIT_MINUTES = 60


@dataclass
class GoogleCalendarVisits:
    """`VisitCalendar` that filters a base calendar's offers by the real diary.

    Composition, not replacement: `base` still decides which hours are offerable at all
    (`StaticHoursCalendar` — 11 AM and 5 PM, inside branch hours). This only ever *removes*
    slots. That ordering matters — it means a broken or empty diary can never invent an
    offer outside branch hours, which is the failure mode a calendar-driven offer generator
    would otherwise have.
    """

    calendar_id: str
    api_key: str
    base: VisitCalendar = DEFAULT_CALENDAR
    session_factory: object = None

    _cache: dict = field(default_factory=dict, repr=False)

    async def offers(
        self, now: datetime, *, count: int = 2, on: "date | None" = None
    ) -> "list[datetime]":
        """The base calendar's offers, minus anything the diary says is taken.

        Asks the base for extra candidates so that removing a busy slot still leaves two to
        offer — otherwise a single booked 11 AM would leave Roma with one option, and "kaunsa
        theek rahega?" needs two. Falls back to the unfiltered offers whenever the diary
        cannot be read.
        """
        candidates = await self.base.offers(now, count=count * 3, on=on)
        if not candidates:
            return candidates

        busy = await self._busy(candidates[0], candidates[-1])
        if busy is None:
            return candidates[:count]

        free = [c for c in candidates if not _clashes(c, busy)]
        dropped = len(candidates[:count]) - len(free[:count])
        if dropped > 0:
            _log.info("calendar: %d offered slot(s) dropped as busy", dropped)
        if not free:
            _log.warning("calendar: every candidate slot is busy; offering unfiltered")
            return candidates[:count]
        return free[:count]

    async def is_open(self, when: datetime) -> bool:
        """Branch hours AND a free diary. Branch hours first — they are the cheap, certain
        half, and a closed branch needs no network call to rule out."""
        if not await self.base.is_open(when):
            return False
        busy = await self._busy(when, when)
        return True if busy is None else not _clashes(when, busy)

    async def _busy(self, first: datetime, last: datetime) -> "list[tuple] | None":
        """Busy intervals covering [first, last], or None if the diary could not be read.

        None and `[]` mean different things and the callers depend on it: `[]` is "the diary
        says nothing is booked", None is "we do not know". Only the second falls back.
        """
        time_min = first.astimezone(IST) - timedelta(minutes=_VISIT_MINUTES)
        time_max = last.astimezone(IST) + timedelta(minutes=_VISIT_MINUTES)
        key = (time_min.isoformat(), time_max.isoformat())

        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_SECS:
            return hit[1]

        busy = await self._fetch(time_min, time_max)
        if busy is not None:
            self._cache[key] = (time.monotonic(), busy)
        return busy

    async def _fetch(self, time_min: datetime, time_max: datetime) -> "list[tuple] | None":
        """One events.list call. Returns None on ANY failure — see the module docstring."""
        query = urlencode(
            {
                "key": self.api_key,
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "50",
                "fields": "items(start,end,status)",
            }
        )
        from urllib.parse import quote

        url = f"{_API_BASE}/{quote(self.calendar_id, safe='')}/events?{query}"

        try:
            body = await self._get(url)
        except Exception:  # noqa: BLE001 — a diary lookup must never take down a turn
            _log.warning("calendar: diary unreadable; falling back to branch hours")
            return None

        try:
            items = json.loads(body).get("items", [])
        except (ValueError, AttributeError):
            _log.warning("calendar: diary response was not usable JSON; falling back")
            return None

        out = []
        for item in items:
            if not isinstance(item, dict) or item.get("status") == "cancelled":
                continue
            start = _parse_edge(item.get("start"))
            end = _parse_edge(item.get("end"))
            if start is not None and end is not None:
                out.append((start, end))
        return out

    async def _get(self, url: str) -> str:
        """Fetch `url` with a hard timeout. Split out so tests inject a session and no test
        ever touches the network."""
        if self.session_factory is not None:
            return await self.session_factory(url)

        import aiohttp

        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECS)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


def _parse_edge(edge) -> "datetime | None":
    """One end of a Google event, as an IST datetime.

    Google gives `dateTime` for a timed event and `date` for an all-day one. An all-day
    block ("counsellor on leave") must count as busy for the whole day, so a bare date
    becomes midnight IST — which, with the event's own end date being exclusive, covers it.
    """
    if not isinstance(edge, dict):
        return None
    raw = edge.get("dateTime") or edge.get("date")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _clashes(slot: datetime, busy) -> bool:
    """Does a visit starting at `slot` overlap any busy interval? Half-open on both sides,
    so an event ending exactly at 11:00 leaves 11:00 offerable."""
    slot_end = slot + timedelta(minutes=_VISIT_MINUTES)
    return any(start < slot_end and slot < end for start, end in busy)


def build_calendar(settings, base: VisitCalendar = DEFAULT_CALENDAR) -> VisitCalendar:
    """The calendar Roma books against: the real diary when configured, else branch hours.

    Both settings must be present. A half-configured calendar is a configuration mistake,
    and silently running on static hours while an operator believes the diary is live is
    worse than running on static hours knowingly — hence the warning.
    """
    cal_id = (getattr(settings, "google_calendar_id", "") or "").strip()
    key = getattr(settings, "google_calendar_api_key", None)
    key_value = key.get_secret_value().strip() if key is not None else ""

    if not cal_id and not key_value:
        return base
    if not cal_id or not key_value:
        _log.warning(
            "calendar: GOOGLE_CALENDAR_ID and GOOGLE_CALENDAR_API_KEY must BOTH be set; "
            "running on branch hours only"
        )
        return base

    _log.info("calendar: booking against the Google diary (read-only)")
    return GoogleCalendarVisits(calendar_id=cal_id, api_key=key_value, base=base)


__all__ = ["GoogleCalendarVisits", "build_calendar"]
