"""The four Step-7 assertions, and the proof that they can actually fail.

The last test in this file is the one that matters most. A harness that reports green
against a KNOWN-BROKEN filter proves nothing, and this repo has already lived through
exactly that: the pre-TTS filter failed open on every Indic line for an entire build phase
while the logs said `allow:clean`. So the canaries are tested by breaking the filter the
way it was actually broken and requiring the harness to go red.
"""

import re
from dataclasses import replace

import pytest
from roma.eval import checks
from roma.eval.checks import (
    Finding,
    ScriptResult,
    TurnResult,
    check_canaries,
    check_filter,
    check_no_phantom_confirmation,
    check_offer_recorded,
    check_phase_hits,
    check_slots,
    check_word_caps,
)


def _result(turns, **kw):
    return ScriptResult(name="t", turns=turns, **kw)


def test_phase_hit_mismatch_is_reported():
    r = _result([TurnResult(1, "haan", "", "p1_open", expect_phase="p2_discover")])
    (f,) = check_phase_hits(r)
    assert "expected p2_discover" in f.detail and "landed p1_open" in f.detail


def test_phase_with_no_expectation_is_not_checked():
    r = _result([TurnResult(1, "haan", "", "p1_open")])
    assert check_phase_hits(r) == []


def test_word_cap_overrun_is_reported_with_the_count():
    long_line = " ".join(["word"] * 40)
    r = _result([TurnResult(1, "haan", long_line, "p2_discover")])
    (f,) = check_word_caps(r)
    assert "40 words over cap 20" in f.detail


def test_word_cap_uses_the_phase_the_turn_landed_in():
    """The line Roma speaks after a turn belongs to the phase the machine moved INTO —
    that is the fragment and the max_tokens that produced it."""
    line = " ".join(["word"] * 40)
    assert check_word_caps(_result([TurnResult(1, "x", line, "p3_value")])) == []
    assert check_word_caps(_result([TurnResult(1, "x", line, "p7_close")]))


def test_empty_roma_line_is_skipped_not_flagged():
    assert check_word_caps(_result([TurnResult(1, "haan", "", "p2_discover")])) == []


def test_missing_slot_is_reported():
    r = _result([], slots={"city": None})
    (f,) = check_slots(r, {"city": "Vadodara"}, "", None)
    assert "expected 'Vadodara'" in f.detail


def test_slot_expected_to_stay_empty():
    """The evasive script asserts slots are NOT filled — a null expectation is a real
    assertion, not a 'don't care'."""
    assert check_slots(_result([], slots={"city": None}), {"city": None}, "", None) == []
    (f,) = check_slots(_result([], slots={"city": "X"}), {"city": None}, "", None)
    assert "should be unfilled" in f.detail


def test_outcome_and_win_are_checked_separately():
    """A soft 'dekhta hoon' is not a win (CLAUDE.md). The outcome and the win flag are
    asserted independently so a fixture cannot imply one from the other."""
    r = _result([], outcome="no_lock", win=False)
    assert len(check_slots(r, {}, "locked", None)) == 1
    assert len(check_slots(r, {}, "", True)) == 1
    assert check_slots(r, {}, "no_lock", False) == []


def test_a_roma_line_that_would_be_substituted_is_reported():
    r = _result([TurnResult(1, "kitni fees hai", "Fees ₹25000 hai.", "p6_objection")])
    (f,) = check_filter(r)
    assert f.check == "filter-leak"
    assert "would be substituted" in f.detail


def test_clean_roma_lines_produce_nothing():
    r = _result([TurnResult(1, "haan", "Aap rehte kahan ho ji?", "p2_discover")])
    assert check_filter(r) == []


def test_canaries_pass_against_the_real_filter():
    assert check_canaries() == []


def test_canary_set_covers_both_scripts_and_every_category():
    """Romanized-only canaries would have been green for the entire period the filter was
    broken, because `\\w+` tokenized Latin text fine. Indic coverage is the point."""
    lines = [line for line, _ in checks.CANARIES]
    assert any(any("ऀ" <= ch <= "ॿ" for ch in ln) for ln in lines), "no Devanagari"
    assert any(any("઀" <= ch <= "૿" for ch in ln) for ln in lines), "no Gujarati"
    assert {cat for _, cat in checks.CANARIES} == set(checks.BlockCategory)


def test_the_allow_case_is_not_over_blocked():
    """docs/04 has exactly one allow-case. A filter that substitutes over a rebuttal makes
    Roma concede the number she was refusing, so over-blocking is a failure too."""
    from roma.domain.safety.filter import screen

    for line in checks.ALLOWED_LINES:
        assert screen(line).allowed, line


def test_canaries_go_RED_when_the_tokenizer_is_broken(monkeypatch):
    """THE test that makes this harness worth running.

    Restore the exact defect the guardrail fix repaired — `_tokens` as a private
    `re.compile(r"[₹%]|\\w+")` — and require the canaries to catch it. Python's `\\w`
    excludes Indic combining marks, so this shatters `फीस` into ['फ', 'स'] and the Indic
    lexicon entries become unreachable while `screen()` cheerfully returns `allow:clean`.

    If this test ever passes with an EMPTY finding list, the harness has stopped being able
    to detect a filter that fails open, and every green run above it means nothing.
    """
    broken = re.compile(r"[₹%]|\w+")
    monkeypatch.setattr("roma.domain.safety.filter._tokens", lambda text: broken.findall(text))

    findings = check_canaries()
    assert findings, "harness stayed green against a filter that fails open on Indic text"

    leaked = [f for f in findings if "LEAKED" in f.detail]
    assert leaked, "expected the Indic canaries to be reported as leaks"
    assert any("फीस" in f.detail for f in leaked)


def test_finding_str_is_readable():
    assert str(Finding("filter-canary", "FEE", "LEAKED")) == "[filter-canary] FEE: LEAKED"


@pytest.mark.parametrize("line,_cat", checks.CANARIES)
def test_each_canary_individually(line, _cat):
    """Parametrized so a regression names the exact line that started leaking."""
    from roma.domain.safety.filter import screen

    assert not screen(line).allowed, line


def test_a_confirmation_over_a_refused_slot_is_reported():
    """THE regression, in one assertion."""
    r = _result(
        [
            TurnResult(
                1,
                "kal subah chhe baje aa jaunga",
                "Aapki visit confirm ho gayi kal subah chhe baje. Milte hain!",
                "p5_pivot",
                slot_status="out_of_hours",
            )
        ]
    )
    findings = check_no_phantom_confirmation(r)
    assert len(findings) == 1
    assert findings[0].check == "phantom-confirmation"
    assert "out_of_hours" in findings[0].detail


@pytest.mark.parametrize("status", ["out_of_hours", "in_past", "unclear"])
def test_every_refusal_status_is_guarded_not_just_out_of_hours(status):
    r = _result([TurnResult(1, "x", "Visit fix kar diya ji.", "p5_pivot", slot_status=status)])
    assert check_no_phantom_confirmation(r)


def test_a_polite_refusal_that_re_offers_is_clean():
    """The line Roma SHOULD say must not trip the check, or the harness teaches the prompt
    to avoid the word rather than to avoid the phantom booking."""
    r = _result(
        [
            TurnResult(
                1,
                "kal subah chhe baje",
                "Us waqt branch band hoti hai ji — subah nau baje ya gyaarah baje?",
                "p5_pivot",
                slot_status="out_of_hours",
            )
        ]
    )
    assert check_no_phantom_confirmation(r) == []


def test_confirming_an_ACCEPTED_slot_is_exactly_what_should_happen():
    r = _result(
        [
            TurnResult(
                1,
                "haan",
                "Theek hai ji, kal shaam paanch baje confirm kar rahi hoon.",
                "p7_close",
                slot_status="accepted",
            )
        ]
    )
    assert check_no_phantom_confirmation(r) == []


def test_the_cue_match_is_on_tokens_not_substrings():
    """`confirm` must not fire inside an unrelated word, the same token-boundary rule the
    pre-TTS filter follows (docs/04)."""
    r = _result(
        [
            TurnResult(
                1, "x", "Aapka reconfirmation nahi chahiye.", "p5_pivot", slot_status="unclear"
            )
        ]
    )
    assert check_no_phantom_confirmation(r) == []


def test_gujarati_confirmation_is_caught_too():
    """STT runs gu-IN (D3); a romanized-only cue set would be blind on live calls."""
    r = _result([TurnResult(1, "x", "Visit કન્ફર્મ છે.", "p5_pivot", slot_status="out_of_hours")])
    assert check_no_phantom_confirmation(r)


def test_the_shipped_refused_slot_fixture_goes_RED_if_roma_confirms():
    """The fixture passes today. Prove that is because Roma's lines are honest, not because
    the check cannot fail — the same standard the canaries are held to above."""
    import asyncio
    from pathlib import Path

    from roma.eval.runner import run_script
    from roma.eval.script import load_dir

    scripts_dir = Path(__file__).resolve().parents[2] / "evals" / "scripts"
    script = next(s for s in load_dir(scripts_dir) if s.name == "refused_slot")
    assert asyncio.run(run_script(script)).ok

    turns = list(script.turns)
    for i, t in enumerate(turns):
        if t.accept:
            turns[i] = replace(t, roma="Aapki visit confirm ho gayi kal subah chhe baje.")
    broken = replace(script, turns=tuple(turns))

    result = asyncio.run(run_script(broken))
    assert not result.ok
    assert any(f.check == "phantom-confirmation" for f in result.findings)


def test_reaching_p5_without_recorded_offers_is_a_finding():
    """`state.slots_offered` was declared, checkpointed and rehydrated for four build steps
    while never being written by production code — and every script passed the whole time,
    because no check asserted the mechanism was connected at all."""
    r = _result([TurnResult(1, "haan", "Monday ya Tuesday?", "p5_pivot")])
    r.slots = {"slots_offered": []}
    findings = check_offer_recorded(r)
    assert findings and findings[0].check == "offer-recorded"


def test_recorded_offers_pass():
    r = _result([TurnResult(1, "haan", "...", "p5_pivot")])
    r.slots = {"slots_offered": ["2026-07-28T11:00:00+05:30"]}
    assert check_offer_recorded(r) == []


def test_a_call_that_never_reached_p5_is_not_expected_to_have_offers():
    """A lead who hangs up in discovery was never offered anything, and that is correct."""
    r = _result([TurnResult(1, "kaun", "...", "p1_open")])
    r.slots = {"slots_offered": []}
    assert check_offer_recorded(r) == []
