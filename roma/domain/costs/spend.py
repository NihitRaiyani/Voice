"""What OpenAI costs, per model, and the ledger that makes the ₹100 cap mean ₹100.

docs/02 is explicit that the testing-phase cap is **enforced by a hard spend limit in the
OpenAI dashboard, not by discipline**, and nothing here changes that. This module is the
second belt: it prices every call, remembers what has been spent across processes, and
lets the pre-dial gate refuse a call the budget cannot pay for.

It lives at the top level, not under `roma.eval`, because it is no longer an eval-only
concern. The meter used to exist solely inside `scripts/run_eval.py --live`, which meant
**every live phone call spent real money unmetered** — and `Meter` was per-process, so the
total reset on each `serve_media.py` restart, which happens before every single call. A
cap that resets before each use is not a cap.

Two postures are load-bearing and must survive any edit:

1. **It rounds against us.** Rates are ceilings, costs round UP, and an unpriced model is
   charged the dearest rate on the board. A guard that drifts optimistic is worse than no
   guard — it turns "you are at the limit" into "you were at the limit ten calls ago".
2. **A broken ledger blocks, it does not allow.** This mirrors `precall_check`: on any
   internal error the answer is BLOCK, never "proceed unguarded" (docs/07). A ledger that
   read as ₹0 on corruption would hand out unlimited dialling at the exact moment the
   accounting stopped working.

It records token COUNTS and a model name. It never records anything the lead said.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from roma.workers.postcall.paths import open_private

_log = logging.getLogger("roma.domain.costs.spend")


@dataclass(frozen=True)
class Prices:
    """USD per 1M tokens. Cached input is a separate, cheaper rate — not a discount
    applied to the input rate — because that is how OpenAI bills it."""

    input: float
    cached_input: float
    output: float


PRICES: "dict[str, Prices]" = {
    "gpt-4o-mini": Prices(input=0.15, cached_input=0.075, output=0.60),
    "gpt-4o": Prices(input=2.50, cached_input=1.25, output=10.00),
}

USD_TO_INR = 100.0


def _dearest() -> Prices:
    """The per-field maximum across the table — by construction at least as expensive as
    any priced model, on any usage shape."""
    return Prices(
        input=max(p.input for p in PRICES.values()),
        cached_input=max(p.cached_input for p in PRICES.values()),
        output=max(p.output for p in PRICES.values()),
    )


def prices_for(model: str) -> Prices:
    """Rates for `model`. An unpriced model is charged the dearest rate we know.

    Never raises. It is called from the live audio path, and ending a call in progress
    over a bookkeeping question would be a worse outcome than an over-estimate.
    """
    known = PRICES.get(model)
    if known is not None:
        return known
    _log.error(
        "model %r has no entry in roma.domain.costs.spend.PRICES — billing it at the most expensive "
        "known rate. Add it to the table; the estimate is wrong until you do.",
        model,
    )
    return _dearest()


@dataclass(frozen=True)
class Usage:
    """One OpenAI call's token counts, as `response.usage` reports them.

    `cached_input_tokens` is a SUBSET of `input_tokens` (that is how OpenAI reports prompt
    caching), so `billable_input` subtracts it rather than adding the two.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def billable_input(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    @classmethod
    def from_response(cls, response) -> "Usage":
        """Read usage off an OpenAI SDK response. Missing fields count as zero — but a
        response with no usage at all is worth knowing about, because it means a real call
        went unmetered."""
        u = getattr(response, "usage", None)
        if u is None:
            _log.warning("OpenAI response carried no usage; this call is UNMETERED")
            return cls()
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details is not None else 0
        return cls(
            input_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(u, "completion_tokens", 0) or 0),
            cached_input_tokens=int(cached or 0),
        )


def inr_for(usage: Usage, model: str) -> float:
    """Rupee cost of one call on `model`, rounded UP to the paisa.

    `model` is required. It was optional-by-omission before — there was one global price
    table — and that is exactly how the meter came to bill gpt-4o at gpt-4o-mini's rate.
    """
    p = prices_for(model)
    usd = (
        usage.billable_input / 1_000_000 * p.input
        + usage.cached_input_tokens / 1_000_000 * p.cached_input
        + usage.output_tokens / 1_000_000 * p.output
    )
    return math.ceil(usd * USD_TO_INR * 100) / 100


class SpendLedger:
    """Cumulative OpenAI spend for the testing phase, on disk, append-only.

    Append-only JSONL rather than a running total in a single mutable field: two concurrent
    calls (docs/08) each hold their own handle, and an append never read-modify-writes over
    another process's charge. It also leaves an audit trail — when the dashboard and this
    file disagree, the per-call rows are what tell you which one drifted.

    Lives under `Settings.roma_data_dir`, which is 0700 and gitignored (docs/07, docs/09).
    """

    def __init__(self, path: "str | Path") -> None:
        self.path = Path(path)

    def _read(self) -> "tuple[float, bool]":
        """(total_inr, trustworthy). A file we cannot parse is not trustworthy, and the
        caller must treat that as "no headroom" rather than "nothing spent"."""
        if not self.path.exists():
            return 0.0, True
        total = 0.0
        ok = True
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    total += float(json.loads(line)["inr"])
                except (ValueError, KeyError, TypeError):
                    _log.error("spend ledger has an unreadable row in %s", self.path)
                    ok = False
        except OSError as exc:
            _log.error("spend ledger unreadable (%s): %s", self.path, type(exc).__name__)
            return 0.0, False
        return total, ok

    @property
    def spent_inr(self) -> float:
        """Best-effort total of the rows that parsed. Read `exhausted()` for the decision —
        this number alone cannot tell you whether rows were lost."""
        return self._read()[0]

    def remaining_inr(self, budget_inr: float) -> float:
        total, ok = self._read()
        if not ok:
            return 0.0
        return max(0.0, budget_inr - total)

    def exhausted(self, budget_inr: float) -> bool:
        """True when there is no headroom left, OR when the ledger cannot be trusted."""
        total, ok = self._read()
        return (not ok) or total >= budget_inr

    def record(
        self,
        usage: Usage,
        model: str,
        *,
        call_sid: "str | None" = None,
        prefix: "str | None" = None,
    ) -> float:
        """Append one call's charge and return its rupee cost.

        Never raises: this runs on the live audio path, and a full disk must not end a call
        that is mid-sentence. A write that fails is logged at ERROR — loudly, because the
        charge really happened and the ledger no longer knows about it.

        `prefix` is a digest of the cached prompt span (`llm.prompts.cache_prefix`). It sits
        next to `cached` so ONE file answers "did our prefix move, or did their cache drop
        it" — the two explanations for the collapses seen in this ledger, which need
        opposite fixes. It is a hash of packaged prompt text, never anything the lead said.
        """
        cost = inr_for(usage, model)
        row = {
            "at": datetime.now(UTC).isoformat(),
            "model": model,
            "inr": cost,
            "in": usage.input_tokens,
            "cached": usage.cached_input_tokens,
            "out": usage.output_tokens,
        }
        if call_sid:
            row["sid"] = call_sid
        if prefix:
            row["prefix"] = prefix
        try:
            with open_private(self.path, "ab") as fh:
                fh.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError as exc:
            _log.error(
                "spend ledger write FAILED (%s): ₹%.2f on %s is now unrecorded",
                type(exc).__name__,
                cost,
                model,
            )
        return cost


__all__ = [
    "Prices",
    "PRICES",
    "USD_TO_INR",
    "Usage",
    "SpendLedger",
    "inr_for",
    "prices_for",
]
