from datetime import datetime
from types import SimpleNamespace

from roma.dialer import CONSENT_LINE
from roma.dialer.dnd import StubRegistry
from roma.telephony.dialer import DialResult, place_call

PHONE = "+919876543210"
FROM = "+917971543192"
ANSWER = "https://roma.example.com/answer"


def _ist(hour):
    return datetime(2026, 7, 24, hour)


class _RecordingClient:
    def __init__(self):
        self.created = []
        self.calls = self

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(sid="CA_fake_uuid")


def test_blocked_verdict_never_dials():
    client = _RecordingClient()
    result = place_call(PHONE, _ist(14), StubRegistry(), client, FROM, ANSWER)
    assert result == DialResult(False, None, "block:dnd", None)
    assert client.created == []


def test_outside_window_never_dials():
    client = _RecordingClient()
    registry = StubRegistry(consented={PHONE})
    result = place_call(PHONE, _ist(23), registry, client, FROM, ANSWER)
    assert result.dialed is False
    assert result.reason == "block:window"
    assert client.created == []


def test_a_spent_budget_never_dials(tmp_path):
    """The ₹100 testing cap has to be able to stop a call being placed, or it is a report
    rather than a cap. gpt-4o is ~₹8 for a five-minute call, so the phase is about twelve
    calls long and the twelfth must be refused rather than noticed afterwards."""
    from roma.spend import SpendLedger, Usage

    ledger = SpendLedger(tmp_path / "spend.jsonl")
    ledger.record(Usage(input_tokens=1_000_000), "gpt-4o")

    client = _RecordingClient()
    registry = StubRegistry(consented={PHONE})
    result = place_call(
        PHONE, _ist(14), registry, client, FROM, ANSWER, spend=ledger, budget_inr=100.0
    )
    assert result.dialed is False
    assert result.reason == "block:budget"
    assert client.created == []


def test_may_dial_places_one_call_pointing_at_the_answer_url():
    client = _RecordingClient()
    registry = StubRegistry(consented={PHONE})
    result = place_call(PHONE, _ist(14), registry, client, FROM, ANSWER)

    assert result.dialed is True
    assert result.call_sid == "CA_fake_uuid"
    assert result.consent_line == CONSENT_LINE

    assert client.created == [
        {"to": PHONE, "from_": FROM, "url": ANSWER, "method": "POST"}
    ]


def test_the_call_sid_degrades_when_the_sdk_response_has_none():
    from roma.telephony.dialer import _created_call_sid

    assert _created_call_sid(SimpleNamespace(sid="CA_1")) == "CA_1"
    assert _created_call_sid(SimpleNamespace(sid=None)) is None
    assert _created_call_sid(None) is None
