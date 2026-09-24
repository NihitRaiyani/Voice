"""Per-turn orchestration (docs/03 + docs/06).

`advance_turn` is the one place the pieces compose: given the finalized user text, it
gathers the turn's signals (per phase), lets the PURE machine pick the next phase, applies
the resulting state mutations, and checkpoints to the store on durable events. It runs
BEFORE Roma's response is generated, so the phase it lands on is the phase Roma speaks —
"the controller picks the phase; the model never does" (docs/03).

The second model call (slot extraction) is gated here to the slot-bearing phases (P2/P5/P7)
and skipped everywhere else, honoring "one turn = one LLM call unless a phase provably needs
one". Dependencies (the extraction calls, the classifier, the store, `now`) are injected so
this is unit-testable with no live API.
"""

import logging
from datetime import date, datetime

from roma.domain.appointments.calendar import DEFAULT_CALENDAR, VisitCalendar
from roma.domain.appointments.slots import extract_discovery_slot, extract_time_slot
from roma.domain.appointments.timeresolve import (
    CONFIDENCE_THRESHOLD,
    SlotVerdict,
    resolve_visit_slot,
)
from roma.domain.conversation.confirmguard import TIME_CUES, lead_wants_out
from roma.domain.conversation.machine import (
    P1_OPEN,
    P2_DISCOVER,
    P5_PIVOT,
    P7_CLOSE,
    Transition,
    TurnSignals,
)
from roma.domain.conversation.objection import classify_objection
from roma.domain.conversation.pacing import band, should_force_pivot
from roma.domain.conversation.state import SLOT_ATTEMPT_CAP, CallState, spoken_slot
from roma.domain.conversation.state_machine import save_state
from roma.domain.conversation.state_machine import transition as transition_state
from roma.domain.safety.normalize import tokens

_log = logging.getLogger("roma.domain.conversation")

_AFFIRMATIONS = {
    "haan",
    "haanji",
    "han",
    "ha",
    "ji",
    "jee",
    "yes",
    "yeah",
    "ok",
    "okay",
    "theek",
    "thik",
    "sahi",
    "bilkul",
    "pakka",
    "chalega",
    "confirm",
    "confirmed",
    "correct",
    "done",
    "barabar",
    "chokkas",
    "haasto",
    "हाँ",
    "हां",
    "जी",
    "ठीक",
    "सही",
    "बिल्कुल",
    "हा",
    "बराबर",
    "હા",
    "હાજી",
    "જી",
    "જીહા",
    "ઠીક",
    "સાચું",
    "સહી",
    "ખરું",
    "બરાબર",
    "ચોક્કસ",
    "હાસ્તો",
}


_NEGATIONS = {
    "nahi",
    "nahin",
    "nai",
    "naa",
    "no",
    "nope",
    "never",
    "mat",
    "नहीं",
    "नही",
    "ना",
    "मत",
    "ના",
    "નહીં",
    "નહિ",
    "નહી",
    "નથી",
}


def is_affirmation(text: str) -> bool:
    """True if the lead's reply carries an affirmation cue and NO negation (P1 confirm)."""
    if not text:
        return False
    toks = set(tokens(text))
    if toks & _NEGATIONS:
        return False
    return bool(toks & _AFFIRMATIONS)


# "Who am I speaking to?" — the one reply to the opener that is NOT the caller stating their
# business. Roma has said "Hello, Weltec Institute" and they did not catch it.
_WHO_WORDS = {"kaun", "kon", "kaunsa", "कौन", "કોણ", "who"}

# What they might name us back with: "Weltec hai?" / "Weltec Institute?"
_US_WORDS = {"weltec", "વેલ્ટેક", "वेलटेक"}

# `_US_WORDS` only reads as an identity question in a SHORT utterance. "Weltec ke digital
# marketing course ke baare mein poochhna tha" names us too and is plainly business.
_NAMING_US_MAX_TOKENS = 3


def asks_who_we_are(text: str) -> bool:
    """Did the caller ask who picked up, rather than say why they rang?

    Roma's opener is a cached "Hello, Weltec Institute" — a second of audio at connect, no
    LLM. Most callers answer it by stating their business, and those go straight to
    discovery. A caller who instead asks "kaun bol raha hai?" has not heard it, and asking
    such a person for their NAME (P2's first question) is the rudest possible reply. They
    stay in P1, which does one thing: says who we are again.
    """
    toks = list(tokens(text))
    seen = set(toks)
    if seen & _WHO_WORDS:
        return True
    return bool(seen & _US_WORDS) and len(toks) <= _NAMING_US_MAX_TOKENS


_COURSE_WORDS = {
    "course",
    "कोर्स",
    "કોર્સ",
    "syllabus",
    "module",
    "modules",
    "मॉड्यूल",
    "કોર્સમાં",
}

# A request to be TOLD. Without one of these, "course" is almost always the lead ANSWERING
# the P2 status question ("koi course kar raha hoon"), which must not trigger anything.
_ASK_CUES = {
    "bataiye",
    "batao",
    "bataye",
    "bataoge",
    "sikhaoge",
    "बताइए",
    "बताओ",
    "बताईये",
    "સિખાવો",
    "બતાવો",
    "jaanna",
    "janna",
    "जानना",
    "જાણવું",
    "kya",
    "क्या",
    "શું",
    "samjhaiye",
    "समझाइए",
    "detail",
    "details",
    "baare",
    "बारे",
    "વિશે",
}


def asks_about_course(text: str) -> bool:
    """Did the lead ask what the COURSE is?

    ## Why this outranks the discovery queue

    Live call 049f0dc1 (2026-08-01). In P2 the lead asked five separate times — "mujhe
    course ke baare mein bataiye", "maine poochha aapko" — and the machine had no signal for
    it. P2 only exits when five slots fill or it times out after five turns, and the lead was
    complaining rather than answering, so neither happened for SIX turns. Roma answered from
    the P2 prompt, which forbids pitching and carries no course content, so she improvised
    the same three facts out of the persona's phrase list over and over.

    The lead asking what the course is IS the cue to go and explain it. P3 exists for exactly
    that question and has the KB to answer it properly.

    Both halves are required. A bare "course" is usually the lead answering the P2 status
    question ("koi course kar rahe hain?" -> "haan course kar raha hoon"), and advancing on
    that would skip discovery for someone who never asked anything.
    """
    toks = set(tokens(text))
    return bool(toks & _COURSE_WORDS and toks & _ASK_CUES)


# Two consecutive turns, not one. One mention is often part of a question about the course
# ("subah ki batch hoti hai kya?") and pivoting on it would offer a slot to someone still
# deciding. Two in a row is the lead steering, and the call should follow them.
LEAD_TIME_ASKS_BEFORE_PIVOT = 2

# Asking to BOOK, as opposed to naming a clock time. `TIME_CUES` holds only times of day —
# baje, kab, subah, shaam — so "mujhe visit schedule karni hai" matched nothing in it, and
# on call ca529641 the lead asked three times while Roma answered:
#
#   "par is waqt main course ke details share kar rahi hoon visit abhi schedule nahi
#    kar rahi. Aapko pehle course ki value clear honi chahiye."
#   "visit abhi schedule wahi hota hai jab mujhe prompt ho."
#
# She was obeying the phase. The phase was wrong. A lead who already knows what they want
# does not have to be walked through the pitch to earn a slot, and being refused one is the
# fastest way to lose someone who was ready to book.
# Unambiguous: none of these can mean "I am busy".
_BOOKING_WORDS = frozenset(
    {
        "visit",
        "schedule",
        "appointment",
        "booking",
        "विजिट",
        "अपॉइंटमेंट",
        "શેડ્યુલ",
        "વિઝિટ",
    }
)

# AMBIGUOUS, and "meeting" is why this split exists. It is already a standalone deferral cue
# — "abhi meeting mein hoon" is the commonest way a lead says they are busy — so on call
# 56504a23 the lead said "…toh ab hamari meeting schedule fix karo" and `defers_the_call`
# fired on the very word that carried the booking intent. The deferral won and Roma stayed
# in P3. These need a booking VERB alongside them to count.
_BOOKING_AMBIGUOUS = frozenset(
    {
        "meeting",
        "मीटिंग",
        "મીટિંગ",
        "milna",
        "milne",
        "aana",
        "aunga",
        "aaunga",
        "मिलना",
        "मिलने",
        "आना",
        "મળવા",
        "આવવું",
    }
)

_BOOKING_VERBS = frozenset(
    {
        "fix",
        "book",
        "schedule",
        "set",
        "karo",
        "kar",
        "karna",
        "kardo",
        "kijiye",
        "kariye",
        "dijiye",
        "rakho",
        "chahiye",
        "chahta",
        "chahti",
        "chahunga",
        "chahungi",
        "sakta",
        "sakte",
        "sakti",
        "चाहता",
        "चाहती",
        "सकता",
        "सकते",
        "करो",
        "करना",
        "कीजिए",
        "दीजिए",
        "चाहिए",
        "કરો",
        "કરવું",
    }
)


def wants_to_book(text: str) -> bool:
    """Did the LEAD ask to schedule the visit, in words rather than clock times?

    Fires on ONE turn, unlike `raises_the_visit_time`. "Visit schedule kar dijiye" is not
    ambiguous the way "subah ki batch hoti hai kya?" is, so there is nothing to confirm by
    waiting — and waiting is what produced three consecutive refusals on ca529641.

    The caller is responsible for not acting on this when the lead is trying to LEAVE:
    `lead_wants_out` and `defers_the_call` both read as booking words otherwise ("baad mein
    milte hain"), and pushing a slot at someone saying goodbye is the bug hard rule 6 exists
    to prevent.
    """
    try:
        toks = set(tokens(text))
        if toks & _BOOKING_WORDS:
            return True
        return bool(toks & _BOOKING_AMBIGUOUS and toks & _BOOKING_VERBS)
    except Exception:  # noqa: BLE001 — a detector must never take down the turn
        return False


def raises_the_visit_time(text: str) -> bool:
    """Did the LEAD bring up when they would come?

    Same lexicon the guard matches on, read from the other side of the conversation. The
    guard asks "is ROMA raising a time too early"; this asks "is the LEAD". Deliberately no
    ask-cue requirement, unlike `asks_about_course`: "Thursday subah aa jaunga" is a booking
    move with no question in it at all.
    """
    try:
        return bool(set(tokens(text)) & TIME_CUES)
    except Exception:  # noqa: BLE001 — a detector must never take down the turn
        return False


def opened_the_conversation(text: str) -> bool:
    """Inbound P1 exit: has the caller stated their business?

    Outbound needed an affirmation here because WE interrupted THEM — "kya abhi 2 minute baat
    kar sakte hain?" is a real question and "haan" is a real answer. Inbound inverts it: they
    dialled Weltec and waited for someone to pick up, so the inquiry is the call itself. A
    caller who says "course ki information chahiye thi" has confirmed it far more clearly
    than one who says "haan" — and requiring the affirmation left them stuck in P1 for the
    whole call, because they never say it.

    The one reply that is NOT an opening is a short bare negation — "nahi", "galat number" —
    which is a wrong number rather than an enquiry, and must not be walked into discovery.
    """
    toks = set(tokens(text))
    if not toks:
        return False
    if toks & _NEGATIONS and len(toks) <= _BARE_NEGATION_MAX_TOKENS:
        return False
    return True


# "nahi" / "nahi ji" / "galat number hai" — short enough to be a refusal rather than a
# sentence that merely contains a negation ("nahi, mujhe course ke baare mein poochhna tha").
_BARE_NEGATION_MAX_TOKENS = 3


# "Not now" — the outbound-only answer to the permission question. Gujarati spellings carried
# throughout, because STT returns Gujarati script (D3) and a matcher without them is a
# matcher that never fires on half the calls.
_DEFERRAL_WORDS = {
    # busy / occupied
    "busy",
    "vyast",
    "વ્યસ્ત",
    "व्यस्त",
    # in something right now
    "meeting",
    "मीटिंग",
    "મીટિંગ",
    "class",
    "lecture",
    "office",
    "kaam",
    "काम",
    "કામ",
    "driving",
    "drive",
    # later
    "baad",
    "बाद",
    "બાદ",
    "later",
    "afterwards",
    "pachi",
    "પછી",
    "abhi",  # only ever counted alongside a deferral cue below
    "अभी",
    "અત્યારે",
}
# A deferral needs a "not now" sense, not just the word "abhi". "Abhi baat karte hain" is
# agreement; "abhi busy hoon" is not. So `abhi` alone never defers — it must sit with another
# cue, or with a negation.
_DEFERRAL_STANDALONE = _DEFERRAL_WORDS - {"abhi", "अभी", "અત્યારે"}


def defers_the_call(text: str) -> bool:
    """Did they say "not right now"? Outbound only — inbound callers never do.

    Distinct from a refusal (`nahi`, `interest nahi`), which is a NO, and from an objection,
    which is a reason to talk more. A deferral means the call is over for now and hard rule 6
    applies: offer one alternative, then close warmly. Continuing into discovery over someone
    who has just said they are driving is the fastest way to make Weltec the institute that
    rang while they were driving and would not stop talking.
    """
    toks = set(tokens(text))
    if not toks:
        return False
    if toks & _DEFERRAL_STANDALONE:
        return True
    # "abhi nahi", "abhi nahi ho payega" — the time word plus a negation.
    return bool(toks & {"abhi", "अभी", "અત્યારે"} and toks & _NEGATIONS)


_TIME_WORDS = {
    "baje",
    "baja",
    "bajey",
    "vagye",
    "vage",
    "oclock",
    "am",
    "pm",
    "बजे",
    "વાગ્યે",
    "વાગે",
    "subah",
    "savare",
    "sawere",
    "dopahar",
    "bapore",
    "shaam",
    "sham",
    "saanjhe",
    "saanje",
    "raat",
    "raatre",
    "morning",
    "afternoon",
    "evening",
    "night",
    "सुबह",
    "दोपहर",
    "शाम",
    "रात",
    "સવારે",
    "બપોરે",
    "સાંજે",
    "રાત્રે",
    "aaj",
    "aaje",
    "kal",
    "kaal",
    "kaale",
    "parso",
    "today",
    "tomorrow",
    "आज",
    "कल",
    "परसों",
    "આજે",
    "કાલે",
    "આવતીકાલે",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "somvaar",
    "somvar",
    "mangalvaar",
    "mangalvar",
    "budhvaar",
    "budhvar",
    "guruvaar",
    "guruvar",
    "shukravaar",
    "shukravar",
    "shanivaar",
    "shanivar",
    "ravivaar",
    "ravivar",
    "સોમવારે",
    "મંગળવારે",
    "બુધવારે",
    "ગુરુવારે",
    "શુક્રવારે",
    "શનિવારે",
    "રવિવારે",
    "મંડે",
    "ટ્યુઝડે",
    "વેનસડે",
    "થર્સડે",
    "ફ્રાઈડે",
    "સેટરડે",
    "સનડે",
    "सोमवार",
    "मंगलवार",
    "बुधवार",
    "गुरुवार",
    "शुक्रवार",
    "शनिवार",
    "रविवार",
}


_CONTRAST_MARKERS = {
    "lekin",
    "magar",
    "par",
    "parantu",
    "but",
    "phir",
    "fir",
    "toh",
    "to",
    "लेकिन",
    "मगर",
    "परंतु",
    "पर",
    "फिर",
    "લેકિન",
    "પણ",
    "પરંતુ",
    "બટ",
    "તો",
}


def _has_time_evidence(toks: set) -> bool:
    """Does the utterance actually contain a day or a clock time?

    A digit counts: "3", "11", "5:30" are how a time arrives when the lead skips the word
    for o'clock. `tokens()` is the Indic-safe tokenizer, so `isdigit` is only ever asked
    about a whole token.
    """
    return bool(toks & _TIME_WORDS) or any(t.isdigit() for t in toks)


def _is_refusal_only(text: str) -> bool:
    """A flat negation — no day, no time, and no "but" — which cannot be an acceptance.

    ## The booking this exists to stop

    Live call CA9933275 (2026-07-27). Roma had offered Monday 11 AM and Monday 5 PM. The
    lead said `નહીં નહીં કોઈ દૂસરા item ના દો` — "no no, don't give me another one" — and
    the extractor returned `accepted=True chose_offer=1 confidence=1.00`. The machine
    recorded Monday 11 AM as accepted, moved to P7, and Roma read the booking back. The
    lead had refused; three utterances later they were still refusing.

    Structured output does not make a model's judgement true, and `accepted` had just been
    widened to include a stated intention to come — which is right for "kal aa jaunga" and
    catastrophic when the model over-reaches. So the win condition gets the same code-level
    veto the P1/P7 affirmation detector already has (`is_affirmation`): a negation anywhere
    wins outright unless the lead also put a day or a time on the table.

    Three things open the gate, and between them they are what a real acceptance carries: a
    day, a clock time, or a contrast marker scoping the negation to an earlier clause.

    ORDINALS ARE NOT EVIDENCE, on purpose. `દૂસરા` ("another"/"the second") is exactly the
    word `chose_offer` is taught to read as an ordinal pick, and it is also the word in the
    sentence above. Counting it would re-open the hole this closes. The cost is that a terse
    "nahi, doosra wala" is vetoed and Roma re-asks — which is the trade docs/03 already
    makes everywhere else on this path: a re-ask is cheap, a phantom booking is the worst
    outcome of the call (hard rule nine).
    """
    if not text:
        return False
    toks = set(tokens(text))
    if not toks & _NEGATIONS:
        return False
    return not (_has_time_evidence(toks) or toks & _CONTRAST_MARKERS)


def _parse_offers(iso_slots) -> list:
    """`state.slots_offered` as datetimes. Unparseable entries are dropped, not raised on —
    a bad checkpoint must degrade to "no offers", never take down the turn."""
    out = []
    for iso in iso_slots or []:
        try:
            out.append(datetime.fromisoformat(iso))
        except (TypeError, ValueError):
            _log.warning("unparseable offered slot in call state; ignoring it")
    return out


def _anchor_day(state: CallState):
    """`state.pending_day` as a date, or None. A malformed value is ignored, never raised
    on — a bad checkpoint must cost the anchor, not the turn."""
    if not state.pending_day:
        return None
    try:
        return date.fromisoformat(state.pending_day)
    except (TypeError, ValueError):
        _log.warning("unparseable pending_day in call state; ignoring it")
        return None


def _sole_offer_day(iso_slots) -> "str | None":
    """The one date every offered slot falls on, ISO, or None if they disagree.

    Roma proposing two times is what puts a day on the table — the lead then answers with a
    bare hour, because the day is already settled between them. So the offers are the day
    under discussion, and `advance_turn` seeds `pending_day` from this.

    Unanimity is required, and it is not a formality. `StaticHoursCalendar.offers()` walks
    forward from `now`, so at midday it returns TODAY 17:00 and TOMORROW 11:00 — against
    those, "4 baje" genuinely is ambiguous, and the honest answer is no anchor and a
    re-ask, not a coin toss on the lead's appointment.
    """
    days = set()
    for iso in iso_slots or []:
        try:
            days.add(datetime.fromisoformat(iso).date())
        except (TypeError, ValueError):
            return None
    return days.pop().isoformat() if len(days) == 1 else None


def _corroborate(ts, user_text: str):
    """Floor the extractor's confidence when the lead's own words carry a day or a time.

    ## The turn this exists to resolve

    Live call CA4777470 (2026-07-27). The lead said `4 બજે` — "four o'clock" — three times.
    Every turn:

        slot verdict: phase=p5_pivot reason=unclear ... hour=4 anchor=- resolved=- status=none

    `hour=4` was extracted correctly. It was thrown away because the model reported
    `confidence=0.50`, under `CONFIDENCE_THRESHOLD` (0.70). Nothing about that utterance was
    unclear; the model was simply hedging.

    That threshold exists to stop a slot the model INVENTED FROM NOTHING from reaching the
    win condition (docs/03: "a confidently-wrong slot burns the lead"). It is the wrong
    instrument for a model under-confident about words that were plainly spoken — and the
    transcript is available right here to say which case this is.

    So the floor needs corroboration from two independent places: the model produced
    structure, AND the lead's own tokens contain a day or a clock time. Neither is the
    model's opinion of itself. A transcript that independently contains "4" and "બજે" is
    stronger evidence a time was said than any float the model reports about it.

    This cannot re-open the phantom booking of CA9933275: `નહીં નહીં કોઈ દૂસરા item ના દો`
    carries no time evidence, so it is never floored — and `_is_refusal_only` vetoes the
    claim regardless. The residual exposure is STT mishearing a time into existence, which
    is exactly the exposure a self-reported 0.9 already carries, and the P7 readback is the
    designed net for it: the lead hears the time and confirms it before anything locks.
    """
    if ts.confidence >= CONFIDENCE_THRESHOLD:
        return ts
    if ts.hour is None and not ts.weekday and ts.day_offset is None and ts.chose_offer is None:
        return ts
    if not _has_time_evidence(set(tokens(user_text))):
        return ts
    _log.info("slot confidence floored: the transcript itself carries a day or a time")
    return ts.model_copy(update={"confidence": CONFIDENCE_THRESHOLD})


def _spoken_offers(state: CallState) -> list:
    """The offers as Roma said them, for the extractor's user message. Same rendering the
    prompt uses, so the model matches against the words the lead actually heard."""
    return [s for s in (spoken_slot(iso) for iso in state.slots_offered) if s]


def _close_turn(
    state: CallState, ts, verdict, claimed: bool, user_text: str, sig: dict
) -> None:
    """The P7 readback turn: revise, lock, or hold. Order is load-bearing.

    REVISION IS CHECKED FIRST. A lead who says "haan, Monday theek rahega" at the readback
    is changing the day, not confirming Tuesday — but `is_affirmation` fires on "haan", so a
    confirm-first ordering locks the OLD slot and reports a win against a time the lead just
    rejected. Previously neither arm matched this at all: the elif required a confirm, so
    `accepted_slot` was never updated and `slot_status` still read "accepted" for the stale
    time.
    """
    if claimed and verdict.slot is not None and verdict.slot.isoformat() != state.accepted_slot:
        _log.info("p7: slot revised %s -> %s", state.accepted_slot, verdict.slot.isoformat())
        state.accepted_slot = verdict.slot.isoformat()
        state.slot_status = "accepted"
        return

    # A revision the machine could NOT resolve is still a revision. Call fa3ae274: the lead
    # answered the Tuesday 11:00 readback with "मैंने बोला Wednesday को छह बजे का चार बजे का
    # slot". Wednesday 18:00 is outside the 10-18 window, so the verdict came back
    # `out_of_hours` with slot=None, the arm above needs a resolved slot and skipped — and
    # the same sentence contains "ठीक है", so `is_affirmation` fired and LOCKED TUESDAY.
    # She then told the lead their Tuesday visit was confirmed and the call reported won=True.
    #
    # Naming any time at the readback means the lead is not agreeing to the one on the table.
    # Surface why it failed so the fragment can re-offer; never fall through to the lock.
    if claimed and verdict.slot is None:
        _log.info(
            "p7: unresolved revision (%s) — holding, not locking %s",
            verdict.reason,
            state.accepted_slot,
        )
        state.slot_status = verdict.reason
        return

    if (ts.readback_confirmed or is_affirmation(user_text)) and state.accepted_slot is not None:
        state.locked_slot = state.accepted_slot
        state.slot_status = "locked"
        sig["readback_confirmed"] = True
        return

    if state.accepted_slot is None:
        state.slot_status = verdict.reason if claimed else "none"


async def advance_turn(
    state: CallState,
    user_text: str,
    *,
    client=None,
    now,
    store=None,
    elapsed_secs: float = 0.0,
    calendar: VisitCalendar = DEFAULT_CALENDAR,
    extract_discovery=extract_discovery_slot,
    extract_time=extract_time_slot,
    classify=classify_objection,
) -> Transition:
    """Advance the call one user turn: mutate `state` in place, return the machine's
    `Transition` (carrying `win`/`hard_pivot`). Checkpoints to `store` on durable events.

    `elapsed_secs` is seconds since the call connected. It drives the five-minute budget
    (`roma.domain.conversation.pacing`): it rides into the phase prompt as `{{pacing}}`, and past
    three minutes it can force the machine forward to the booking. Defaults to 0.0 so
    every existing caller and test keeps the old behaviour exactly.
    """
    state.elapsed_secs = elapsed_secs
    # Same `now` the slot resolver uses, so the prompt's "today" and the machine's "today"
    # can never disagree (live call e3237622: she called a Saturday "Wednesday").
    state.today_iso = now.date().isoformat()
    state.turn_count += 1
    state.phase_turn_count += 1
    phase = state.phase

    # Checked in EVERY phase, not just P1: a lead can ask the call to stop at any point, and
    # on 049f0dc1 they did it in P3. Sticky by assignment — once true it is never cleared.
    if not state.lead_wants_out and lead_wants_out(user_text):
        state.lead_wants_out = True
        _log.info("lead asked to end the call; sign-off guard will not hold (phase=%s)", phase)

    sig: dict = {}
    slot_filled = False
    lock_happened = False
    objection_recorded = False
    offers_changed = False

    def _record_objection(text: str) -> None:
        nonlocal objection_recorded
        obj = classify(text)
        if obj is not None:
            state.objection_counts[obj.value] = state.objection_counts.get(obj.value, 0) + 1
            sig["objection"] = obj.value
            objection_recorded = True
            _log.info(
                "objection: %s (count=%d, phase=%s)",
                obj.value,
                state.objection_counts[obj.value],
                phase,
            )

    # Counted for every phase, before the per-phase branches: the lead can start pushing for
    # a time in P3 and still be pushing in P4, and a counter that only ran inside one branch
    # would reset at the phase boundary — which is exactly where 0f09c8a3 crossed.
    if raises_the_visit_time(user_text):
        state.lead_time_asks += 1
    else:
        state.lead_time_asks = 0

    # Two routes to the same pivot, at different confidence. An explicit "visit schedule kar
    # dijiye" needs no confirmation and fires at once; a bare clock word waits for a second
    # turn, because "subah ki batch hoti hai kya?" is a course question.
    #
    # Neither fires while the lead is leaving. "Baad mein milte hain" carries a booking word
    # and a deferral carries a time, and answering either with a slot is precisely the push
    # hard rule 6 forbids.
    # Read from the text directly, not from `sig`: `declined_now` is set inside the P1 branch
    # BELOW this point, so a sig lookup here is always False.
    #
    # A DEFERRAL vetoes the weak route only. `defers_the_call` fires on "meeting", because
    # "abhi meeting mein hoon" is how a busy lead says so — and on 56504a23 that veto beat
    # "toh ab hamari meeting schedule fix karo", an explicit ask to book. `wants_to_book` now
    # requires a booking verb before it trusts that word, so when it fires it outranks the
    # deferral reading of the same sentence.
    #
    # `lead_wants_out` still vetoes everything. It matches consecutive dismissal phrases and
    # is the one signal precise enough to override a request to book.
    leaving = state.lead_wants_out or lead_wants_out(user_text)
    weak_route = state.lead_time_asks >= LEAD_TIME_ASKS_BEFORE_PIVOT and not defers_the_call(
        user_text
    )
    sig["asks_to_book"] = not leaving and (wants_to_book(user_text) or weak_route)
    if sig["asks_to_book"] and phase not in (P5_PIVOT, P7_CLOSE):
        # States the SIGNAL, not the outcome. This line used to say "pivoting to the offer"
        # and said it twice on bde258d1 while the machine was refusing to pivot out of P1 —
        # a log that reports an intention as a fact hides the bug it should have exposed.
        # Whether the pivot happened is visible in the phase change that follows.
        _log.info("lead asked to book (phase=%s)", phase)

    if phase == P1_OPEN:
        # A caller asking who picked up holds P1 whatever else the reply contains — "haan,
        # Weltec hai?" is an affirmation AND a question, and the question is the part that
        # matters. P1 answers it; P2 would ask them their name instead.
        # Outbound adds a third answer that inbound never had. Inbound, anything substantive
        # meant "I rang you, here is why" and advancing was right. Outbound, WE interrupted
        # THEM, so "abhi meeting mein hoon, baad mein call karo" is substantive, is not a
        # wrong number, and is emphatically not permission — `opened_the_conversation` returns
        # True for it and would walk a busy person straight into five discovery questions.
        # Hard rule 6: one alternative, then close. So a deferral holds P1.
        # ...unless they asked to BOOK. "Meeting fix karo" matches `defers_the_call` (the
        # word "meeting" genuinely carries both senses), and P1 was the one phase where that
        # collision still won: `machine.next_phase` lists P1_OPEN among the phases a booking
        # request may pivot from, but gates it on `not declined_now`, so the readiest lead on
        # the list — someone answering "kya abhi 2 minute baat ho sakti hai?" with "book me a
        # meeting" — was read as hanging up and held in P1.
        #
        # Same precedence the pivot already applies everywhere else: an EXPLICIT ask to book
        # outranks the deferral lexicon it collides with. A deferral that is not also a
        # booking request still holds P1, which is the compliance case this flag exists for.
        sig["declined_now"] = defers_the_call(user_text) and not sig["asks_to_book"]
        sig["inquiry_confirmed"] = (
            not asks_who_we_are(user_text)
            and not sig["declined_now"]
            and (is_affirmation(user_text) or opened_the_conversation(user_text))
        )

    elif phase == P2_DISCOVER:
        sig["asks_about_course"] = asks_about_course(user_text)
        slot_name = state.next_discovery_slot()
        if slot_name and client is not None:
            dv = await extract_discovery(client, user_text, slot_name)
            if dv.value is not None and dv.confidence >= CONFIDENCE_THRESHOLD:
                setattr(state, slot_name, dv.value)
                sig["discovery_slot_filled"] = True
                slot_filled = True
            else:
                # Nothing usable came back. Count it, so a slot the caller will not answer
                # cannot pin the pointer for the rest of P2 (state.SLOT_ATTEMPT_CAP).
                state.record_slot_attempt(slot_name)
                if state.slot_attempts[slot_name] >= SLOT_ATTEMPT_CAP:
                    _log.info(
                        "discovery slot given up on after %d tries: %s",
                        SLOT_ATTEMPT_CAP,
                        slot_name,
                    )

    elif phase in (P5_PIVOT, P7_CLOSE):
        if client is not None:
            offered = _parse_offers(state.slots_offered)
            ts = await extract_time(client, user_text, offered=_spoken_offers(state))
            ts = _corroborate(ts, user_text)
            verdict = resolve_visit_slot(
                ts, now, offered=offered, anchor_day=_anchor_day(state)
            )
            claimed = ts.accepted or ts.chose_offer is not None

            said_a_time = _has_time_evidence(set(tokens(user_text)))
            if not claimed and verdict.reason == "ok" and said_a_time:
                _log.info("claim inferred: the lead named a bookable time (phase=%s)", phase)
                claimed = True

            if claimed and _is_refusal_only(user_text):
                _log.info("claim vetoed: refusal with no day or time in it (phase=%s)", phase)
                claimed = False
                verdict = SlotVerdict("unclear")

            if verdict.day is not None:
                state.pending_day = verdict.day.isoformat()

            if phase == P5_PIVOT:
                if claimed and verdict.slot is not None:
                    state.accepted_slot = verdict.slot.isoformat()
                    state.slot_status = "accepted"
                    sig["slot_accepted"] = True
                else:
                    state.slot_status = verdict.reason if (claimed or said_a_time) else "none"

                if verdict.day is not None:
                    pinned = [d.isoformat() for d in await calendar.offers(now, on=verdict.day)]
                    if pinned and pinned != state.slots_offered:
                        state.slots_offered = pinned
                        offers_changed = True
                        _log.info("p5 re-offer on %s: %s", verdict.day, ",".join(pinned))
            elif phase == P7_CLOSE:
                _close_turn(state, ts, verdict, claimed, user_text, sig)
                lock_happened = sig.get("readback_confirmed", False)

            _log.info(
                "slot verdict: phase=%s reason=%s accepted=%s chose_offer=%s conf=%.2f "
                "day_offset=%s weekday=%s%s hour=%s anchor=%s resolved=%s status=%s",
                phase,
                verdict.reason,
                ts.accepted,
                ts.chose_offer,
                ts.confidence,
                ts.day_offset,
                ts.weekday,
                "(BOTH)" if ts.weekday and ts.day_offset is not None else "",
                ts.hour,
                state.pending_day or "-",
                verdict.slot.isoformat() if verdict.slot else "-",
                state.slot_status,
            )
            _log.debug("slot verdict text: %r", user_text)

    if not (phase == P2_DISCOVER and slot_filled):
        _record_objection(user_text)

    transition = transition_state(state, TurnSignals(**sig))

    if should_force_pivot(elapsed_secs, transition.next_phase):
        _log.info(
            "pacing: %.0fs elapsed (%s) — forcing %s → p5_pivot",
            elapsed_secs,
            band(elapsed_secs),
            transition.next_phase,
        )
        transition = Transition(P5_PIVOT, hard_pivot=True)

    changed = transition.next_phase != state.phase
    if changed:
        state.phase = transition.next_phase
        state.phase_turn_count = 0

    if state.phase == P5_PIVOT and not state.slots_offered:
        state.slots_offered = [d.isoformat() for d in await calendar.offers(now)]
        offers_changed = True
        state.pending_day = _sole_offer_day(state.slots_offered)
        _log.info(
            "p5 offers: %s (anchor=%s)",
            ",".join(state.slots_offered) or "(none available)",
            state.pending_day or "-",
        )

    if store is not None and (
        changed or slot_filled or lock_happened or objection_recorded or offers_changed
    ):
        try:
            await save_state(store, state)
        except Exception:  # noqa: BLE001 — a checkpoint is best-effort, the turn is not
            _log.warning("call-state checkpoint failed; continuing (phase=%s)", state.phase)

    return transition


__all__ = ["advance_turn", "is_affirmation"]
