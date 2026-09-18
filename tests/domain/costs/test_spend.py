"""What an OpenAI call costs, per model, and the ledger that makes ₹100 mean ₹100.

Two things were true before gpt-4o and are not true after it:

1. `roma.eval.cost` hardcoded ONE price table — gpt-4o-mini's. gpt-4o input is 16.7x
   that and output is 16.7x that, so a meter that kept mini's rates would have reported
   ₹6 at the moment ₹100 was gone. `cost.py`'s own docstring calls that out: "a guard
   that drifts optimistic is worse than no guard".
2. The meter lived only in `scripts/run_eval.py --live` and was per-process. Live calls
   spent real money entirely unmetered, and nothing survived a restart — so a cap "for
   the whole testing phase" could not hold across two calls, let alone twelve.

The rounding posture is inherited deliberately: rates are ceilings, costs round UP, and
every ambiguous case resolves AGAINST us. The OpenAI dashboard hard limit is still the
real enforcement (docs/02); this is the belt that reports and refuses.
"""

import pytest
from roma.domain.costs.spend import (
    PRICES,
    SpendLedger,
    Usage,
    inr_for,
    prices_for,
)


def test_gpt_4o_is_priced_and_is_not_charged_at_the_mini_rate():
    """THE defect this module exists for. Identical usage billed as gpt-4o must cost far
    more than as gpt-4o-mini. If these two ever return the same number, the meter has
    silently gone back to pricing every model as the cheap one."""
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    mini = inr_for(u, "gpt-4o-mini")
    full = inr_for(u, "gpt-4o")
    assert full > mini
    assert full / mini == pytest.approx(16.67, rel=0.01)


def test_cached_input_is_cheaper_than_fresh_input_on_every_model():
    """Roma's prompts are deliberately prefix-cached (docs/11) and live calls report
    cached=2304-3328 of ~3200 prompt tokens. A price table that ignored the cached rate
    would over-report by more than half the prompt on every single turn."""
    for model in PRICES:
        fresh = Usage(input_tokens=1_000_000)
        cached = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
        assert inr_for(cached, model) < inr_for(fresh, model)


def test_an_unpriced_model_is_charged_the_most_expensive_rate_we_know():
    """Fail-safe direction. A model nobody priced must never cost zero — that is how a
    budget guard becomes decorative. Charging the dearest known rate keeps the guard
    pessimistic, which is the only safe way for it to be wrong."""
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    dearest = max(inr_for(u, m) for m in PRICES)
    assert inr_for(u, "gpt-9-omni-turbo") == dearest


def test_prices_for_an_unpriced_model_does_not_raise():
    """It is called from the live audio path. Raising there would end a call in progress
    over a bookkeeping question."""
    assert prices_for("something-nobody-added") is not None


def test_cost_rounds_up_never_down():
    assert inr_for(Usage(input_tokens=1), "gpt-4o") > 0.0
    assert inr_for(Usage(), "gpt-4o") == 0.0


def test_spend_survives_a_restart(tmp_path):
    """The whole point. `Meter` was per-process, so ₹100 "for the testing phase" reset to
    zero every time serve_media.py was restarted — and it is restarted before every single
    live call. A ledger that forgets is a cap that does not exist."""
    path = tmp_path / "spend.jsonl"
    SpendLedger(path).record(Usage(input_tokens=1_000_000), "gpt-4o")

    reopened = SpendLedger(path)
    assert reopened.spent_inr == pytest.approx(250.0, rel=0.01)


def test_two_processes_appending_do_not_lose_each_others_spend(tmp_path):
    """Concurrent calls (docs/08) each hold their own ledger handle. Append-only means
    neither read-modify-writes over the other."""
    path = tmp_path / "spend.jsonl"
    a, b = SpendLedger(path), SpendLedger(path)
    a.record(Usage(input_tokens=1_000_000), "gpt-4o-mini")
    b.record(Usage(input_tokens=1_000_000), "gpt-4o-mini")
    assert SpendLedger(path).spent_inr == pytest.approx(30.0, rel=0.01)


def test_an_unreadable_ledger_reports_the_budget_as_gone(tmp_path):
    """Fail-safe, mirroring `precall_check`: on any internal error the answer is BLOCK,
    never "proceed unguarded" (docs/07). A corrupt ledger that read as ₹0 spent would
    hand you unlimited dialling at the exact moment the accounting broke."""
    path = tmp_path / "spend.jsonl"
    path.write_text("{not json at all\n", encoding="utf-8")
    assert SpendLedger(path).exhausted(budget_inr=100.0) is True


def test_a_missing_ledger_is_zero_spent_not_an_error(tmp_path):
    """First run of the testing phase. Nothing spent yet is a fact, not a failure."""
    ledger = SpendLedger(tmp_path / "never-written.jsonl")
    assert ledger.spent_inr == 0.0
    assert ledger.exhausted(budget_inr=100.0) is False


def test_the_ledger_is_exhausted_at_the_budget_not_past_it(tmp_path):
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    ledger.record(Usage(input_tokens=1_000_000), "gpt-4o")
    assert ledger.exhausted(budget_inr=100.0) is True
    assert ledger.remaining_inr(budget_inr=100.0) == 0.0


def test_the_ledger_lives_under_the_one_data_root(tmp_path):
    """docs/07/docs/09: everything Roma writes to disk goes under one configurable root,
    so there is a single directory to lock down and a single line in .gitignore. The spend
    ledger is not an exception just because it holds no PII."""
    from roma.workers.postcall.paths import data_root, spend_ledger_path

    class _S:
        roma_data_dir = str(tmp_path)

    assert spend_ledger_path(_S()).parent == data_root(_S())


def test_the_ledger_and_the_root_it_creates_are_owner_only(tmp_path):
    """Found by the commit security review, and the interesting half is not the file.

    `record()` used a bare `mkdir(parents=True)` + `open("a")`, which under the usual
    umask 022 yields a 0755 directory and a 0644 file. The ledger's own contents are dull
    — token counts, a model name, a call SID — but it is written on the FIRST LLM TURN of
    a call, which can be before the recorder or the job spool exist. So it was able to be
    the thing that created `var/roma` itself, at 0755.

    That is the leak, because `ensure_private_dir` only chmods directories IT created: a
    later `recordings/` gets its correct 0700 while the parent it sits under stays
    world-traversable, and every local user can list the call SIDs and dates inside. This
    is the exact trap `paths.ensure_private_dir` documents.
    """
    ledger_path = tmp_path / "root" / "spend.jsonl"
    SpendLedger(ledger_path).record(Usage(input_tokens=100), "gpt-4o", call_sid="CA_x")

    assert ledger_path.stat().st_mode & 0o077 == 0, "ledger is readable by other users"
    assert ledger_path.parent.stat().st_mode & 0o077 == 0, (
        "the data root is traversable by other users — a 0700 recordings/ underneath it "
        "still leaks its directory listing"
    )


def test_a_ledger_write_cannot_downgrade_an_existing_private_root(tmp_path):
    """The recorder may well get there first. Recording spend must not loosen what it
    found."""
    from roma.workers.postcall.paths import ensure_private_dir

    root = ensure_private_dir(tmp_path / "root")
    SpendLedger(root / "spend.jsonl").record(Usage(input_tokens=100), "gpt-4o")
    assert root.stat().st_mode & 0o077 == 0


def test_the_ledger_never_writes_transcript_text(tmp_path):
    """docs/07: lead PII does not leave the call. A spend ledger needs token COUNTS and a
    model name to do its job and has no business holding anything the lead said."""
    path = tmp_path / "spend.jsonl"
    SpendLedger(path).record(
        Usage(input_tokens=100, output_tokens=20, cached_input_tokens=64), "gpt-4o"
    )
    written = path.read_text(encoding="utf-8")
    assert "gpt-4o" in written
    for field in ("transcript", "text", "utterance", "phone"):
        assert field not in written
