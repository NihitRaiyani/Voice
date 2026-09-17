from datetime import datetime

from roma.dialer import CONSENT_LINE
from roma.dialer.dnd import StubRegistry
from roma.telephony.dialer import DialResult, place_call

PHONE = "+919876543210"
FROM = "+917971543192"
ANSWER = "https://roma.example.com/answer"


def _ist(hour):
    return datetime(2026, 7, 24, hour)


class _RecordingClient:
    """Mock Vobiz client: records create_call(...) invocations, never touches the network."""

    def __init__(self):
        self.created = []

    def create_call(self, **kwargs):
        self.created.append(kwargs)
        return {"call_uuid": "CA_fake_uuid"}


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
    """Vobiz takes an answer_url and fetches the XML when the callee picks up — Twilio took
    its TwiML inline, which is why this argument changed shape rather than name."""
    client = _RecordingClient()
    registry = StubRegistry(consented={PHONE})
    result = place_call(PHONE, _ist(14), registry, client, FROM, ANSWER)

    assert result.dialed is True
    assert result.call_sid == "CA_fake_uuid"
    assert result.consent_line == CONSENT_LINE

    assert len(client.created) == 1
    kwargs = client.created[0]
    assert kwargs["to"] == PHONE
    assert kwargs["from_"] == FROM
    assert kwargs["answer_url"] == ANSWER


def test_the_call_id_survives_an_unfamiliar_response_key():
    """The exact key Vobiz returns is not pinned down in the docs we have. It is used for
    logging and as a filename/Redis key, and `media.py` learns the authoritative id from the
    stream's `start` event regardless — so an unknown shape must degrade, never raise."""
    from roma.telephony.dialer import _call_id_from

    assert _call_id_from({"request_uuid": "RQ_1"}) == "RQ_1"
    assert _call_id_from({"something_else": "x"}) is None
    assert _call_id_from(None) is None
