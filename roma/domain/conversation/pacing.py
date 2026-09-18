"""The call's time budget (docs/02: Roma books a visit, she does not counsel).

Roma has **five minutes**. Not as an average — as a ceiling. Everything a lead actually
wants to know (fees, EMI, batch fit, placement record) is the counsellor's job at the
visit, and the real Weltec calls prove it: the shortest counselling session in the
reference corpus runs 17 minutes and the longest 42. Roma is the door, not the room.

## Why a machine clock and not a prompt line

"Be brief" is not a budget. A model asked to hurry still spends four turns explaining SEO,
because nothing measures it. So the elapsed time is measured in code, converted to one of
four `Phase`s here, and rendered into the prompt as an instruction Roma cannot drift past
— the same shape as `state.slot_status`. At the hard deadline the call is ENDED, by
`telephony.closing.CallCloser`, whatever the model is in the middle of saying.

## The four bands

| band | from | what Roma is told |
|---|---|---|
| `open` | 0:00 | nothing — run the normal phase flow |
| `hurry` | 3:00 | stop exploring, get to two slots |
| `close` | 4:00 | one slot or WhatsApp, then sign off |
| `over` | 5:00 | the call is ended under her |

Derived from the live calls, not guessed: CAea3dd7e reached a (mis-)confirmed booking in
**191 seconds** with five discovery questions and a value turn, so 3:00 is genuinely late
rather than aggressive. The margin between `close` and `over` is one minute because a
sign-off plus a WhatsApp promise is two short turns.
"""

HURRY_SECS = 180.0
CLOSE_SECS = 240.0
OVER_SECS = 300.0

OVER_GRACE_SECS = 30.0

PACING_LINES = {
    "open": "",
    "hurry": (
        "TIME: call aadhi se zyada nikal gayi hai. Ab nayi baat mat chhedo — jo pooch "
        "rahe hain uska ek line mein jawab do aur turant do slot offer karo."
    ),
    "close": (
        "TIME: call khatam karni hai. Ek hi slot offer karo, ya bolo ki details WhatsApp "
        "par bhej rahi hoon. Koi nayi baat nahi, koi naya sawaal nahi."
    ),
    "over": ("TIME: call ab khatam ho rahi hai. Sirf sign off karo — ek chhoti line, aur bas."),
}

FORCE_PIVOT_BANDS = frozenset({"hurry", "close", "over"})


def band(elapsed_secs: float) -> str:
    """Which pacing band the call is in. Monotonic, so it can never go backwards."""
    if elapsed_secs >= OVER_SECS:
        return "over"
    if elapsed_secs >= CLOSE_SECS:
        return "close"
    if elapsed_secs >= HURRY_SECS:
        return "hurry"
    return "open"


def pacing_line(elapsed_secs: float) -> str:
    """The instruction for the phase prompt. Empty string while there is time."""
    return PACING_LINES[band(elapsed_secs)]


def should_force_pivot(elapsed_secs: float, phase: str) -> bool:
    """True if the machine should cut discovery/value short and go straight to booking.

    Never fires in P6 (an objection is being answered — abandoning it mid-sentence reads
    as evasion, which is the one thing docs/03 says loses a warm lead) or in P7 (already
    closing). It also never fires in P1: without the inquiry confirm there is nothing to
    book, and pivoting at a lead who has not agreed to talk is how a call becomes a
    complaint.
    """
    return band(elapsed_secs) in FORCE_PIVOT_BANDS and phase in (
        "p2_discover",
        "p3_value",
        "p4_structure",
    )


__all__ = [
    "band",
    "pacing_line",
    "should_force_pivot",
    "HURRY_SECS",
    "CLOSE_SECS",
    "OVER_SECS",
    "OVER_GRACE_SECS",
    "PACING_LINES",
]
