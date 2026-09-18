from datetime import datetime

from roma.domain.calls.consent import CONSENT_LINE
from roma.domain.calls.dnd import StubRegistry
from roma.domain.calls.precall import precall_check
from roma.domain.costs.spend import SpendLedger, Usage

PHONE = "+919876543210"


def _spent_ledger(tmp_path):
    """A ledger with a real charge on it — ₹250 of gpt-4o, well past any test budget."""
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    ledger.record(Usage(input_tokens=1_000_000), "gpt-4o")
    return ledger


def _ist(hour, minute=0):
    return datetime(2026, 7, 24, hour, minute)


def _consented():
    return StubRegistry(consented={PHONE})


def test_consented_and_in_window_may_dial_with_consent_line():
    v = precall_check(PHONE, _ist(14), _consented())
    assert v.may_dial is True
    assert v.reason == "ok"
    assert v.consent_line == CONSENT_LINE


def test_outside_window_blocks_and_hides_consent_line():
    v = precall_check(PHONE, _ist(23), _consented())
    assert v.may_dial is False
    assert v.reason == "block:window"
    assert v.consent_line is None


def test_unknown_number_blocks_even_in_window():
    v = precall_check("+919999999999", _ist(14), _consented())
    assert v.may_dial is False
    assert v.reason == "block:dnd"
    assert v.consent_line is None


def test_failsafe_blocks_when_registry_raises():
    class ExplodingRegistry:
        def is_dialable(self, phone):
            raise RuntimeError("registry down")

    v = precall_check(PHONE, _ist(14), ExplodingRegistry())
    assert v.may_dial is False
    assert v.reason.startswith("precall-error:")
    assert v.consent_line is None


def test_a_spent_budget_blocks_the_dial(tmp_path):
    """The ₹100 testing cap, made real (2026-07-27).

    Before this, the cap existed only inside `scripts/run_eval.py --live` and only for the
    lifetime of that process. Live calls — the expensive ones — were never metered at all.
    On gpt-4o a five-minute call is roughly ₹8 against a ₹100 phase budget, so "we will
    keep an eye on it" is about twelve calls away from being wrong.
    """
    spent = _spent_ledger(tmp_path)
    v = precall_check(PHONE, _ist(14), _consented(), spend=spent, budget_inr=1.0)
    assert v.may_dial is False
    assert v.reason == "block:budget"
    assert v.consent_line is None


def test_headroom_still_dials(tmp_path):
    fresh = SpendLedger(tmp_path / "fresh.jsonl")
    v = precall_check(PHONE, _ist(14), _consented(), spend=fresh, budget_inr=100.0)
    assert v.may_dial is True
    assert v.reason == "ok"


def test_dnd_is_reported_before_budget(tmp_path):
    """A compliance block must never be masked by a money block. If someone is on the DND
    list, that is the fact worth surfacing — a budget top-up must not make them dialable.
    """
    spent = _spent_ledger(tmp_path)
    v = precall_check("+919999999999", _ist(14), _consented(), spend=spent, budget_inr=1.0)
    assert v.reason == "block:dnd"


def test_failsafe_blocks_when_the_ledger_raises():
    """Same posture as the registry: an accounting failure must not open the dialer."""

    class ExplodingLedger:
        def exhausted(self, budget_inr):
            raise RuntimeError("disk gone")

    v = precall_check(PHONE, _ist(14), _consented(), spend=ExplodingLedger(), budget_inr=100.0)
    assert v.may_dial is False
    assert v.reason.startswith("precall-error:")


def test_window_checked_before_dnd():
    class ExplodingRegistry:
        def is_dialable(self, phone):
            raise RuntimeError("should not be called")

    v = precall_check(PHONE, _ist(23), ExplodingRegistry())
    assert v.reason == "block:window"
