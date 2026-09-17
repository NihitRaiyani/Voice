"""Which course facts Roma has already spoken (docs/02 "every detail is said ONCE").

## Why this is machine state and not a prompt rule

`persona.md` already says it, twice and emphatically: "Every course detail is said ONCE in
the call... saying it again in fresh words is still saying it again, and the lead hears a
loop." On live call 049f0dc1 Roma said the three-module line in FOUR separate turns and
practical-not-theory in three, and the lead's complaint was exactly that.

The rule asks the model to audit its own earlier turns, which is the same thing the PATA HAI
line exists because it cannot reliably do (`state.py`, barge-in amnesia on CA9933275). A fact
that has been said is a fact about the CALL, so the machine should hold it and tell the model,
the same way it holds filled discovery slots.

Detection is deliberately coarse — an anchor token set per fact, matched on the normalized
tokenizer. A false positive costs one fact Roma could have said and did not; a false negative
is today's behaviour. That asymmetry is the right way round, because the failure being fixed
is saying too much, not too little.
"""

from roma.guardrails.normalize import tokens

# fact key -> the token sets that identify it. A fact matches when ANY of its token sets is
# fully present in the line. Multi-token sets, so "batch" alone never matches BATCH_SIZE.
FACT_ANCHORS: "dict[str, tuple[frozenset[str], ...]]" = {
    "modules": (
        frozenset({"teen", "module"}),
        frozenset({"seo", "ads"}),
        frozenset({"तीन", "मॉड्यूल"}),
    ),
    "practical": (
        frozenset({"practical", "theory"}),
        frozenset({"poora", "practical"}),
        frozenset({"प्रैक्टिकल"}),
    ),
    "faculty": (
        frozenset({"faculty", "working"}),
        frozenset({"faculty", "industry"}),
        frozenset({"फैकल्टी"}),
    ),
    "batch_size": (
        frozenset({"das", "baarah"}),
        frozenset({"batch", "log"}),
    ),
    "recording": (
        frozenset({"recording"}),
        frozenset({"रिकॉर्डिंग"}),
    ),
    "revision": (frozenset({"revision", "batch"}),),
    "exam": (
        frozenset({"exam"}),
        frozenset({"एग्जाम"}),
    ),
    "duration": (
        frozenset({"chaar", "mahine"}),
        frozenset({"basic", "advance"}),
    ),
    "placement": (
        frozenset({"placement", "support"}),
        frozenset({"interview", "preparation"}),
    ),
    "ai_tools": (frozenset({"ai", "tools"}),),
    "live_project": (frozenset({"live", "project"}),),
    "certificate": (frozenset({"weltec", "certificate"}),),
}

# What the model is told, per fact. Short: this line is billed on every turn of the phases
# that carry it, and it is a reminder, not an explanation.
FACT_LABELS = {
    "modules": "teen module",
    "practical": "practical-not-theory",
    "faculty": "faculty",
    "batch_size": "batch size",
    "recording": "recordings",
    "revision": "revision batch",
    "exam": "monthly exam",
    "duration": "duration",
    "placement": "placement support",
    "ai_tools": "AI tools",
    "live_project": "live project",
    "certificate": "certificate",
}

ALREADY_SAID_PREFIX = "PEHLE HI BATA CHUKI HAIN (dobara mat kehna): "


def facts_in(line: str) -> "set[str]":
    """Every fact key `line` states. Never raises — a detector must not cost a turn."""
    try:
        toks = set(tokens(line))
    except Exception:  # noqa: BLE001
        return set()
    if not toks:
        return set()
    return {key for key, variants in FACT_ANCHORS.items() if any(v <= toks for v in variants)}


def already_said_line(said: "list[str] | None") -> str:
    """The prompt line naming what is spent, or "" when nothing is.

    Empty string rather than "nothing yet": an empty line renders as a blank the fragment
    absorbs, while a sentence saying nothing has been said is tokens spent to tell the model
    something it can already see.
    """
    if not said:
        return ""
    labels = [FACT_LABELS[k] for k in said if k in FACT_LABELS]
    if not labels:
        return ""
    return ALREADY_SAID_PREFIX + ", ".join(labels) + "."


__all__ = [
    "FACT_ANCHORS",
    "FACT_LABELS",
    "ALREADY_SAID_PREFIX",
    "facts_in",
    "already_said_line",
]
