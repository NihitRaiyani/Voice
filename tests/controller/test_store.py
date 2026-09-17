"""Call-state store + resume-on-drop (docs/06). InMemory for the common path; fakeredis
exercises the real Redis serialization + TTL. Resume returns the phase and filled slots so
none is re-asked.

Async is driven with asyncio.run (the repo convention — see tests/telephony/test_pretts.py)."""

import asyncio

import fakeredis.aioredis
import pytest

from roma.controller.state import DISCOVERY_ORDER, CallState
from roma.controller.store import (
    STATE_TTL_SECONDS,
    InMemoryCallStateStore,
    RedisCallStateStore,
    state_key,
)


def _mid_call_state() -> CallState:
    return CallState(
        call_sid="CA_resume",
        phase="p5_pivot",
        phase_turn_count=1,
        lead_name="Asha",
        education="12th",
        passing_year="2020",
        current_status="student",
        city="Vadodara",
        timing_constraint="evenings",
        slots_offered=["Mon 6pm", "Tue 11am"],
        objection_counts={"cost": 1},
    )


def test_inmemory_round_trip():
    async def run():
        store = InMemoryCallStateStore()
        s = _mid_call_state()
        await store.save(s)
        return await store.load("CA_resume")

    assert asyncio.run(run()) == _mid_call_state()


def test_inmemory_missing_returns_none():
    assert asyncio.run(InMemoryCallStateStore().load("nope")) is None


def test_redis_round_trip_and_resume_preserves_phase_and_slots():
    async def run():
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RedisCallStateStore(client=client)
        await store.save(_mid_call_state())
        return await store.load("CA_resume")

    resumed = asyncio.run(run())
    assert resumed.phase == "p5_pivot"
    assert resumed.filled_discovery_count() == len(DISCOVERY_ORDER)
    assert resumed.next_discovery_slot() is None
    assert resumed.objection_counts == {"cost": 1}


def test_redis_missing_returns_none():
    async def run():
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        return await RedisCallStateStore(client=client).load("absent")

    assert asyncio.run(run()) is None


def test_redis_sets_ttl():
    async def run():
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RedisCallStateStore(client=client)
        await store.save(_mid_call_state())
        return await client.ttl(state_key("CA_resume"))

    ttl = asyncio.run(run())
    assert 0 < ttl <= STATE_TTL_SECONDS


def test_url_or_client_required():
    with pytest.raises(ValueError):
        RedisCallStateStore()
