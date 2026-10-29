"""Provider protocol extraction: validated, bounded JSON; all tests are offline."""

import asyncio

import pytest
from roma.core.ai_contracts import LLMChunk
from roma.domain.appointments.slots import (
    DiscoveryValue,
    ExtractionUnavailable,
    TimeSlot,
    extract_discovery_slot,
    extract_time_slot,
)


class FakeProvider:
    def __init__(self, parsed, capture=None, finish="stop"):
        self.parsed = parsed
        self.capture = capture
        self.finish = finish
        self.closed = False

    async def generate(self, request):
        if self.capture is not None:
            self.capture["request"] = request
        try:
            if isinstance(self.parsed, Exception):
                raise self.parsed
            content = (
                self.parsed.model_dump_json()
                if hasattr(self.parsed, "model_dump_json")
                else (self.parsed or "")
            )
            midpoint = len(content) // 2
            yield LLMChunk(content[:midpoint])
            yield LLMChunk(content[midpoint:], finish_reason=self.finish)
        finally:
            self.closed = True


def test_extract_discovery_returns_parsed_value():
    client = FakeProvider(DiscoveryValue(value="12th pass", confidence=0.92, raw="baarvi paas"))
    got = asyncio.run(extract_discovery_slot(client, "baarvi paas hoon", "education"))
    assert got.value == "12th pass" and got.confidence == 0.92
    assert client.closed


def test_extract_discovery_passes_field_and_asks_for_capped_json():
    cap = {}
    client = FakeProvider(DiscoveryValue(value="Vadodara", confidence=0.9), cap)
    asyncio.run(extract_discovery_slot(client, "Vadodara se", "city"))
    request = cap["request"]
    assert request.max_tokens == 40
    assert request.metadata["purpose"] == "extraction"
    assert "city" in request.messages[1].content
    assert "JSON" in request.messages[0].content
    assert request.model is None, "provider selection cannot be bypassed by a legacy model"


def test_booking_path_requires_the_complete_locally_validated_schema():
    cap = {}
    client = FakeProvider(TimeSlot(day_offset=1, confidence=0.9), cap)
    asyncio.run(extract_time_slot(client, "kal"))
    request = cap["request"]
    assert 0 < request.max_tokens <= 512
    assert "readback_confirmed" in request.messages[0].content
    assert "day_offset" in request.messages[0].content


def test_extract_time_returns_accepted_and_readback_flags():
    client = FakeProvider(
        TimeSlot(day_offset=1, hour=6, period="evening", accepted=True, confidence=0.88)
    )
    got = asyncio.run(extract_time_slot(client, "haan kal shaam chhe baje theek hai"))
    assert got.accepted is True and got.day_offset == 1 and got.period == "evening"


def test_provider_failure_raises_the_typed_upstream_signal():
    with pytest.raises(ExtractionUnavailable):
        asyncio.run(extract_discovery_slot(FakeProvider(RuntimeError("provider down")),
                                          "anything", "education"))


@pytest.mark.parametrize(
    "content,finish",
    [
        (None, "stop"),
        ('{"confidence":2,"accepted":true}', "stop"),
        ('{"accepted":true}', "length"),
        ('{"accepted":true}', None),
        ("not JSON", "stop"),
        ("x" * 8193, "stop"),
    ],
)
def test_invalid_incomplete_and_oversized_booking_json_reasks(content, finish):
    client = FakeProvider(content, finish=finish)
    with pytest.raises(ExtractionUnavailable):
        asyncio.run(extract_time_slot(client, "hmm"))
    assert client.closed


def test_offer_context_stays_in_user_message():
    cap = {}
    asyncio.run(
        extract_time_slot(
            FakeProvider(TimeSlot(chose_offer=1, confidence=0.9), cap),
            "pehla",
            offered=["Tuesday 11 AM"],
        )
    )
    request = cap["request"]
    assert "Tuesday 11 AM" in request.messages[1].content
    assert "Tuesday 11 AM" not in request.messages[0].content


def test_time_extraction_obeys_provider_output_cap():
    cap = {}
    client = FakeProvider(TimeSlot(), cap)
    client.max_output_tokens = 128
    asyncio.run(extract_time_slot(client, "hmm"))
    assert cap["request"].max_tokens == 128


def test_cancellation_propagates_and_closes_stream():
    class CancellingProvider(FakeProvider):
        async def generate(self, request):
            try:
                yield LLMChunk('{"accepted":')
                raise asyncio.CancelledError
            finally:
                self.closed = True

    client = CancellingProvider(None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(extract_time_slot(client, "kal"))
    assert client.closed


@pytest.mark.parametrize('outcome', [RuntimeError('synthetic provider outage'), 'not JSON'])
def test_default_advance_turn_extraction_failure_preserves_discovery_budgets(outcome):
    from datetime import datetime

    from roma.domain.appointments.timeresolve import IST
    from roma.domain.conversation.state import CallState
    from roma.domain.conversation.turn import advance_turn

    async def run():
        current = CallState(call_sid='synthetic-default-extraction', stage='discover', lead_name='Amit')
        provider = FakeProvider(outcome)
        for _ in range(10):
            await advance_turn(current, 'I am a student', client=provider,
                               now=datetime(2026,10,29,10,tzinfo=IST))
            assert current.slot_attempts == {} and current.stage_turn_count == 0
            assert current.stage == 'discover' and current.next_discovery_slot() == 'current_status'
        assert current.turn_count == 10 and current.current_status is None
        assert provider.closed
    asyncio.run(run())


@pytest.mark.parametrize('text', ['', '\0', '<invalid>'])
def test_invalid_caller_input_is_ordinary_ambiguity_without_a_provider_request(text):
    cap = {}
    provider = FakeProvider(RuntimeError('must not be called'), cap)
    discovery = asyncio.run(extract_discovery_slot(provider, text, 'education'))
    booking = asyncio.run(extract_time_slot(provider, text))
    assert discovery.value is None and discovery.confidence == 0
    assert booking.confidence == 0 and not booking.accepted
    assert cap == {}


def test_default_time_extraction_outage_preserves_existing_readback():
    from datetime import datetime

    from roma.domain.appointments.timeresolve import IST
    from roma.domain.conversation.state import CallState
    from roma.domain.conversation.turn import advance_turn

    async def run():
        slot = '2026-10-30T15:00:00+05:30'
        current = CallState(stage='close', booking_required=True, accepted_slot=slot,
                            slot_status='accepted', slots_offered=[slot])
        await advance_turn(current, 'maybe', client=FakeProvider(RuntimeError('provider outage')),
                           now=datetime(2026,10,29,10,tzinfo=IST))
        assert current.accepted_slot == slot and current.slot_status == 'accepted'
        assert current.stage_turn_count == 0 and current.appointment_id is None
    asyncio.run(run())
