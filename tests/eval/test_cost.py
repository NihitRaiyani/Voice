"""The ₹100 budget guard (docs/02 "Cost cap").

The cap was revised down from ₹200 on 2026-07-26 because ₹100 is what is actually left on
the account. The dashboard hard limit is the real enforcement; this meter is the belt that
reports and halts. It is only worth having if it errs against us, so that is what is
pinned here.
"""

import pytest
from roma.eval.cost import BudgetExceeded, Meter, Usage, inr_for


def test_cached_input_is_not_double_counted():
    """`cached_input_tokens` is a SUBSET of `input_tokens` — that is how OpenAI reports
    prompt caching. Adding them would inflate every estimate on a stack whose prompts are
    deliberately prefix-cached (docs/11)."""
    u = Usage(input_tokens=1000, cached_input_tokens=800, output_tokens=0)
    assert u.billable_input == 200


def test_cost_rounds_up_never_down():
    """A guard that drifts optimistic is worse than no guard."""
    tiny = Usage(input_tokens=1, output_tokens=0)
    assert inr_for(tiny, "gpt-4o") > 0.0
    assert inr_for(Usage(), "gpt-4o") == 0.0


def test_the_meter_bills_the_model_it_was_given():
    """The eval harness has the same 16.7x exposure the live path had: it used ONE
    hardcoded gpt-4o-mini price table, so a `--live` run against gpt-4o would have reported
    a sixteenth of what it spent. There is now one price table (`roma.domain.costs.spend.PRICES`) and
    the model is a required argument, so there is no way to bill a call without saying
    which model billed it."""
    big = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    mini, full = Meter(budget_inr=10_000.0), Meter(budget_inr=10_000.0)
    mini.record(big, "gpt-4o-mini")
    full.record(big, "gpt-4o")
    assert full.spent_inr > mini.spent_inr


def test_meter_halts_at_the_budget():
    meter = Meter(budget_inr=0.10)
    big = Usage(input_tokens=2_000_000, output_tokens=2_000_000)
    with pytest.raises(BudgetExceeded):
        meter.record(big, "gpt-4o-mini")


def test_the_charge_is_recorded_before_the_raise():
    """The money is already spent by the time the guard fires. A meter that under-reports
    on the way out is the one number nobody can afford to be wrong."""
    meter = Meter(budget_inr=0.10)
    with pytest.raises(BudgetExceeded):
        meter.record(Usage(input_tokens=2_000_000, output_tokens=2_000_000), "gpt-4o-mini")
    assert meter.spent_inr > 0.10
    assert meter.calls == 1


def test_check_before_call_refuses_a_spent_budget():
    meter = Meter(budget_inr=1.0, spent_inr=1.0)
    with pytest.raises(BudgetExceeded):
        meter.check_before_call()


def test_check_before_call_allows_headroom():
    Meter(budget_inr=1.0, spent_inr=0.5).check_before_call()


def test_remaining_never_goes_negative():
    assert Meter(budget_inr=1.0, spent_inr=5.0).remaining_inr == 0.0


def test_usage_from_response_with_no_usage_is_zero_not_a_crash():
    """An unmetered call must not take the run down — but it is logged as unmetered,
    because silently costing zero is how a budget guard becomes decorative."""

    class _Resp:
        usage = None

    assert Usage.from_response(_Resp()) == Usage()


def test_usage_from_response_reads_cached_tokens():
    class _Details:
        cached_tokens = 64

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 20
        prompt_tokens_details = _Details()

    class _Resp:
        usage = _Usage()

    u = Usage.from_response(_Resp())
    assert (u.input_tokens, u.output_tokens, u.cached_input_tokens) == (100, 20, 64)
    assert u.billable_input == 36
