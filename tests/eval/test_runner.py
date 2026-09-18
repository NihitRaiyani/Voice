"""Driving a script through the real controller, and the two properties that make the
offline mode trustworthy: it costs nothing, and its verdict does not depend on the clock.

Async tests use `asyncio.run(...)` — there is no pytest-asyncio in this repo.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from roma.domain.appointments.timeresolve import IST
from roma.eval import script as script_mod
from roma.eval.cost import BudgetExceeded, Meter, Usage
from roma.eval.runner import FROZEN_NOW, run_script

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "scripts"


def _load_all():
    return script_mod.load_dir(SCRIPTS_DIR)


def test_every_shipped_script_loads():
    scripts = _load_all()
    assert len(scripts) >= 4
    assert {s.name for s in scripts} >= {
        "clean_lock",
        "objection_detour",
        "evasive_p2_valve",
        "gujarati_cost_objection",
    }


@pytest.mark.parametrize("script", _load_all(), ids=lambda s: s.name)
def test_shipped_script_passes_offline(script):
    """The corpus is the regression suite. A finding here is a real behaviour change in the
    controller, the objection lexicon, or the filter — not a harness problem."""
    result = asyncio.run(run_script(script))
    assert result.ok, "\n".join(str(f) for f in result.findings) or result.halted


def test_the_gujarati_cost_objection_actually_reaches_p6():
    """Pinned separately from the generic pass because it is a named regression: the COST
    lexicon carried no money word in any script, so `ફી બહુ વધારે છે` classified as None
    and the one objection the flow exists to route to a visit never reached P6 (D3)."""
    script = next(s for s in _load_all() if s.name == "gujarati_cost_objection")
    result = asyncio.run(run_script(script))
    assert any(t.phase == "p6_objection" for t in result.turns)


def test_a_clean_lock_produces_a_real_datetime_not_just_a_flag():
    """`outcome == "locked"` is only meaningful if `locked_slot` holds an actual future
    visit time — the win condition is a specific day+time (docs/03), not a boolean."""
    script = next(s for s in _load_all() if s.name == "clean_lock")
    result = asyncio.run(run_script(script))
    assert result.win and result.outcome == "locked"
    locked = datetime.fromisoformat(result.slots["locked_slot"])
    assert locked > FROZEN_NOW
    assert locked - FROZEN_NOW < timedelta(days=2)
    assert 9 <= locked.hour < 18


def test_offline_run_makes_zero_network_calls():
    """The default mode must never reach OpenAI. `advance_turn` skips extraction when
    `client is None`, so the runner passes a sentinel that raises on ANY attribute access —
    which means this test fails loudly rather than silently spending money."""
    script = script_mod.loads(
        '{"name": "t"}\n'
        '{"lead": "haan", "expect_phase": "p2_discover"}\n'
        '{"lead": "job karta hoon", "slot": "job", "expect_phase": "p2_discover"}\n'
    )
    result = asyncio.run(run_script(script))
    assert result.ok
    assert result.slots["current_status"] == "job"


def test_the_offline_sentinel_raises_if_anything_touches_it():
    from roma.eval.runner import _NoClient

    with pytest.raises(AssertionError, match="zero network calls"):
        getattr(_NoClient(), "chat")  # noqa: B009 — attribute ACCESS is the thing under test


def test_repeat_runs_are_byte_identical():
    """`resolve_time_slot` refuses a slot in the past or outside visiting hours, so a
    wall-clock `now` would make the same fixture pass in the morning and fail at night. The
    runner freezes `now` for exactly that reason: a harness whose verdict depends on when it
    ran is not a harness."""
    script = next(s for s in _load_all() if s.name == "clean_lock")
    first = asyncio.run(run_script(script))
    second = asyncio.run(run_script(script))
    assert first.ok and second.ok
    assert first.slots == second.slots
    assert [t.phase for t in first.turns] == [t.phase for t in second.turns]


def test_now_is_injected_rather_than_read_from_the_clock():
    """The frozen default is a choice, not a hardcoding — a caller can move `now` and the
    booking moves with it. That is what will let a future live-transcript replay assert
    against the time the call actually happened."""
    script = next(s for s in _load_all() if s.name == "clean_lock")
    default = asyncio.run(run_script(script))
    shifted = asyncio.run(run_script(script, now=datetime(2027, 1, 1, 9, 30, tzinfo=IST)))
    assert shifted.outcome == "locked"
    assert datetime.fromisoformat(shifted.slots["locked_slot"]).year == 2027
    assert default.slots["locked_slot"] != shifted.slots["locked_slot"]


def test_a_budget_stop_is_reported_as_halted_not_as_a_pass():
    """Halting early must never look like success — this runs in CI, where a green exit is
    read as 'the checks passed'."""
    script = next(s for s in _load_all() if s.name == "clean_lock")
    meter = Meter(budget_inr=0.01)

    async def _broke_generate(state, m):
        m.record(Usage(input_tokens=2_000_000, output_tokens=2_000_000), "gpt-4o")
        return "never reached"

    result = asyncio.run(
        run_script(script, live=True, client=object(), meter=meter, generate=_broke_generate)
    )
    assert result.halted
    assert not result.ok
    assert result.findings == []


def test_budget_exceeded_carries_the_meter():
    meter = Meter(budget_inr=0.01)
    try:
        meter.record(Usage(input_tokens=2_000_000, output_tokens=2_000_000), "gpt-4o")
    except BudgetExceeded as exc:
        assert exc.meter is meter
        assert "₹" in str(exc)
    else:
        pytest.fail("expected BudgetExceeded")
