"""Off-topic turns: a polite, varied redirect instead of one stiff line or an LLM ramble.

Live calls wander — cricket, "aapki shaadi hui hai?", "gaana sunao". Before this module
those turns went to the model, which either engaged (off-script, unbounded) or produced a
stiff one-size refusal every time. The turn's ANSWER does not depend on what was asked —
"we don't discuss that, back to the course" — which is exactly the short-circuit rule
(`shortcircuit`), so these turns skip the completion too and rotate through a small bank so
a chatty lead never hears the same deflection twice in one call.

## What a lexicon can and cannot catch — stated honestly

A lexicon recognises COMMON off-topic shapes: small talk, personal probes at Roma,
song/joke bait, mild flirtation. It cannot recognise arbitrary topics; anything it misses
still reaches the model, whose fragments and hard rules carry the fallback redirect. This
module narrows the funnel, it is not the fence.

## What must NOT be deflected — the false-positive traps, each one deliberate

* "Beti ki shaadi hai next week" — a real timing constraint mid-discovery. The PERSONAL
  cues are therefore possessive-gated ("aapki shaadi", never bare "shaadi").
* "Aap robot ho?" / "kahan se bol rahi ho?" — hard rule: Roma answers both honestly (AI
  assistant; Vadodara branch). Deflecting an identity question is a compliance failure, so
  no identity cue appears below.
* Guarded topics (fees/salary/placement/cert) and objections have their own routes that run
  earlier or carry real answers; their vocabularies are intentionally disjoint from these.

Runs AFTER `canned_reply` in `phase_controller._maybe_short_circuit`, so a pending readback
or sign-off always outranks a deflection. Every line here survives its own `safe_output`
(pinned in tests) and, like every short-circuit, still passes through `pretts` on the way
to Bulbul.
"""

import logging
from enum import Enum

from roma.guardrails.normalize import tokens

_log = logging.getLogger("roma.controller")

# The third off-topic turn stops being charming. One warm line, then back to the script.
CONVERGE_AFTER = 3


class OffTopic(Enum):
    INAPPROPRIATE = "inappropriate"
    PERSONAL = "personal"
    CHITCHAT = "chitchat"
    RANDOM = "random"


# Contiguous-token phrases (the `objection._has_phrase` shape) and bare keywords.
# Possessives gate the PERSONAL phrases — see the module docstring's false-positive traps.
_PHRASES: dict[OffTopic, list[list[str]]] = {
    OffTopic.INAPPROPRIATE: [
        ["i", "love", "you"],
        ["love", "you"],
        ["date", "pe"],
        ["shaadi", "karogi"],
        ["shadi", "karogi"],
        ["शादी", "करोगी"],
    ],
    OffTopic.PERSONAL: [
        ["aapki", "shaadi"],
        ["tumhari", "shaadi"],
        ["teri", "shaadi"],
        ["aapki", "umar"],
        ["aapki", "umr"],
        ["aapki", "age"],
        ["tumhari", "umar"],
        ["aap", "single"],
        ["आपकी", "शादी"],
        ["आपकी", "उम्र"],
        ["તમારી", "ઉંમર"],
        ["તમારા", "લગ્ન"],
    ],
    OffTopic.CHITCHAT: [],
    OffTopic.RANDOM: [
        ["gaana", "sunao"],
        ["gana", "sunao"],
        ["joke", "sunao"],
        ["kavita", "sunao"],
        ["khana", "khaya"],
        ["khana", "banaya"],
        ["गाना", "सुनाओ"],
        ["खाना", "खाया"],
    ],
}

_KEYWORDS: dict[OffTopic, frozenset] = {
    OffTopic.INAPPROPRIATE: frozenset({"sexy", "pyaar", "pyar", "प्यार"}),
    OffTopic.PERSONAL: frozenset({"girlfriend", "boyfriend"}),
    OffTopic.CHITCHAT: frozenset(
        {
            "cricket",
            "match",
            "ipl",
            "mausam",
            "baarish",
            "weather",
            "movie",
            "film",
            "picture",
            "election",
            "क्रिकेट",
            "मैच",
            "मौसम",
            "बारिश",
            "फिल्म",
            "ક્રિકેટ",
            "મેચ",
            "વરસાદ",
            "ફિલ્મ",
        }
    ),
    OffTopic.RANDOM: frozenset(),
}

# INAPPROPRIATE first: "shaadi karogi" must not be read as a PERSONAL question about
# Roma's marriage — it is a proposal, and the reply has a different register.
_CHECK_ORDER = (OffTopic.INAPPROPRIATE, OffTopic.PERSONAL, OffTopic.CHITCHAT, OffTopic.RANDOM)

# Two lines per category; with CONVERGE_AFTER = 3 at most two ever play, so rotation by
# count means a repeat is impossible within one call. Every line redirects and hands the
# turn back; none contains a time word (the `is_premature_time_talk` trigger is "?" plus a
# time cue in one sentence) and none touches a guarded topic.
DEFLECTIONS: dict[OffTopic, list[str]] = {
    OffTopic.INAPPROPRIATE: [
        "Ji, hum bas course ki baat karenge. Aapko course mein interest hai toh main aage batati hoon.",
        "Ye baat theek nahi ji. Main Weltec ke course ke liye call kar rahi hoon — wahi baat karte hain.",
    ],
    OffTopic.PERSONAL: [
        "Ye toh main nahi bataungi ji — main yahan sirf course ki baat ke liye hoon. Chaliye, hum kahan the?",
        "Wo personal sawaal ho gaya ji, uska jawab main nahi de sakti. Course pe wapas aate hain?",
    ],
    OffTopic.CHITCHAT: [
        "Wo toh pata nahi ji — main sirf Weltec ke course ki baat kar sakti hoon. Chaliye, wahi poori kar lete hain?",
        "Uska toh idea nahi ji. Main Weltec se course ke liye baat kar rahi hoon — wahi continue karein?",
    ],
    OffTopic.RANDOM: [
        "Haha, ye toh main nahi kar paungi ji. Chaliye, apni course wali baat poori kar lete hain?",
        "Achha ji! Par pehle apni baat poori kar lein — main course ke baare mein bata rahi thi.",
    ],
}

# The question Roma returns to after converging. Mirrors the wording in
# `prompts/phases/p2_discover.md` — the fragment is the source of the phrasing; if it
# changes there, change it here (the register lint in tests/llm covers both).
_SLOT_QUESTIONS = {
    "lead_name": "aapka naam kya hai?",
    "current_status": "abhi aap padh rahe hain, koi course kar rahe hain, ya job kar rahe hain?",
    "education": "aapne padhai kya ki hai?",
    "passing_year": "kis saal complete hua?",
    "city": "aap rehte kahan hain?",
    "timing_constraint": "aapko din mein kaunsa hissa zyada suit karta hai?",
}

CONVERGE_PREFIX = "Koi baat nahi ji, wo sab chhodiye. Bas ek cheez bata dijiye — "
CONVERGE_FALLBACK = (
    "Koi baat nahi ji. Chaliye, apni course wali baat poori kar lete hain — "
    "main jo pooch rahi thi, wo bata dijiye?"
)


def _has_phrase(toks: list, phrase: list) -> bool:
    n = len(phrase)
    return any(toks[i : i + n] == phrase for i in range(len(toks) - n + 1))


def classify_off_topic(text) -> "OffTopic | None":
    """The off-topic category of this utterance, or None. Never raises."""
    try:
        if not text:
            return None
        toks = tokens(text)
        if not toks:
            return None
        token_set = set(toks)
        for cat in _CHECK_ORDER:
            if token_set & _KEYWORDS.get(cat, frozenset()):
                return cat
            if any(_has_phrase(toks, p) for p in _PHRASES.get(cat, [])):
                return cat
        return None
    except Exception:  # noqa: BLE001 — a classifier failure is "not off-topic", never a crash
        return None


def deflection_for(state, user_text: str) -> "str | None":
    """The deflection line for this turn, or None to let the model have it.

    Mutates `state.off_topic_turns` when it fires — the count is what drives both the
    rotation (no repeat within a call) and the convergence (at `CONVERGE_AFTER`, one warm
    line that re-asks the pending discovery question, and the same line for every off-topic
    turn after that; a lead who keeps drifting gets the same calm wall each time).
    """
    try:
        category = classify_off_topic(user_text)
        if category is None:
            return None
        state.off_topic_turns += 1
        if state.off_topic_turns >= CONVERGE_AFTER:
            _log.info("off-topic #%d (%s): converging", state.off_topic_turns, category.value)
            slot = state.next_discovery_slot()
            question = _SLOT_QUESTIONS.get(slot) if slot else None
            return CONVERGE_PREFIX + question if question else CONVERGE_FALLBACK
        bank = DEFLECTIONS[category]
        line = bank[(state.off_topic_turns - 1) % len(bank)]
        _log.info("off-topic #%d (%s): deflecting", state.off_topic_turns, category.value)
        return line
    except Exception:  # noqa: BLE001 — same posture as canned_reply: fall through to the model
        _log.warning("off-topic router failed; forwarding the turn to the LLM")
        return None


__all__ = [
    "OffTopic",
    "classify_off_topic",
    "deflection_for",
    "DEFLECTIONS",
    "CONVERGE_AFTER",
    "CONVERGE_PREFIX",
    "CONVERGE_FALLBACK",
]
