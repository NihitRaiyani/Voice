"""The per-run budget guard for `scripts/run_eval.py --live` (docs/02 "Cost cap").

The testing-phase ceiling is ₹100 — revised down from ₹200 on 2026-07-26 because ₹100 is
what is actually left on the account. docs/02 is explicit that the cap is **enforced by a
hard spend limit in the OpenAI dashboard, not by discipline**, and nothing here changes
that. This module lets one eval run refuse to start and halt mid-run, so a runaway loop
reports a partial result instead of discovering the ceiling by having the account reject a
call.

**Pricing now lives in `roma.spend`, not here.** It used to be a module-level table of
three constants — gpt-4o-mini's rates — with nothing tying them to the model actually
configured. That was survivable while everything ran on mini, and became a 16.7x
under-report the moment `LLM_MODEL` moved to gpt-4o. Worse, the same three constants had
no equivalent on the live phone path, which was not metered at all. One table, one place,
and `model` is a required argument on every call that spends money.

Two consequences of being a second belt, both deliberate:

1. **It rounds against us.** Rates are ceilings and `Meter.spent_inr` never rounds down.
   A guard that drifts optimistic is worse than no guard — it converts "you are at the
   limit" into "you were at the limit two hundred calls ago".
2. **It only counts what it is told.** A call whose usage is never recorded is invisible
   here. Every OpenAI call on the eval path — generation AND the structured-output slot
   extractors, which are easy to forget because they are small — must go through `record`.

`Meter` is per-RUN and per-PROCESS by design; it is the within-run halt. What holds a cap
across runs and restarts is `roma.spend.SpendLedger`, which is on disk.
"""

import logging
from dataclasses import dataclass

from roma.spend import PRICES, USD_TO_INR, Usage, inr_for

_log = logging.getLogger("roma.eval")


class BudgetExceeded(RuntimeError):
    """The run crossed the cap. Carries the meter so the caller can report the partial."""

    def __init__(self, meter: "Meter") -> None:
        super().__init__(
            f"OpenAI budget exhausted: spent ₹{meter.spent_inr:.2f} of ₹{meter.budget_inr:.2f} "
            f"over {meter.calls} call(s)"
        )
        self.meter = meter


@dataclass
class Meter:
    """Running spend for one live eval run.

    Not persisted between runs, and that is a real limitation worth stating rather than
    papering over: this meter knows what THIS process spent, not what the account has spent
    all week. `roma.spend.SpendLedger` is what carries the total across processes; the
    dashboard limit is what makes the cap hold at all.
    """

    budget_inr: float
    spent_inr: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def remaining_inr(self) -> float:
        return max(0.0, self.budget_inr - self.spent_inr)

    def check_before_call(self) -> None:
        """Raise if there is no headroom left. Call BEFORE spending, not after."""
        if self.spent_inr >= self.budget_inr:
            raise BudgetExceeded(self)

    def record(self, usage: Usage, model: str) -> float:
        """Add one call's usage on `model`. Returns its cost. Raises once the total crosses
        the cap.

        `model` is required, not defaulted. A default is exactly how this meter came to
        bill every model at gpt-4o-mini's rate.

        The charge is recorded BEFORE the raise: the money is already spent, and a meter
        that under-reports on the way out is the one number nobody can afford to be wrong.
        """
        cost = inr_for(usage, model)
        self.spent_inr += cost
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        _log.info(
            "openai call: model=%s in=%d cached=%d out=%d cost=₹%.2f total=₹%.2f/%.2f",
            model,
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            cost,
            self.spent_inr,
            self.budget_inr,
        )
        if self.spent_inr >= self.budget_inr:
            raise BudgetExceeded(self)
        return cost


__all__ = [
    "Usage",
    "Meter",
    "BudgetExceeded",
    "inr_for",
    "USD_TO_INR",
    "PRICES",
]
