"""Roma may not announce a booking the machine did not make.

## Why this is code and not a prompt rule

It already IS a prompt rule. `hard_rules.md` rule 9 says it in as many words, every phase
fragment carries a `SLOT STATUS` line saying what the controller decided, and
`state.slot_status` spells the refusal out as an imperative Hinglish sentence. On live call
CA1bf16a (2026-07-26) the prompt said:

    SLOT STATUS: lead ne jo time bola wo clear nahi tha. Confirm kuch mat karo —
    dobara poochho ya do slots offer karo.

and Roma said:

    "Tuesday subah gyaarah baje ko aap aa rahi ho, ye confirm ho gaya."

Teardown: `phase=p5_pivot won=False`. No booking exists. That is the third call in a row
with this failure and the second since the status line was added, so the conclusion is
forced: on gpt-4o-mini this instruction does not hold, and a rule that does not hold is not
a control. docs/03 calls a lead who leaves believing in a visit that was never booked the
worst outcome of the call — worse than no booking at all — which makes this the one place
worth spending a hard gate.

## The shape (docs/04's, deliberately)

Router, not censor: on a catch the line is REPLACED with a safe one, never deleted into
dead air. Same contract as the pre-TTS filter, same reason.

## The rule

A confirmation cue is permitted only when the machine says `accepted` or `locked`. Not
"unless it refused" — `none` is a refusal too, and so is any status this module has not
heard of. Roma confirms what the controller booked, or she confirms nothing.

False positives are accepted, knowingly. "Aapki visit fix karna chahungi" is an intention,
not a claim, and it will be substituted when nothing is accepted yet. The replacement is a
warm question that moves towards the booking, so the cost is one slightly redundant turn;
the cost of a miss is a lead who drives to a branch that is not expecting them.
"""

import logging

from roma.domain.safety.normalize import tokens

_log = logging.getLogger("roma.domain.conversation")
# this all are used to extract entities and gets the confitmation by seeing the word from list and confirmation detected
CONFIRMATION_CUES = frozenset(
    {
        "confirm",
        "confirmed",
        "confirmation",
        "fix",
        "fixed",
        "lock",
        "locked",
        "book",
        "booked",
        "booking",
        "pakka",
        "pakki",
        "पक्का",
        "पक्की",
        "फिक्स",
        "कन्फर्म",
        "કન્ફર્મ",
        "ફિક્સ",
        "પાક્કુ",
        "પાક્કી",
    }
)

CONFIRMATION_PHRASES = (
    ("tay", "hai"),
    ("tay", "ho", "gaya"),
    ("tay", "ho", "gayi"),
    ("tay", "ho", "chuka"),
    ("tay", "ho", "chuki"),
    ("tay", "kar", "diya"),
    ("tay", "kar", "diyi"),
    ("तय", "है"),
    ("तय", "हो", "गया"),
    ("तय", "हो", "गयी"),
    ("तय", "हो", "चुका"),
    ("तय", "कर", "दिया"),
    ("તય", "છે"),
    ("નક્કી", "છે"),
    ("નક્કી", "થઈ", "ગયું"),
)

CONFIRMABLE_STATUSES = frozenset({"accepted", "locked"})

REFUSAL_STATUSES = frozenset({"out_of_hours", "in_past", "unclear", "day_only"})

# No "Ek second ji" opener. That phrase has now been reported twice by the lead, and this
# was the third place it could still come out of — after the pacer clip and SAFE_HOLD_LINE.
SAFE_REOFFER_LINE = (
    "Abhi wo time final nahi hua hai. Aapko subah ka time theek rahega ya shaam ka?"
)

# In P5/P7 the OFFER line is right there and Roma is about to name two concrete times, so
# the subah/shaam question above becomes a SECOND, competing time question in the same turn.
# Call c9be521d, one turn, verbatim:
#
#   "Abhi wo time final nahi hua hai. Aapko subah ka time theek rahega ya shaam ka?
#    Monday, 3 August ko shaam 5 baje ya Tuesday, 4 August ko subah 11 baje ...  Kaunsa?"
#
# The lead's reply: "यहां पे थोड़ा loop का error है, आपने time के बारे में बार बार बोला है".
# A statement here, so the turn keeps moving on HER question rather than racing it. Not dead
# air, which is the only thing docs/04 forbids a substitution from producing.
SAFE_REOFFER_LINE_IN_OFFER = "Abhi wo time final nahi hua hai."


def reoffer_line(phase: str = "") -> str:
    """The substitution for a phantom confirmation, given where the call is."""
    return SAFE_REOFFER_LINE_IN_OFFER if phase in OFFER_PHASES else SAFE_REOFFER_LINE


# Roma telling the lead a time is unavailable. The exact mirror of a phantom confirmation:
# there, she books what the machine did not; here, she refuses what the machine never
# refused. Hard rule 8 already forbids it in words — "koi fixed slot nahi hai", "'wo slot
# available nahi hai' kabhi mat bolo" — and on call bde258d1 she said, in one call:
#
#   "shaam 4 baje ka timing unfortunately available nahi hai"   (4pm is inside 10-18)
#   "Saturday ka slot nahi hai"
#
# and then booked the lead at Friday 4pm. A refusal she invents costs the booking the whole
# call exists to make, so it gets a gate rather than another sentence of prompt.
DENIAL_PHRASES = (
    ("available", "nahi"),
    ("available", "नहीं"),
    ("slot", "nahi"),
    ("slot", "नहीं"),
    ("slot", "નથી"),
    ("उपलब्ध", "नहीं"),
    ("ઉપલબ્ધ", "નથી"),
    ("time", "nahi", "hai"),
    ("timing", "nahi", "hai"),
)

# The statuses in which a refusal is TRUE: the machine itself rejected the lead's time.
# Anything else — ok, accepted, locked, none, unclear — and there is nothing to refuse.
DENIABLE_STATUSES = frozenset({"out_of_hours", "in_past"})

# Carries no "?" on purpose, so `is_premature_time_talk` (which needs a question) can never
# catch this guard's own substitution in a phase without an OFFER line.
# Threads every other gate's vocabulary: no "fix"/"book" (confirmation cues), no "slot"
# or "available" (this guard's own cues), no sign-off word, and no "?" — so none of the
# four gates can catch the line this one substitutes.
SAFE_AVAILABILITY_LINE = (
    "Counselling subah das baje se shaam chhe baje tak kabhi bhi ho sakti hai — aap jo "
    "waqt bataayenge, hum usi hisaab se arrange kar denge."
)


# "Koi fixed slot nahi hota" is the sentence hard rule 8 WANTS — there is no fixed slot, so
# any time works. It shares its tokens with "Saturday ka slot nahi hai", which means the
# opposite, exactly as "tay hai" shares tokens with "tay karna baaki hai" above. Checked
# first, so the open-hours truth is never mistaken for a refusal — and so this guard cannot
# catch its own substitution, which says precisely this.
NOT_A_DENIAL_PHRASES = (
    ("fixed", "slot"),
    ("फिक्स्ड", "स्लॉट"),
)


def is_phantom_denial(line: str, slot_status: str) -> bool:
    """True if `line` tells the lead a time is unavailable when nothing was refused."""
    if not line or slot_status in DENIABLE_STATUSES:
        return False
    try:
        toks = tokens(line)
        if _has_phrase(toks, NOT_A_DENIAL_PHRASES):
            return False
        return _has_phrase(toks, DENIAL_PHRASES)
    except Exception:  # noqa: BLE001
        return False


def safe_availability(line: str, slot_status: str) -> str:
    """`line` unless it invents a refusal, in which case the open-hours truth."""
    try:
        if is_phantom_denial(line, slot_status):
            _log.warning("phantom denial blocked (slot_status=%s)", slot_status)
            _log.debug("phantom denial was: %r", line)
            return SAFE_AVAILABILITY_LINE
        return line
    except Exception:  # noqa: BLE001 — same posture as the other gates
        _log.exception("availability guard failed; passing the line through")
        return line


SIGNOFF_CUES = frozenset(
    {
        "milte",
        "milenge",
        "dhanyavaad",
        "dhanyawad",
        "shukriya",
        "alvida",
        "bye",
        "goodbye",
        "मिलते",
        "मिलेंगे",
        "धन्यवाद",
        "शुक्रिया",
        "अलविदा",
        "મળીએ",
        "મળીશું",
        "આભાર",
        "ધન્યવાદ",
    }
)

CLOSEABLE_STATUSES = frozenset({"locked"})

SIGNOFF_ATTEMPT_CAP = 3

# The hold when NO time is on the table yet. "Ek minute ji" is gone: the lead asked twice
# for that phrase to stop, once when the pacer said it and again when this line did. A hold
# does not need a stalling noise in front of it — it needs a question.
SAFE_HOLD_LINE = (
    "Visit ka time abhi tay karna baaki hai. Aapke liye subah theek rahega ya shaam?"
)

# The hold when the machine ALREADY HAS the time and is only waiting on the readback.
# On call e1fff5ee this branch did not exist, so an `accepted` slot got the line above and
# Roma contradicted herself inside one turn: "Monday, 3 August ko do baje slot tay ho chuka
# hai … visit ka time abhi tay karna baaki hai." Re-asking for a time she had just been
# given is also what kept the slot from locking — the lead had nothing to affirm.
# Does NOT end in "theek hai?". Roma's own readback almost always does, and this line is
# substituted next to hers, not instead of the whole turn — on fa3ae274 the lead heard
# "…confirm kar dijiye, theek hai? Theek hai?" and asked "दो बार क्यों बोला".
SAFE_READBACK_HOLD_LINE = "{slot} — yeh sahi rahega aapke liye?"

# The warm close for a lead who has asked the call to stop (hard rule 6). Lives here rather
# than in `controller.shortcircuit` because that module answers WHEN to say a fixed line and
# this file owns WHICH — and because a compliance sentence must have exactly one wording.
#
# Carries no second ask. A lead who said "call mat karo" and then hears one more offer has
# been ignored, and the whole point of detecting them deterministically is that the answer
# does not depend on a model deciding to try once more.
SAFE_SIGNOFF_LINE = (
    "Bilkul, main samajh gayi. Aapka time lene ke liye shukriya — aapka din shubh rahe."
)


def hold_line(slot_status: str = "none", slot: str = "") -> str:
    """What to say instead of a premature goodbye.

    Asks for CONFIRMATION when a time is already accepted, and for a time otherwise. The
    distinction is the whole point: `accepted` means the slot is decided and unconfirmed,
    so a line that re-asks for the time contradicts the machine's own state.
    """
    try:
        if slot_status == "accepted" and slot:
            return SAFE_READBACK_HOLD_LINE.format(slot=slot)
    except Exception:  # noqa: BLE001 — a hold must never cost a turn
        pass
    return SAFE_HOLD_LINE


def _has_phrase(toks: "list[str]", phrases) -> bool:
    """True if any phrase appears as CONSECUTIVE tokens in `toks`.

    Consecutive, not merely present: that is what separates "tay hai" (booked) from
    "tay karna baaki hai" (not booked yet), which share their first token and mean opposite
    things. Same tokenizer as every other matcher here — `guardrails.normalize.tokens()`,
    never `\\w`, which splits Indic combining marks.
    """
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i : i + n]) == phrase:
                return True
    return False


def is_premature_signoff(line: str, slot_status: str) -> bool:
    """True if `line` says goodbye while `slot_status` shows no locked visit."""
    if not line or slot_status in CLOSEABLE_STATUSES:
        return False
    return bool(set(tokens(line)) & SIGNOFF_CUES)


DISMISSAL_PHRASES = frozenset(
    {
        # "go away" / "leave"
        ("chale", "jao"),
        ("chale", "jaao"),
        ("चले", "जाओ"),
        ("ચાલ્યા", "જાવ"),
        # "put the phone down"
        ("phone", "rakho"),
        ("फोन", "रखो"),
        ("ફોન", "રાખો"),
        ("rakh", "do"),
        ("रख", "दो"),
        # "don't call" / "don't bother me"
        ("call", "mat", "karo"),
        ("कॉल", "मत", "करो"),
        ("pareshan", "mat", "karo"),
        ("परेशान", "मत", "करो"),
        ("madad", "mat", "karo"),
        ("मदद", "मत", "करो"),
        ("मदद", "नहीं", "चाहिए"),
        # "leave it" / "I don't want to talk"
        ("rehne", "do"),
        ("रहने", "दो"),
        ("baat", "nahi", "karni"),
        ("बात", "नहीं", "करनी"),
        ("interest", "nahi"),
        ("interest", "नहीं"),
    }
)


def lead_wants_out(text: str) -> bool:
    """Did the lead ask for this call to STOP? Narrow on purpose.

    Distinct from all three neighbours: a DEFERRAL (`turn.defers_the_call`) is "not right
    now", an OBJECTION is a reason to keep talking, and `_is_refusal_only` is any negation
    with no time evidence — which fires on "maine aapko kuch bola hi nahi hai", a complaint
    about being misquoted, not a request to hang up.

    Matched as CONSECUTIVE token phrases, never single tokens. Precision is what matters
    here and the asymmetry is deliberate: a false positive lets Roma close on a live lead
    and loses a booking, while a false negative only leaves today's behaviour in place. So
    "jao" alone does not qualify and neither does a bare "nahi".
    """
    try:
        return _has_phrase(tokens(text), DISMISSAL_PHRASES)
    except Exception:  # noqa: BLE001 — a detector must never take down the turn
        _log.exception("dismissal detector failed; treating as no dismissal")
        return False


def safe_close(
    line: str,
    slot_status: str,
    attempts: int,
    *,
    wants_out: bool = False,
    slot: str = "",
) -> str:
    """`line` if Roma may say goodbye, else `SAFE_HOLD_LINE` — until `attempts` reaches
    `SIGNOFF_ATTEMPT_CAP`, after which the call is allowed to end regardless.

    Fail-safe like `safe_confirmation`: a guard that can silence Roma is worse than the bug.

    ## `wants_out` — why this guard needed a veto

    Live call 049f0dc1 (2026-08-01). The lead said "aap meri madad mat karo" and then "aap
    chale jao yahan se". Roma tried to close politely, which is exactly what hard rule 6
    asks for — "BUSY OR ANNOYED: offer one alternative, then close politely. Never push
    twice." This guard overrode her and substituted `SAFE_HOLD_LINE`, a booking push, and
    would have done it three times over (`SIGNOFF_ATTEMPT_CAP`).

    The guard was built for a lead who is still engaged and a model that says goodbye too
    early. It had no concept of a lead who wants the call to END, so it turned a rule about
    not being pushy into a machine that is pushy on the lead's behalf. `wants_out` is the
    veto: when the lead has asked to stop, the sign-off passes untouched, every time.
    """
    try:
        if wants_out:
            return line
        if attempts >= SIGNOFF_ATTEMPT_CAP:
            return line
        if is_premature_signoff(line, slot_status):
            _log.warning(
                "premature sign-off held (slot_status=%s, attempt=%d/%d)",
                slot_status,
                attempts + 1,
                SIGNOFF_ATTEMPT_CAP,
            )
            _log.debug("premature sign-off was: %r", line)
            return hold_line(slot_status, slot)
        return line
    except Exception:  # noqa: BLE001 — same posture as the confirmation gate
        _log.exception("sign-off guard failed; passing the line through")
        return line


OFFER_PHASES = frozenset({"p5_pivot", "p7_close"})

TIME_CUES = frozenset(
    {
        "baje",
        "बजे",
        "બજે",
        "kab",
        "कब",
        "ક્યારે",
        "subah",
        "सुबह",
        "સવારે",
        "shaam",
        "शाम",
        "સાંજે",
        "timing",
        "morning",
        "evening",
        "weekday",
        "weekend",
        "slot",
    }
)

SAFE_COURSE_LINE = (
    "Pehle main aapko course ke baare mein bata deti hoon — "
    "SEO, social media marketing aur Google Ads, teen module hain. "
    "Aap kis cheez ke baare mein zyada jaanna chahenge?"
)

# Steers for when the modules fact is already spent. On call 6d7cc330 this guard fired
# twice after Roma had ALREADY named the three modules, so the lead heard that fact three
# times — twice in the identical sentence, because the steer was one fixed string. The
# repetition was blamed on the prompt and `{{said}}` was added to p1/p2 to stop it; that
# was wrong. These lines are substituted, not generated, so no prompt can reach them.
#
# Fact-free by construction: the steer's job is to hold premature time talk and hand the
# turn back, and it does not need to spend a course detail to do it. Rotated by the hold
# count so a second catch in one call does not repeat the first word for word.
#
# Every line here must satisfy the same invariants as SAFE_COURSE_LINE — carrying no
# TIME_CUES token, or the guard would catch its own substitution.
SAFE_COURSE_LINES_FACT_SPENT = (
    "Course ke baare mein aur kya jaanna chahenge aap?",
    "Aap course ke kis hisse ke baare mein aur sunna chahenge?",
)


def steer_line(said: "list[str] | set[str] | None" = None, holds: int = 0) -> str:
    """The line to substitute for premature time talk.

    Names the modules only while that fact is unspent; after that it asks without
    restating. `holds` is the number of times this guard has already fired on this call.
    """
    try:
        if said is not None and "modules" in said:
            return SAFE_COURSE_LINES_FACT_SPENT[holds % len(SAFE_COURSE_LINES_FACT_SPENT)]
    except Exception:  # noqa: BLE001 — a steer must never cost a turn
        pass
    return SAFE_COURSE_LINE


def is_premature_time_talk(line: str, phase: str) -> bool:
    """True if `line` raises a visit time in a phase that has no OFFER line.

    Gated on the line being a QUESTION as well as carrying a time cue, and that pairing is
    what keeps it safe. "Branch subah das se shaam chhe baje tak khuli rehti hai" is a
    factual answer to "kab khula hai" and hard rule eight positively requires it; the same
    words with a question mark are Roma asking the lead to pick a slot.
    """
    if not line or phase in OFFER_PHASES:
        return False
    if "?" not in line:
        return False
    return bool(set(tokens(line)) & TIME_CUES)


def safe_time_talk(
    line: str,
    phase: str,
    said: "list[str] | set[str] | None" = None,
    holds: int = 0,
) -> str:
    """`line` if the phase may discuss a visit time, else a course question."""
    try:
        if is_premature_time_talk(line, phase):
            _log.warning(
                "premature time talk held (phase=%s); steering back to the course", phase
            )
            _log.debug("premature time talk was: %r", line)
            return steer_line(said, holds)
        return line
    except Exception:  # noqa: BLE001 — same posture as the other two gates
        _log.exception("time-talk guard failed; passing the line through")
        return line


def is_phantom_confirmation(line: str, slot_status: str) -> bool:
    """True if `line` announces a booking that `slot_status` does not support.

    Token-equality after `normalize.tokens()` — never a substring test, or "confirm" fires
    inside unrelated words, and never `\\w`, which splits Indic combining marks (the bug
    docs/04 exists downstream of).
    """
    if not line or slot_status in CONFIRMABLE_STATUSES:
        return False
    toks = tokens(line)
    if set(toks) & CONFIRMATION_CUES:
        return True
    return _has_phrase(toks, CONFIRMATION_PHRASES)


def safe_confirmation(line: str, slot_status: str, phase: str = "") -> str:
    """`line` if it may be spoken, else the safe re-offer. Never raises, never empty.

    Fail-safe like `guardrails.safe_output`: if this function itself breaks, the caller
    still gets a speakable line. It must never be the reason a turn goes silent.
    """
    try:
        if is_phantom_confirmation(line, slot_status):
            _log.warning(
                "phantom confirmation blocked (slot_status=%s); substituting the re-offer",
                slot_status,
            )
            _log.debug("phantom confirmation was: %r", line)
            return reoffer_line(phase)
        return line
    except Exception:  # noqa: BLE001 — a guard that can kill the turn is worse than the bug
        _log.exception("confirmation guard failed; passing the line through")
        return line


__all__ = [
    "CONFIRMATION_CUES",
    "CONFIRMATION_PHRASES",
    "CONFIRMABLE_STATUSES",
    "REFUSAL_STATUSES",
    "SAFE_REOFFER_LINE",
    "SIGNOFF_CUES",
    "CLOSEABLE_STATUSES",
    "SIGNOFF_ATTEMPT_CAP",
    "SAFE_HOLD_LINE",
    "SAFE_REOFFER_LINE_IN_OFFER",
    "reoffer_line",
    "SAFE_AVAILABILITY_LINE",
    "is_phantom_denial",
    "safe_availability",
    "SAFE_READBACK_HOLD_LINE",
    "SAFE_SIGNOFF_LINE",
    "hold_line",
    "DISMISSAL_PHRASES",
    "lead_wants_out",
    "is_phantom_confirmation",
    "safe_confirmation",
    "is_premature_signoff",
    "safe_close",
    "OFFER_PHASES",
    "TIME_CUES",
    "SAFE_COURSE_LINE",
    "SAFE_COURSE_LINES_FACT_SPENT",
    "steer_line",
    "is_premature_time_talk",
    "safe_time_talk",
]
