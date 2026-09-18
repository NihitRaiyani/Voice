"""Structured-output slot extraction (docs/03). A fake AsyncOpenAI-shaped client returns
canned parsed models — no live API. We assert the function returns what the model parsed,
passes the pydantic response_format, and never raises (a failure → re-ask).

Async is driven with asyncio.run (the repo convention — see tests/telephony/test_pretts.py)."""

import asyncio

from roma.domain.appointments.slots import (
    DiscoveryValue,
    TimeSlot,
    extract_discovery_slot,
    extract_time_slot,
)


class _NS:
    pass


class _Completion:
    """Carries BOTH shapes: `.parsed` for the `beta.parse` path that `extract_time_slot`
    still uses, and `.content` JSON for the `json_object` path `extract_discovery_slot`
    moved to for latency (see that function's docstring)."""

    def __init__(self, parsed):
        msg = _NS()
        msg.parsed = parsed
        msg.content = parsed.model_dump_json() if hasattr(parsed, "model_dump_json") else None
        choice = _NS()
        choice.message = msg
        self.choices = [choice]


def make_client(parsed, capture=None):
    async def respond(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        if isinstance(parsed, Exception):
            raise parsed
        return _Completion(parsed)

    client = _NS()
    # The booking path (extract_time_slot) — strict schema.
    client.beta = _NS()
    client.beta.chat = _NS()
    client.beta.chat.completions = _NS()
    client.beta.chat.completions.parse = respond
    # The discovery path — json_object, capped, off the same fake.
    client.chat = _NS()
    client.chat.completions = _NS()
    client.chat.completions.create = respond
    return client


def test_extract_discovery_returns_parsed_value():
    client = make_client(DiscoveryValue(value="12th pass", confidence=0.92, raw="baarvi paas"))
    got = asyncio.run(extract_discovery_slot(client, "baarvi paas hoon", "education"))
    assert got.value == "12th pass" and got.confidence == 0.92


def test_extract_discovery_passes_field_and_asks_for_json():
    """Discovery runs BEFORE the reply prompt is assembled, so its latency is dead air the
    lead hears. It uses `json_object` + a token cap rather than strict schema parsing —
    measured at half the median and, more importantly, without the 2.46s tail.

    The cap must stay: an uncapped reply can run on past the two scalars we want and turn a
    0.9s call back into a slow one."""
    cap: dict = {}
    client = make_client(DiscoveryValue(value="Vadodara", confidence=0.9), capture=cap)
    asyncio.run(extract_discovery_slot(client, "Vadodara se", "city"))
    assert cap["response_format"] == {"type": "json_object"}
    assert cap["max_tokens"] == 40
    assert "city" in cap["messages"][1]["content"]
    assert "JSON" in cap["messages"][0]["content"], "model was not told the output shape"


def test_the_booking_path_keeps_strict_schema_parsing():
    """`extract_time_slot` must NOT follow discovery onto the loose path. Its schema is six
    coupled fields and it decides a real visit — a silently mis-parsed field there is a lead
    told the wrong day, not a re-ask."""
    cap: dict = {}
    client = make_client(TimeSlot(day_offset=1, confidence=0.9), capture=cap)
    asyncio.run(extract_time_slot(client, "kal"))
    assert cap["response_format"] is TimeSlot


def test_extract_time_returns_accepted_and_readback_flags():
    client = make_client(
        TimeSlot(day_offset=1, hour=6, period="evening", accepted=True, confidence=0.88)
    )
    got = asyncio.run(extract_time_slot(client, "haan kal shaam chhe baje theek hai"))
    assert got.accepted is True and got.day_offset == 1 and got.period == "evening"


def test_extraction_never_raises_on_client_error():
    client = make_client(RuntimeError("openai down"))
    got = asyncio.run(extract_discovery_slot(client, "anything", "education"))
    assert got.value is None and got.confidence == 0.0


def test_time_extraction_none_parsed_falls_back_to_reask():
    client = make_client(None)
    got = asyncio.run(extract_time_slot(client, "hmm"))
    assert got.confidence == 0.0
