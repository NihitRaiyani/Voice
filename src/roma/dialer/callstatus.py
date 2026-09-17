"""Where a dialled call got to, so the web UI can say something true (docs/13).

## Why this exists at all

`request_uuid` is Vobiz's handle for a fired call, and until this module it lived for three
lines in `trigger.py` — read off the response, logged, returned — and correlated with
nothing. `/answer` receives only `?lead=<token>`; `/ws` knows `stream_sid` and `call_sid`.
Nothing joined them, and `_registered_call`'s `finally:` pops the call from `app.state.calls`
the moment it ends, so a FINISHED call left no trace in the process at all.

So the chain the UI needs — `request_uuid -> lead_token -> live call` — is built here, in
Redis rather than in memory, for a reason that is not merely tidiness: the dialer and the
media server are **different processes**. `place_test_call.py` runs `trigger_outbound_call`
under its own `asyncio.run`. An in-process dict would be written by one and read by neither.
`media.py:1073-1078` already argues against a second in-process call map.

## The state a phone call actually has

    dialing -> connected -> ended
            \\-> no_answer

`no_answer` is DERIVED, never written, and that is the important design point. Vobiz is sent
no status callback (`dialer.create_call` posts four fields: from, to, answer_url,
answer_method), so when a callee simply does not pick up, **nothing in this system ever hears
about it** — Vobiz never fetches `/answer` and no socket opens. This session produced two such
calls by accident, and a UI that waited for a writer would sit on "dialing" for ever.

Age is the only honest signal available, so age is what is used.

## What is stored and what is returned are not the same

The callee's number is written (an operator needs to know which call a row is) and is NEVER
returned by `get()`. It is the lead's PII (docs/07), and the browser has no need of a number
the operator just typed. The lead token is likewise an authority and never leaves this module.
"""

import json
import logging
import time

_log = logging.getLogger("roma.dialer")

DIALING = "dialing"
CONNECTED = "connected"
ENDED = "ended"
NO_ANSWER = "no_answer"

# How long a record may sit at `dialing` before it reads as nobody having picked up. Vobiz
# rings for roughly 30-45s before giving up; 60 clears that without leaving the UI guessing.
NO_ANSWER_AFTER_SECS = 60


def status_key(request_uuid: str) -> str:
    return f"roma:callstatus:{request_uuid}"


def token_index_key(lead_token: str) -> str:
    """The reverse link. `/answer` and the teardown know the TOKEN and not the uuid, because
    the token is the only identifier we put in the answer URL ourselves."""
    return f"roma:callindex:{lead_token}"


class CallStatusStore:
    """`request_uuid -> {status, reason, at}`, TTL-bounded. Mirrors `RedisLeadStore`.

    Every method is fail-open: a status write must never affect a call in progress. The UI
    showing a stale state is a cosmetic problem; an exception on the media path is not.
    """

    def __init__(self, redis_url: "str | None" = None, *, client=None, ttl: int = 3600) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("CallStatusStore needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._client = client
        self._ttl = ttl

    async def put_dialing(self, request_uuid: str, lead_token: str, to: str) -> None:
        """Record a fired call and the token that will identify it when it connects."""
        record = {"status": DIALING, "reason": "", "at": time.time(), "to": to}
        await self._client.set(status_key(request_uuid), json.dumps(record), ex=self._ttl)
        await self._client.set(token_index_key(lead_token), request_uuid, ex=self._ttl)

    async def _touch(self, lead_token: str, status: str, reason: str) -> None:
        uuid = await self._client.get(token_index_key(lead_token))
        if not uuid:
            # An inbound call, or one placed by the CLI before this store existed. Not an
            # error: most calls have no status row and never needed one.
            return
        raw = await self._client.get(status_key(uuid))
        record = json.loads(raw) if raw else {"at": time.time(), "to": ""}
        record["status"] = status
        record["reason"] = reason
        await self._client.set(status_key(uuid), json.dumps(record), ex=self._ttl)

    async def mark_connected(self, lead_token: "str | None") -> None:
        """The callee picked up — Vobiz fetched `/answer` for this token."""
        if not lead_token:
            return
        try:
            await self._touch(lead_token, CONNECTED, "")
        except Exception:  # noqa: BLE001 — a status write must never cost a live call
            _log.warning("could not mark a call connected; the UI may show a stale state")

    async def mark_ended(self, lead_token: "str | None", reason: str = "") -> None:
        if not lead_token:
            return
        try:
            await self._touch(lead_token, ENDED, reason)
        except Exception:  # noqa: BLE001
            _log.warning("could not mark a call ended; the UI may show a stale state")

    async def get(self, request_uuid: str) -> "dict | None":
        """The public view: `{status, reason}`. Never the number, never the token.

        `no_answer` is computed here rather than stored, because nothing exists to write it:
        an unanswered call produces no event anywhere in this system.
        """
        try:
            raw = await self._client.get(status_key(request_uuid))
        except Exception:  # noqa: BLE001 — an unreadable store is "unknown", not a 500
            _log.warning("call status unreadable")
            return None
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        status = record.get("status", DIALING)
        if status == DIALING:
            age = time.time() - float(record.get("at", 0) or 0)
            if age > NO_ANSWER_AFTER_SECS:
                status = NO_ANSWER
        return {"status": status, "reason": record.get("reason", "")}

    async def aclose(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()


def hour_key(epoch_secs: float) -> str:
    """The counter key for the hour `epoch_secs` falls in."""
    return f"roma:dialcount:{int(epoch_secs // 3600)}"


class HourlyDialCap:
    """How many calls this clock-hour, so one loop cannot spend the whole budget.

    The spend cap (`roma.spend`) is the ceiling on MONEY and it works, but it only trips once
    the money is gone. This trips on COUNT, in minutes, and catches the failure the other
    cannot see coming: a stuck retry in the browser, or an operator holding the button down.

    Fixed clock-hour buckets rather than a sliding window — a sliding window needs a sorted
    set and a trim on every call, to bound something that is already a blunt safety net. The
    edge case (up to 2N calls across a bucket boundary) is acceptable for a stop-the-bleeding
    limit and not for anything that had to be exact.

    Fails OPEN on a Redis error, like the other stores here: an unreachable counter must not
    stop the operator working. The spend cap is still underneath it.
    """

    def __init__(self, redis_url: "str | None" = None, *, client=None, limit: int = 20) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("HourlyDialCap needs a redis_url or a client")
            from redis import asyncio as redis_asyncio

            client = redis_asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._client = client
        self._limit = limit

    async def take(self, now: "float | None" = None) -> bool:
        """Claim one dial. True if allowed, False when the hour is spent.

        Increments FIRST and compares after, so two concurrent requests cannot both read
        `limit - 1` and both proceed. `INCR` is atomic; a read-then-write here would not be.
        """
        if self._limit <= 0:
            return False
        try:
            key = hour_key(now if now is not None else time.time())
            count = await self._client.incr(key)
            if count == 1:
                # Only on creation, so a long hour cannot keep pushing its own expiry out.
                await self._client.expire(key, 3600)
            return count <= self._limit
        except Exception:  # noqa: BLE001 — a broken counter must not stop the operator
            _log.warning("hourly dial cap unreadable; allowing the call")
            return True


__all__ = [
    "CallStatusStore",
    "HourlyDialCap",
    "hour_key",
    "status_key",
    "token_index_key",
    "DIALING",
    "CONNECTED",
    "ENDED",
    "NO_ANSWER",
    "NO_ANSWER_AFTER_SECS",
]
