"""Drive a scripted call through the real controller and collect what happened.

The machinery half of the harness; `checks.py` holds the contract. This module owns one
question: how does a `Script` become a `ScriptResult`?

It calls the REAL `advance_turn` — real machine, real objection classifier, real
`resolve_time_slot`, real `CallState`. Only the two model-backed extractors are swapped,
because they are the only pieces that would otherwise need a network. That boundary is not
a convenience: `advance_turn` already takes `extract_discovery`/`extract_time`/`classify`
as parameters (Step 4 built it that way), so the harness injects rather than mocks, and
nothing here reaches into a private.

The pattern generalises `tests/telephony/test_media_phase_e2e.py`, which already drives a
scripted P1→P7 call offline. That test keeps its own copy on purpose — a test that depends
on the harness it is meant to backstop is not a backstop.

Two modes:

* **offline** (default, ₹0) — stubs answer from the fixture, Roma's line is the fixture's
  reference text, `now` is frozen. Validates the controller and the filter.
* **live** (`--live`) — real extractors and real generation, metered against
  `Settings.openai_budget_inr`. The only mode that says anything about the model's own
  output shape, and the only one that costs money.
"""

import logging
from datetime import datetime

from roma.domain.appointments.slots import DiscoveryValue, TimeSlot
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.state import CallState
from roma.domain.conversation.turn import advance_turn
from roma.eval.checks import ScriptResult, TurnResult, run_checks
from roma.eval.cost import BudgetExceeded, Meter
from roma.workers.postcall.job import outcome_for

_log = logging.getLogger("roma.eval")

FROZEN_NOW = datetime(2026, 7, 25, 10, 0, tzinfo=IST)

STUB_CONFIDENCE = 0.95


class _NoClient:
    """Offline sentinel. Any attribute access is a bug: something tried to reach OpenAI."""

    def __getattr__(self, name):
        raise AssertionError(
            f"offline eval run tried to use the OpenAI client (.{name}) — "
            "offline mode must make zero network calls"
        )


def _stub_extractors(turn):
    """Deterministic extractors for one turn, driven by the fixture's own fields."""

    async def extract_discovery(client, text, slot_name):
        if turn.slot is None:
            return DiscoveryValue()
        return DiscoveryValue(value=turn.slot, confidence=STUB_CONFIDENCE, raw=text)

    async def extract_time(client, text, *, offered=None):
        if turn.chose_offer is not None:
            return TimeSlot(
                accepted=True, chose_offer=turn.chose_offer, confidence=STUB_CONFIDENCE
            )
        if turn.accept:
            return TimeSlot(
                accepted=True,
                day_offset=1,
                hour=turn.accept_hour if turn.accept_hour is not None else 5,
                period=turn.accept_period or "evening",
                confidence=STUB_CONFIDENCE,
            )
        if turn.confirm:
            return TimeSlot(readback_confirmed=True, confidence=STUB_CONFIDENCE)
        return TimeSlot(confidence=0.0)

    return extract_discovery, extract_time


async def run_script(
    script,
    *,
    live: bool = False,
    client=None,
    meter: "Meter | None" = None,
    now: "datetime | None" = None,
    generate=None,
) -> ScriptResult:
    """Replay one script. Never raises for a failed expectation — those become findings.

    A budget stop is NOT a finding: it means the run did not finish, so the result is
    marked `halted` and reported as incomplete rather than as a pass.
    """
    now = now or FROZEN_NOW
    state = CallState(
        call_sid=f"eval-{script.name}",
        lead_name=script.lead_name,
        branch=script.branch,
    )
    result = ScriptResult(name=script.name, live=live)

    for i, turn in enumerate(script.turns, start=1):
        if live:
            extract_discovery, extract_time = None, None
            turn_client = client
        else:
            extract_discovery, extract_time = _stub_extractors(turn)
            turn_client = client if client is not None else _NoClient()

        kwargs = {"client": turn_client, "now": now, "elapsed_secs": turn.elapsed}
        if extract_discovery is not None:
            kwargs["extract_discovery"] = extract_discovery
            kwargs["extract_time"] = extract_time

        try:
            transition = await advance_turn(state, turn.lead, **kwargs)
        except BudgetExceeded as exc:
            result.halted = str(exc)
            result.cost_inr = exc.meter.spent_inr
            break

        roma_line = turn.roma
        if live and generate is not None:
            try:
                roma_line = await generate(state, meter)
            except BudgetExceeded as exc:
                result.halted = str(exc)
                result.cost_inr = exc.meter.spent_inr
                break

        result.turns.append(
            TurnResult(
                index=i,
                lead=turn.lead,
                roma=roma_line,
                phase=state.phase,
                expect_phase=turn.expect_phase,
                win=transition.win,
                slot_status=state.slot_status,
            )
        )
        if transition.win:
            result.win = True

    result.slots = {
        # lead_name is a discovery slot like any other now that Roma is inbound. It was
        # missing here, so a fixture asserting it compared against a key the harness never
        # reported and silently read as None — the check looked green-adjacent and tested
        # nothing. Any slot in DISCOVERY_ORDER must appear in this dict.
        "lead_name": state.lead_name,
        "education": state.education,
        "passing_year": state.passing_year,
        "current_status": state.current_status,
        "city": state.city,
        "timing_constraint": state.timing_constraint,
        "accepted_slot": state.accepted_slot,
        "locked_slot": state.locked_slot,
        "slots_offered": list(state.slots_offered),
    }
    result.outcome = outcome_for(state)
    if meter is not None:
        result.cost_inr = meter.spent_inr
    if not result.halted:
        result.findings = run_checks(result, script)
    return result


__all__ = ["run_script", "FROZEN_NOW", "STUB_CONFIDENCE"]
