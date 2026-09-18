"""The per-call state datum (docs/03, docs/06).

One `CallState` per call, keyed by Twilio Call SID in Redis (`store.py`). It carries
the discovery slots, the phase-machine bookkeeping, and the win-condition fields. It is
a plain dataclass — the phase is *state*, not an agent (the LangGraph rejection, docs/11).

`as_prompt_vars()` is the bridge to `roma.domain.conversation.prompts.assemble_system_prompt`, which
reads its `call_state` via `__contains__`/`__getitem__` — so the prompt layer needs zero
changes; the controller just hands it this view.
"""

from dataclasses import dataclass, field
from datetime import datetime

from roma.domain.conversation.facts import already_said_line
from roma.domain.conversation.pacing import pacing_line

PHASES: tuple[str, ...] = (
    "p1_open",
    "p2_discover",
    "p3_value",
    "p4_structure",
    "p5_pivot",
    "p6_objection",
    "p7_close",
)

DISCOVERY_ORDER: tuple[str, ...] = (
    "lead_name",
    "current_status",
    "education",
    "passing_year",
    "city",
)

# How many turns a single slot may be attempted before the machine gives up on it and moves
# to the next one. Without this the pointer sticks: `next_discovery_slot` returns the first
# unfilled slot forever, and it drives EXTRACTION only — it does not force the question. So a
# caller who will not give their name leaves the pointer on `lead_name` while the model
# sensibly asks about something else, and every later answer is extracted against a field
# nobody was asked about, returns null, and fills nothing for the rest of P2.
#
# Two is deliberate: one re-ask is a normal misheard-reply recovery, a third is nagging.
#
# The trade: a capped slot is never retried, so a name volunteered later ("waise mera naam
# Nikhil hai") is not captured. That is the right side of the trade — a stuck pointer costs
# all four remaining slots — but it IS a real limitation. Do not "fix" it by removing the cap.
SLOT_ATTEMPT_CAP = 2


SLOT_STATUS_LINES = {
    "none": "SLOT STATUS: abhi tak koi visit slot accept nahi hua hai.",
    "unclear": (
        "SLOT STATUS: lead ne jo time bola wo clear nahi tha. Confirm kuch mat karo — "
        "dobara poochho ya do time bata do."
    ),
    "in_past": (
        "SLOT STATUS: lead ka bola hua time nikal chuka hai. Use confirm mat karo — "
        "aage ka koi time bata do."
    ),
    "day_only": (
        "SLOT STATUS: lead ne DIN bata diya hai, lekin time nahi. Us din ka time poochho — "
        "OFFER mein us din ke jo slots hain wahi bolo. Koi doosra din mat bolo, aur visit "
        "confirm bilkul mat karo jab tak time tay na ho."
    ),
    "out_of_hours": (
        "SLOT STATUS: lead ka bola hua time BRANCH KE HOURS KE BAHAR hai. Branch subah "
        "das se shaam chhe baje tak hi khuli rehti hai. Us time ko confirm bilkul mat "
        "karo — politely batao ki us waqt branch band hoti hai, aur uske baad do valid "
        "time bata do."
    ),
    "accepted": (
        "SLOT STATUS: machine ne yeh slot accept kiya hai — {slot}. Readback isi ka karo, "
        "bilkul isi din aur isi time ka. Koi doosra time mat bolo."
    ),
    "locked": (
        "SLOT STATUS: visit LOCK ho chuki hai — {slot}. Yahi time bolo, aur sign off karo."
    ),
}

_OFFER_LINE = (
    "OFFER: yeh DO time SUGGEST karo — {a} ya {b}. Yeh sirf suggestion hain: counselling "
    "das baje se chhe baje tak kabhi bhi ho sakti hai, koi fixed slot nahi hai. Agar lead "
    "in do ke alawa koi bhi time bole jo das aur chhe ke beech ho, use turant maan lo aur "
    "readback karo — 'wo slot available nahi hai' kabhi mat bolo. Khud se koi teesra time "
    "tab tak mat bolo jab tak lead na bole."
)
_ONE_OFFER_LINE = (
    "OFFER: yeh time suggest karo — {a}. Yeh sirf ek suggestion hai: counselling das se "
    "chhe ke beech kabhi bhi ho sakti hai. Lead koi bhi time bole jo das aur chhe ke beech "
    "ho, use turant maan lo."
)
_NO_OFFER_LINE = "OFFER: abhi koi slot tay nahi hua hai."
_OFFER_AFTER_REFUSAL_LINE = (
    "OFFER: lead ne jo time bola wo use nahi ho sakta — SLOT STATUS dekho. Pehle wo baat "
    "warmly bolo, phir yeh do time suggest karo — {a} ya {b}. Lead koi DOOSRA time bole jo "
    "das aur chhe ke beech ho aur abhi nikla na ho, to use turant maan lo aur readback karo. "
    "Jo time abhi refuse hua hai use dobara mat maano."
)
_OFFER_TAKEN_LINE = (
    "OFFER: slot tay ho chuka hai. Ab koi naya time offer mat karo aur koi doosra time "
    "bolo hi mat — sirf SLOT STATUS wala din aur time bolo."
)

_KNOWN_LABELS = {
    "lead_name": "naam",
    "education": "padhai",
    "passing_year": "passing year",
    "current_status": "abhi kya kar rahe hain",
    "city": "sheher",
    "timing_constraint": "free time",
}
_KNOWN_LINE = (
    "PATA HAI (lead ne yeh already bata diya hai): {known}. Inmein se koi bhi cheez "
    "dobara mat poochho — pehle se poochha hua sawaal dobara poochhna sabse saaf signal "
    "hai ki aap sun nahi rahe the."
)
_NO_KNOWN_LINE = "PATA HAI: abhi tak lead ne kuch nahi bataya hai."

_TODAY_LINE = (
    "AAJ: {today} hai. Yeh haqeeqat hai — koi aur din 'aaj' mat bolo. Lead agar koi weekday "
    "bole, wo AGLA aisa din hai, aaj nahi."
)

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def spoken_slot(iso: "str | None") -> str:
    """An ISO slot as the weekday/date/time Roma should say (docs/03 P7 readback).

    Returns "" for None or anything unparseable — the caller falls back to a status line
    with no slot in it, which is strictly safer than voicing a half-parsed date.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    hour12 = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return (
        f"{_WEEKDAY_NAMES[dt.weekday()]} {dt.day} {dt.strftime('%B')}, "
        f"{hour12}:{dt.minute:02d} {meridiem}"
    )


@dataclass
class CallState:
    """docs/03 call-state object + the pinned win-condition/bookkeeping fields.

    `branch` (Vadodara/Ahmedabad — the branch they visit) seeds a prompt template var.

    `lead_name` does NOT: Roma is inbound, the caller rang us, and we know nothing about them
    until they say it. It starts None and is the FIRST discovery slot (docs/03). It reaches
    the model through the PATA HAI line rather than a `{{lead_name}}` substitution, which is
    what makes it survive a barge-in.

    `course` is the fixed internal id. There is no `course_interest`: Weltec sells one course,
    nothing preloaded it and no fragment read it (docs/decisions.md, 2026-07-31).
    """

    call_sid: str = ""
    branch: str = "Vadodara"
    # Outbound only: "student" | "working_professional" | "unemployed", supplied by the
    # trigger (dialer.leadstore.SEGMENTS). A VARIABLE, not a branch — one flow, one set of
    # override rules. Empty on any call where we did not know, which is every inbound call.
    #
    # Carried into prompt vars here so the plumbing is done and testable; NO fragment reads
    # `{{segment}}` yet. What it will change is exactly two things (P2's study-or-work
    # question becoming a confirmation, and which value proof leads in P3), and both are
    # prompt work still awaiting sign-off.
    segment: str = ""
    # None, not "": `next_discovery_slot` treats a non-None value as already filled, so an
    # empty-string default would mark the name permanently captured and Roma would never ask.
    lead_name: "str | None" = None
    course: str = "digital_marketing"

    education: "str | None" = None
    passing_year: "str | None" = None
    current_status: "str | None" = None
    city: "str | None" = None
    timing_constraint: "str | None" = None

    placement_interest: "str | None" = None
    mode_pref: "str | None" = None

    # slot name -> how many turns we have tried to fill it. See SLOT_ATTEMPT_CAP.
    slot_attempts: dict = field(default_factory=dict)

    objection_counts: dict = field(default_factory=dict)
    slots_offered: list = field(default_factory=list)
    accepted_slot: "str | None" = None
    locked_slot: "str | None" = None

    pending_day: "str | None" = None

    slot_status: str = "none"

    elapsed_secs: float = 0.0

    phase: str = "p1_open"
    turn_count: int = 0
    phase_turn_count: int = 0

    inquiry_confirmed: bool = False
    readback_confirmed: bool = False

    # STICKY, deliberately: set once the lead asks the call to stop, never cleared. Hard
    # rule 6 is "offer one alternative, then close politely — never push twice", which is a
    # statement about the whole call, not about one turn. Per-turn would let the sign-off
    # guard start pushing again on the next silence, which is the bug it exists to fix
    # (`confirmguard.safe_close`, live call 049f0dc1).
    lead_wants_out: bool = False

    # ISO date of the call, set by `advance_turn` from the same `now` the slot resolver uses,
    # so "today" in the prompt and "today" in the machine can never disagree.
    today_iso: str = ""

    # Course facts Roma has already spoken, in the order she spoke them. Sorted nowhere —
    # order is "what she said first" and it keeps the prompt line stable across turns, which
    # matters because an unstable line costs a prefix cache hit (docs/11).
    facts_said: "list[str]" = field(default_factory=list)

    # Consecutive turns in which the LEAD raised the visit time. Reset the moment they talk
    # about something else, so it counts insistence rather than a total.
    #
    # `confirmguard.is_premature_time_talk` exists to stop ROMA volunteering a time before
    # the OFFER line. When the LEAD keeps raising it, that premise is inverted: the guard
    # substitutes a course question, the lead asks about time again, and the call deadlocks.
    # Call 0f09c8a3 spent four turns there (`time_talk_holds=4`) with Roma asking "course ke
    # baare mein aur kya jaanna chahenge?" at a lead who was trying to book.
    lead_time_asks: int = 0

    # Total off-topic turns this call (controller.offtopic). A running count, not
    # consecutive like `lead_time_asks`: three drifts spread across the call still mean the
    # deflection bank has been spent and the converge line should take over.
    off_topic_turns: int = 0

    def record_facts(self, keys) -> None:
        """Mark `keys` as spent. Idempotent — a fact repeated is still one entry."""
        for key in keys:
            if key not in self.facts_said:
                self.facts_said.append(key)

    def next_discovery_slot(self) -> "str | None":
        """The first unfilled discovery slot in fixed order, or None if none is left.

        Skips already-filled slots so a resumed call never re-asks (docs/06), and skips slots
        already attempted `SLOT_ATTEMPT_CAP` times so one the caller will not answer cannot
        pin the pointer and swallow every slot behind it.
        """
        for slot in DISCOVERY_ORDER:
            if getattr(self, slot) is None and self.slot_attempts.get(slot, 0) < (
                SLOT_ATTEMPT_CAP
            ):
                return slot
        return None

    def record_slot_attempt(self, slot: str) -> None:
        """Note that `slot` was asked for and nothing usable came back."""
        self.slot_attempts[slot] = self.slot_attempts.get(slot, 0) + 1

    def filled_discovery_count(self) -> int:
        """How many of the five discovery slots are filled (docs/03 P2 exit gate)."""
        return sum(1 for slot in DISCOVERY_ORDER if getattr(self, slot) is not None)

    def as_prompt_vars(self) -> dict:
        """The mapping `assemble_system_prompt` substitutes into the fragments (docs/11).

        `slot_status` renders inside the PHASE fragment, never in persona/hard_rules — the
        `persona -> hard_rules` prefix must stay byte-identical across a call's turns or
        OpenAI prefix caching stops hitting (docs/11).
        """
        slot = spoken_slot(self.locked_slot or self.accepted_slot)
        status = self.slot_status if self.slot_status in SLOT_STATUS_LINES else "none"
        if status in ("accepted", "locked") and not slot:
            status = "none"
        return {
            "branch": self.branch,
            # `or ""` because lead_name is None until P2 captures it — a fragment must never
            # render the string "None" at a lead.
            "lead_name": self.lead_name or "",
            "segment": self.segment,
            "slot_status": SLOT_STATUS_LINES[status].format(slot=slot),
            "pacing": pacing_line(self.elapsed_secs),
            "offered_slots": self._offer_line(status),
            "known": self._known_line(),
            # Roma is told what day it IS. Without this she has no way to know, and on live
            # call e3237622 she invented one: the lead offered "Wednesday ko aata hoon 2
            # baje" and she refused it with "Aaj Wednesday hai, toh aap ab nahi aa sakte" —
            # on a Saturday. A bookable slot turned into a refusal built on a made-up fact
            # (hard rule 4). She cannot reason about a named weekday without knowing today.
            "today": self._today_line(),
            # The mirror of `known` for the OTHER direction: what Roma has already told
            # THEM. Same reasoning, same failure mode (`controller.facts`).
            "said": already_said_line(self.facts_said),
        }

    def _today_line(self) -> str:
        """Today's weekday and date, as a fact she may not contradict.

        Empty when the call has no clock reference rather than guessing — an unknown day is
        survivable, a wrong one refuses real bookings.
        """
        if not self.today_iso:
            return ""
        try:
            d = datetime.fromisoformat(self.today_iso)
        except ValueError:
            return ""
        return _TODAY_LINE.format(today=f"{_WEEKDAY_NAMES[d.weekday()]}, {d.day} {d:%B}")

    def _known_line(self) -> str:
        """What the lead has already told us, as an instruction not to ask again.

        The conversation history is in the context, so in principle the model can see all
        of this. In practice it cannot rely on it: a barge-in truncates the assistant turn
        mid-sentence, and on CA9933275 Roma asked "aap kis saal complete hui thi?", was
        interrupted, and asked it again in the very next turn. The lead's complaint was that
        she forgets what they told her.

        So the answers are restated every turn from the machine's own state, which is the
        one record of them that an interruption cannot damage. Values are the lead's own
        words as extracted, so this is lead data — it goes into the system prompt (which is
        already lead-specific) and is never logged.
        """
        known = [
            (label, getattr(self, slot))
            for slot, label in _KNOWN_LABELS.items()
            if getattr(self, slot) is not None
        ]
        if not known:
            return _NO_KNOWN_LINE
        joined = ", ".join(f"{label} {value}" for label, value in known)
        return _KNOWN_LINE.format(known=joined)

    def _offer_line(self, status: str = "none") -> str:
        """The OFFER instruction for the phase fragment.

        Renders only slots that actually parse — a malformed ISO string must degrade to
        "no offer" rather than put a dangling blank into a sentence Roma then reads aloud.

        ## Why this reads the status

        An accepted slot SPENDS the offer, and until this checked, `p7_close.md` rendered
        both at once: SLOT STATUS saying "read back Monday 2:00 PM" directly above OFFER
        saying "offer only 11 AM or 5 PM". Two different sets of times, one prompt.

        Live call CA1652a5e (2026-07-27). The lead said `મેં 2 બજે વિઝિટ કરૂંગા` — "I'll
        visit at 2" — and the machine took it:

            slot verdict: phase=p5_pivot reason=ok accepted=True hour=2 \
                resolved=2026-07-27T14:00:00+05:30 status=accepted

        A booking, correctly recorded. Roma then asked "gyaarah baje ya paanch baje?" seven
        times in a row and the call ended `won=False`. She read the OFFER list, because it
        was still sitting there naming times, and never read back the 2 PM she had been
        given. Removing the competing instruction is the fix; telling her more firmly which
        of two contradictory lines to obey is not.
        """
        if status in ("accepted", "locked"):
            return _OFFER_TAKEN_LINE
        spoken = [s for s in (spoken_slot(iso) for iso in self.slots_offered) if s]
        if status in ("in_past", "out_of_hours") and len(spoken) >= 2:
            return _OFFER_AFTER_REFUSAL_LINE.format(a=spoken[0], b=spoken[1])
        if len(spoken) >= 2:
            return _OFFER_LINE.format(a=spoken[0], b=spoken[1])
        if len(spoken) == 1:
            return _ONE_OFFER_LINE.format(a=spoken[0])
        return _NO_OFFER_LINE

    def to_dict(self) -> dict:
        """Plain JSON-serializable dict for the Redis checkpoint (docs/06)."""
        return {
            "call_sid": self.call_sid,
            "branch": self.branch,
            "segment": self.segment,
            "lead_name": self.lead_name,
            "course": self.course,
            "education": self.education,
            "passing_year": self.passing_year,
            "current_status": self.current_status,
            "city": self.city,
            "timing_constraint": self.timing_constraint,
            "placement_interest": self.placement_interest,
            "mode_pref": self.mode_pref,
            "slot_attempts": dict(self.slot_attempts),
            "objection_counts": dict(self.objection_counts),
            "slots_offered": list(self.slots_offered),
            "accepted_slot": self.accepted_slot,
            "locked_slot": self.locked_slot,
            "pending_day": self.pending_day,
            "phase": self.phase,
            "turn_count": self.turn_count,
            "phase_turn_count": self.phase_turn_count,
            "inquiry_confirmed": self.inquiry_confirmed,
            "readback_confirmed": self.readback_confirmed,
            "facts_said": list(self.facts_said),
            # Must survive a resume: a lead who asked to stop before the drop has not
            # changed their mind because the socket did.
            "lead_wants_out": self.lead_wants_out,
            # Same reasoning: a call that reconverges after a drop must not hand a serial
            # drifter a fresh deflection bank.
            "off_topic_turns": self.off_topic_turns,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CallState":
        """Rehydrate a checkpoint (docs/06 resume-on-drop). Unknown keys are ignored so
        an older checkpoint schema never crashes a resume."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


__all__ = [
    "CallState",
    "PHASES",
    "DISCOVERY_ORDER",
    "SLOT_ATTEMPT_CAP",
    "SLOT_STATUS_LINES",
    "spoken_slot",
]
