"""Live calls are metered (2026-07-27).

Before this, `Meter` existed only inside `scripts/run_eval.py --live`. The eval harness —
which replays scripted text against the model — was the metered path, and the live phone
calls, which cost far more and are the ones actually placed dozens of times a day, spent
real money with nothing counting it at all. `_UsageLogger` logged token counts and stopped
there: no rupees, no total, no ledger, nothing that survived the process.

That was survivable on gpt-4o-mini at roughly ₹0.5 a call. On gpt-4o it is ~₹8 a call
against a ₹100 phase budget, which is about twelve calls — few enough that "we would
notice" is not a plan.
"""

import asyncio

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection
from roma.domain.costs.spend import SpendLedger
from roma.realtime.pipeline import _UsageLogger


def _usage_frame(model="gpt-4o", prompt=3200, completion=70, cached=2800):
    """One turn's usage, shaped like a real one: live calls report cached=2304-3328 of a
    ~3200-token prompt, because the persona/rules prefix is deliberately cache-friendly
    (docs/11). A meter that ignored the cached rate would be wrong by most of the prompt.
    """
    return MetricsFrame(
        data=[
            LLMUsageMetricsData(
                processor="OpenAILLMService#0",
                model=model,
                value=LLMTokenUsage(
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=prompt + completion,
                    cache_read_input_tokens=cached,
                ),
            )
        ]
    )


def _drive(proc, frames):
    async def _noop(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    proc.push_frame = _noop

    async def run():
        for f in frames:
            await proc.process_frame(f, FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    return proc


def test_a_live_turn_lands_in_the_ledger(tmp_path):
    path = tmp_path / "spend.jsonl"
    u = _UsageLogger(enable_direct_mode=True, ledger=SpendLedger(path), call_sid="CA_test")
    _drive(u, [_usage_frame()])

    assert SpendLedger(path).spent_inr > 0.0


def test_the_turn_is_billed_at_the_model_the_frame_names(tmp_path):
    """pipecat reports the model OpenAI actually served, which is the only trustworthy
    source — `LLM_MODEL` is what we asked for, not necessarily what billed. Identical
    usage must cost far more on gpt-4o than on gpt-4o-mini."""
    mini_path, full_path = tmp_path / "mini.jsonl", tmp_path / "full.jsonl"
    _drive(
        _UsageLogger(enable_direct_mode=True, ledger=SpendLedger(mini_path)),
        [_usage_frame(model="gpt-4o-mini")],
    )
    _drive(
        _UsageLogger(enable_direct_mode=True, ledger=SpendLedger(full_path)),
        [_usage_frame(model="gpt-4o")],
    )
    assert SpendLedger(full_path).spent_inr > SpendLedger(mini_path).spent_inr


def test_spend_accumulates_across_turns(tmp_path):
    path = tmp_path / "spend.jsonl"
    u = _UsageLogger(enable_direct_mode=True, ledger=SpendLedger(path))
    _drive(u, [_usage_frame()])
    one = SpendLedger(path).spent_inr
    _drive(u, [_usage_frame(), _usage_frame()])
    assert SpendLedger(path).spent_inr > one * 2


def test_no_ledger_configured_is_not_a_crash(tmp_path):
    """The synthetic media tests build the app without a data dir, and an unmetered unit
    test must never be the thing that takes a call down."""
    _drive(_UsageLogger(enable_direct_mode=True), [_usage_frame()])


def test_the_usage_logger_reports_what_this_call_spent(tmp_path):
    """The teardown line needs a per-call number, not just a running total: "this call cost
    ₹8" is what tells you the phase is twelve calls long."""
    u = _UsageLogger(enable_direct_mode=True, ledger=SpendLedger(tmp_path / "s.jsonl"))
    _drive(u, [_usage_frame(), _usage_frame()])
    assert u.call_inr > 0.0
