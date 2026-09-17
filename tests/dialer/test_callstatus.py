"""Call status for the web UI (docs/13): what `GET /api/call/{uuid}` can honestly say.

fakeredis is constructed fresh INSIDE each test and injected via `client=`, matching
`tests/postcall/test_queue_and_store.py`. Async via `asyncio.run` — there is no
pytest-asyncio.
"""

import asyncio
import json
import time

import fakeredis

from roma.dialer.callstatus import (
    CONNECTED,
    DIALING,
    ENDED,
    NO_ANSWER,
    NO_ANSWER_AFTER_SECS,
    CallStatusStore,
    status_key,
    token_index_key,
)

UUID = "req-abc-123"
TOKEN = "tok_xyz"
TO = "+919876543210"


def _client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def test_a_fired_call_reads_as_dialing():
    async def run():
        store = CallStatusStore(client=_client())
        await store.put_dialing(UUID, TOKEN, TO)
        return await store.get(UUID)

    assert asyncio.run(run()) == {"status": DIALING, "reason": ""}


def test_the_token_index_is_what_lets_answer_find_the_call():
    """`/answer` and the teardown know only the lead TOKEN — the uuid never reaches them.

    Without this reverse key there is no way to move a record off `dialing`, which is the
    whole reason the index exists.
    """

    async def run():
        client = _client()
        store = CallStatusStore(client=client)
        await store.put_dialing(UUID, TOKEN, TO)
        return await client.get(token_index_key(TOKEN))

    assert asyncio.run(run()) == UUID


def test_connected_then_ended_moves_the_record():
    async def run():
        store = CallStatusStore(client=_client())
        await store.put_dialing(UUID, TOKEN, TO)
        await store.mark_connected(TOKEN)
        mid = await store.get(UUID)
        await store.mark_ended(TOKEN, "won")
        return mid, await store.get(UUID)

    mid, end = asyncio.run(run())
    assert mid["status"] == CONNECTED
    assert end["status"] == ENDED and end["reason"] == "won"


def test_an_unanswered_call_reads_as_no_answer_without_anyone_writing_it():
    """The case this session hit twice, and the reason `no_answer` is DERIVED.

    Vobiz is sent no status callback, so when a callee does not pick up, nothing in this
    system ever hears about it — `/answer` is never fetched and no socket opens. A UI that
    waited for a writer would sit on "dialing" for ever. Age is the only signal available.
    """

    async def run():
        client = _client()
        store = CallStatusStore(client=client)
        await store.put_dialing(UUID, TOKEN, TO)
        # Backdate past the threshold rather than sleeping through it.
        stale = json.loads(await client.get(status_key(UUID)))
        stale["at"] = time.time() - (NO_ANSWER_AFTER_SECS + 5)
        await client.set(status_key(UUID), json.dumps(stale))
        return await store.get(UUID)

    assert asyncio.run(run())["status"] == NO_ANSWER


def test_a_connected_call_never_decays_into_no_answer():
    """Only `dialing` ages out. A long call that has been answered must not be relabelled
    as unanswered just for lasting more than a minute — which every real call does."""

    async def run():
        client = _client()
        store = CallStatusStore(client=client)
        await store.put_dialing(UUID, TOKEN, TO)
        await store.mark_connected(TOKEN)
        rec = json.loads(await client.get(status_key(UUID)))
        rec["at"] = time.time() - (NO_ANSWER_AFTER_SECS * 10)
        await client.set(status_key(UUID), json.dumps(rec))
        return await store.get(UUID)

    assert asyncio.run(run())["status"] == CONNECTED


def test_the_status_payload_never_carries_the_number_or_the_token():
    """The number is the lead's PII and the token is an authority (docs/07). The browser
    needs neither, and the operator typed the number in themselves."""

    async def run():
        store = CallStatusStore(client=_client())
        await store.put_dialing(UUID, TOKEN, TO)
        return await store.get(UUID)

    record = asyncio.run(run())
    assert set(record) == {"status", "reason"}
    assert TO not in json.dumps(record)
    assert TOKEN not in json.dumps(record)


def test_an_unknown_uuid_is_none_not_an_error():
    async def run():
        return await CallStatusStore(client=_client()).get("never-dialled")

    assert asyncio.run(run()) is None


def test_marking_a_call_we_never_recorded_is_silent():
    """Inbound calls and CLI-placed calls have no status row and never needed one. Marking
    them must be a no-op, not an exception on the live media path."""

    async def run():
        store = CallStatusStore(client=_client())
        await store.mark_connected("token-with-no-record")
        await store.mark_ended("token-with-no-record", "")
        return True

    assert asyncio.run(run())


def test_the_record_carries_a_ttl():
    async def run():
        client = _client()
        store = CallStatusStore(client=client, ttl=1800)
        await store.put_dialing(UUID, TOKEN, TO)
        return await client.ttl(status_key(UUID)), await client.ttl(token_index_key(TOKEN))

    status_ttl, index_ttl = asyncio.run(run())
    assert status_ttl > 0 and index_ttl > 0, "status rows must expire, not accumulate"


# --- hourly dial cap ---------------------------------------------------------------------


def test_the_cap_allows_up_to_the_limit_then_refuses():
    """The bound the spend cap cannot provide: it trips on COUNT, in minutes, where the
    money ceiling only trips once the money is gone."""
    from roma.dialer.callstatus import HourlyDialCap

    async def run():
        cap = HourlyDialCap(client=_client(), limit=3)
        return [await cap.take() for _ in range(5)]

    assert asyncio.run(run()) == [True, True, True, False, False]


def test_the_cap_is_per_clock_hour():
    """Fixed buckets, so a spent hour does not poison the next one."""
    from roma.dialer.callstatus import HourlyDialCap

    async def run():
        cap = HourlyDialCap(client=_client(), limit=2)
        hour_one = 3600.0 * 100
        spent = [await cap.take(hour_one) for _ in range(3)]
        return spent, await cap.take(hour_one + 3600)

    spent, next_hour = asyncio.run(run())
    assert spent == [True, True, False]
    assert next_hour is True


def test_the_counter_expires_so_buckets_do_not_accumulate():
    from roma.dialer.callstatus import HourlyDialCap, hour_key

    async def run():
        client = _client()
        cap = HourlyDialCap(client=client, limit=5)
        await cap.take(3600.0 * 100)
        return await client.ttl(hour_key(3600.0 * 100))

    assert asyncio.run(run()) > 0


def test_a_zero_limit_refuses_everything():
    """A misconfigured `MAX_CALLS_PER_HOUR=0` must stop calls, not wave them through — the
    same fail-closed reading as an unset API_TOKEN."""
    from roma.dialer.callstatus import HourlyDialCap

    async def run():
        return await HourlyDialCap(client=_client(), limit=0).take()

    assert asyncio.run(run()) is False


def test_an_unreachable_counter_fails_open():
    """The opposite posture to the token, deliberately: a broken Redis must not stop the
    operator working, and the spend cap is still underneath."""
    from roma.dialer.callstatus import HourlyDialCap

    class _Broken:
        async def incr(self, key):
            raise RuntimeError("redis is down")

    async def run():
        return await HourlyDialCap(client=_Broken(), limit=5).take()

    assert asyncio.run(run()) is True
