"""The outbound trigger: lead record in Redis and Twilio Calls API request shape."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from roma.dialer.leadstore import (
    LEAD_TTL_SECONDS,
    SEGMENTS,
    OutboundLead,
    RedisLeadStore,
    lead_key,
)
from roma.dialer.trigger import (
    build_answer_url,
    lead_token_from_query,
    trigger_outbound_call,
)


class _FakeRedis:
    """Minimal async stand-in: `set(key, value, ex=)` / `get(key)`."""

    def __init__(self):
        self.data = {}
        self.expiries = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.expiries[key] = ex

    async def get(self, key):
        return self.data.get(key)


class _FakeTwilio:
    """Records exactly what would go on the wire."""

    def __init__(self, sid="CA" + "1" * 32):
        self.calls = []
        self.sid = sid
        self.calls_api = self

    @property
    def calls(self):
        return self.calls_api

    @calls.setter
    def calls(self, value):
        self.created = value

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(sid=self.sid)


LEAD = OutboundLead(
    phone="+919327858018",
    lead_name="Nihit",
    city="Vadodara",
    segment="working_professional",
)


def _trigger(store, client, base_url="https://host.example"):
    return asyncio.run(
        trigger_outbound_call(
            LEAD,
            store=store,
            client=client,
            from_number="+917971543192",
            base_url=base_url,
        )
    )


# --- the lead record ----------------------------------------------------------


def test_the_lead_lands_in_redis_before_the_call_is_fired():
    """Order is load-bearing: Vobiz fetches the answer URL the moment the callee picks up.

    A record written after the POST races the carrier, and Roma answers a call she triggered
    herself knowing nothing about the person on the other end.
    """
    redis, seen = _FakeRedis(), []

    class _WatchingTwilio(_FakeTwilio):
        def create(self, **kw):
            seen.append(dict(redis.data))  # snapshot Redis AT dial time
            return super().create(**kw)

    store = RedisLeadStore(client=redis)
    result = _trigger(store, _WatchingTwilio())

    assert seen, "calls.create was never invoked"
    assert lead_key(result.lead_token) in seen[0], "lead was not stored before dialling"


def test_the_record_round_trips_and_carries_a_ttl():
    redis = _FakeRedis()
    store = RedisLeadStore(client=redis)
    result = _trigger(store, _FakeTwilio())

    got = asyncio.run(store.get(result.lead_token))
    assert got == LEAD
    assert redis.expiries[lead_key(result.lead_token)] == LEAD_TTL_SECONDS


def test_reading_the_record_is_not_destructive():
    """A carrier retry of the answer webhook must not get an amnesiac call."""
    store = RedisLeadStore(client=_FakeRedis())
    result = _trigger(store, _FakeTwilio())
    assert asyncio.run(store.get(result.lead_token)) is not None
    assert asyncio.run(store.get(result.lead_token)) is not None


def test_an_unknown_or_corrupt_token_degrades_instead_of_raising():
    """A bad record must not 500 the answer route while the phone is already ringing."""
    redis = _FakeRedis()
    store = RedisLeadStore(client=redis)
    assert asyncio.run(store.get("never-minted")) is None
    assert asyncio.run(store.get(None)) is None
    redis.data[lead_key("bad")] = "{not json"
    assert asyncio.run(store.get("bad")) is None


# --- the request shape, copied from docs/call/make-call -----------------------


def test_the_call_payload_matches_twilio_calls_api():
    store = RedisLeadStore(client=_FakeRedis())
    client = _FakeTwilio()
    result = _trigger(store, client)
    assert client.created == [
        {
            "to": LEAD.phone,
            "from_": "+917971543192",
            "url": result.answer_url,
            "method": "POST",
        }
    ]


def test_every_call_gets_its_own_answer_url_carrying_its_own_token():
    store = RedisLeadStore(client=_FakeRedis())
    client = _FakeTwilio()
    a, b = _trigger(store, client), _trigger(store, client)

    assert a.lead_token != b.lead_token
    assert client.created[0]["url"] != client.created[1]["url"]
    assert client.created[0]["url"] == a.answer_url
    assert a.answer_url.startswith("https://host.example/answer?lead=")
    assert a.request_uuid == "CA" + "1" * 32


def test_the_answer_url_survives_a_base_url_with_a_trailing_slash():
    assert build_answer_url("https://h.example/", "tok") == "https://h.example/answer?lead=tok"


def test_the_token_round_trips_through_the_query_string():
    from urllib.parse import parse_qs, urlparse

    store = RedisLeadStore(client=_FakeRedis())
    result = _trigger(store, _FakeTwilio())
    q = parse_qs(urlparse(result.answer_url).query)
    assert lead_token_from_query({"lead": q["lead"][0]}) == result.lead_token
    assert lead_token_from_query({}) is None
    assert lead_token_from_query(None) is None


# --- dynamic variables --------------------------------------------------------


def test_the_dynamic_variables_reach_call_state_and_the_prompt():
    """The whole point of the token: what the trigger knew must reach prompt assembly."""
    from roma.controller.state import CallState

    state = CallState(call_sid="c1", **LEAD.as_state_seed())
    assert state.lead_name == "Nihit"
    assert state.city == "Vadodara"
    assert state.segment == "working_professional"

    v = state.as_prompt_vars()
    assert v["lead_name"] == "Nihit"
    assert v["segment"] == "working_professional"
    assert v["branch"] == "Vadodara"


def test_the_seed_never_leaks_the_extra_bag_to_the_model():
    """`llm.prompts._render` raises on an unresolvable var, and `extra` is CRM data."""
    lead = OutboundLead(phone="+91", extra={"crm_id": 7, "utm": "fb"})
    assert set(lead.as_state_seed()) == {"branch", "segment"}


def test_an_unknown_name_is_omitted_from_the_seed_never_passed_as_empty():
    """THE bug this seeding path reintroduced, from the other side.

    `CallState.lead_name` is None-by-default precisely so `next_discovery_slot()` will ask
    for it (ce4a2e9). Seeding "" for a CRM record with no name marks it already-captured:
    Roma never asks, and `filled_discovery_count()` is inflated by two, cutting P2 short.
    """
    from roma.controller.state import CallState

    blank = OutboundLead(phone="+91", lead_name="", city="", segment="student")
    assert "lead_name" not in blank.as_state_seed()
    assert "city" not in blank.as_state_seed()

    state = CallState(call_sid="c", **blank.as_state_seed())
    assert state.lead_name is None
    assert state.next_discovery_slot() == "lead_name"
    assert state.filled_discovery_count() == 0

    known = OutboundLead(phone="+91", lead_name="Nihit", city="Vadodara")
    seeded = CallState(call_sid="c", **known.as_state_seed())
    assert seeded.next_discovery_slot() == "current_status"


def test_segment_survives_a_checkpoint_round_trip():
    from roma.controller.state import CallState

    state = CallState(call_sid="c1", segment="unemployed")
    assert CallState.from_dict(json.loads(json.dumps(state.to_dict()))).segment == "unemployed"


def test_an_inbound_call_has_no_segment_and_that_is_valid():
    from roma.controller.state import CallState

    assert CallState().segment == ""
    assert CallState().as_prompt_vars()["segment"] == ""


# --- lead validation ----------------------------------------------------------


def test_a_lead_must_have_a_number_to_dial():
    with pytest.raises(ValueError, match="phone"):
        OutboundLead(phone="")


def test_an_unknown_segment_is_refused_at_construction():
    """Caught at the trigger, not discovered as a prompt that silently means nothing."""
    with pytest.raises(ValueError, match="segment"):
        OutboundLead(phone="+91", segment="freelancer")
    for seg in SEGMENTS:
        assert OutboundLead(phone="+91", segment=seg).segment == seg
    assert OutboundLead(phone="+91").segment == ""
