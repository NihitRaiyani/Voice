"""The off-topic deflection bank (controller.offtopic).

Two failure modes bound this design, and the tests hold both edges:

  * UNDER-deflect and the model rambles about cricket on a five-minute budget call;
  * OVER-deflect and Roma stonewalls real answers — "beti ki shaadi hai" is a timing
    constraint, not chit-chat, and deflecting it torches the discovery phase.

Every line the bank can speak must also survive the systems below it: `safe_output`
(nothing here may trip a guarded category) and `is_premature_time_talk` (a deflection with
a time word and a question mark would be rewritten mid-flight into a course question).
"""

from roma.controller.offtopic import (
    _SLOT_QUESTIONS,
    CONVERGE_AFTER,
    CONVERGE_FALLBACK,
    CONVERGE_PREFIX,
    DEFLECTIONS,
    OffTopic,
    classify_off_topic,
    deflection_for,
)
from roma.controller.state import DISCOVERY_ORDER, CallState

# --- the classifier ------------------------------------------------------------------------


def test_the_classifier_recognises_each_category():
    cases = {
        "Aaj match dekha aapne? IPL chal raha hai na": OffTopic.CHITCHAT,
        "Mausam bahut kharab hai aaj baarish hogi kya": OffTopic.CHITCHAT,
        "Aapki shaadi hui hai kya?": OffTopic.PERSONAL,
        "Aapki umar kya hai?": OffTopic.PERSONAL,
        "Ek gaana sunao na": OffTopic.RANDOM,
        "Khana khaya aapne?": OffTopic.RANDOM,
        "I love you yaar": OffTopic.INAPPROPRIATE,
        "Mujhse shaadi karogi?": OffTopic.INAPPROPRIATE,
    }
    for text, expected in cases.items():
        assert classify_off_topic(text) is expected, text


def test_on_topic_and_trap_utterances_are_never_deflected():
    """Each of these is a REAL turn the machine must handle itself. The possessive gate is
    what keeps "beti ki shaadi" (a timing constraint) out of PERSONAL, and identity
    questions have an honest answer required by the hard rules."""
    for text in (
        "Course kab shuru hoga?",
        "Fees kitni hai?",
        "Meri beti ki shaadi hai next week, time nahi milega",
        "Aap robot ho kya?",
        "Kahan se bol rahi hain aap?",
        "haan ji theek hai",
        "",
        None,
    ):
        assert classify_off_topic(text) is None, text


def test_a_proposal_is_inappropriate_not_a_personal_question():
    """ "Shaadi karogi" is a proposal at Roma, not a question about her marriage — the reply
    has a different register, so the category must not fall through to PERSONAL."""
    assert classify_off_topic("mujhse shaadi karogi") is OffTopic.INAPPROPRIATE


# --- rotation and convergence --------------------------------------------------------------


def test_two_drifts_get_two_different_lines_and_the_third_converges():
    state = CallState(call_sid="CA_ot", phase="p2_discover")
    first = deflection_for(state, "match dekha kya aapne")
    second = deflection_for(state, "mausam accha hai na aaj")
    third = deflection_for(state, "cricket khelte ho kya")

    assert first and second and third
    assert first != second, "a chatty lead must never hear the same deflection twice"
    assert state.off_topic_turns == 3
    assert third.startswith(CONVERGE_PREFIX) or third == CONVERGE_FALLBACK
    assert third != first and third != second


def test_the_converge_line_re_asks_the_pending_discovery_question():
    state = CallState(
        call_sid="CA_conv", phase="p2_discover", off_topic_turns=CONVERGE_AFTER - 1
    )
    line = deflection_for(state, "ek joke sunao na")
    assert line is not None
    assert line.startswith(CONVERGE_PREFIX)
    assert _SLOT_QUESTIONS["lead_name"] in line, (
        "nothing is filled yet, so the converge line must return to the FIRST slot"
    )


def test_after_convergence_every_drift_gets_the_same_calm_wall():
    state = CallState(call_sid="CA_wall", phase="p2_discover", off_topic_turns=CONVERGE_AFTER)
    a = deflection_for(state, "gaana sunao")
    b = deflection_for(state, "match dekha")
    assert a == b, "past the converge point there is one line, not a fresh bank"


def test_an_ordinary_turn_never_touches_the_counter():
    state = CallState(call_sid="CA_plain", phase="p2_discover")
    assert deflection_for(state, "BCom kiya hai maine 2024 mein") is None
    assert state.off_topic_turns == 0


def test_every_discovery_slot_has_a_converge_question():
    assert set(_SLOT_QUESTIONS) >= set(DISCOVERY_ORDER)


# --- every line survives the systems below it ----------------------------------------------


def _every_line():
    yield from (line for bank in DEFLECTIONS.values() for line in bank)
    yield CONVERGE_FALLBACK
    yield from (CONVERGE_PREFIX + q for q in _SLOT_QUESTIONS.values())


def test_every_deflection_survives_its_own_filter():
    """Same pin as the SUBSTITUTIONS table: a safe line the filter would rewrite is a
    config bug caught at test time, not mid-call."""
    from roma.guardrails import safe_output

    for line in _every_line():
        assert safe_output(line) == line, line


def test_no_deflection_trips_the_premature_time_talk_guard():
    """A deflection plays in NON-offer phases by construction, where `safe_time_talk`
    rewrites any "?" sentence carrying a time cue into a course question — which would
    silently replace the deflection. No line may qualify."""
    from roma.controller.confirmguard import is_premature_time_talk

    for line in _every_line():
        for sentence in line.replace("—", ".").split("."):
            assert not is_premature_time_talk(sentence, phase="p2_discover"), (line, sentence)


def test_the_counter_survives_a_checkpoint_round_trip():
    state = CallState(call_sid="CA_rt", off_topic_turns=2)
    assert CallState.from_dict(state.to_dict()).off_topic_turns == 2, (
        "a reconnecting drifter must not get a fresh deflection bank"
    )
