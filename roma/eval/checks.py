"""The four Step-7 assertions (docs/10): phase hits, word caps, slot extracted, filter
never leaks.

Its own module because this is the contract. `runner.py` is machinery — how a script gets
driven through the controller — and machinery gets rewritten. What a passing run MEANS
should not move when it does.

Every check returns a `Finding` list rather than raising. A run that stops at the first
failure tells you one thing about one script; a run that collects tells you the shape of
the regression, which is what a harness is for.
"""

from dataclasses import dataclass, field

from roma.controller.confirmguard import CONFIRMATION_CUES, REFUSAL_STATUSES
from roma.guardrails.filter import screen
from roma.guardrails.lexicon import HARD_FAIL_LINE, BlockCategory
from roma.guardrails.normalize import tokens
from roma.llm.prompts import PHASE_WORD_CAPS

CANARIES: tuple[tuple[str, BlockCategory], ...] = (
    ("Course ki फीस 25000 hai.", BlockCategory.FEE),
    ("અમારી ફી 30000 છે.", BlockCategory.FEE),
    ("Abhi ડિસ્કાઉન્ટ chal raha hai.", BlockCategory.DISCOUNT),
    ("Salary 5 લાખ tak jaati hai.", BlockCategory.SALARY),
    ("प्लेसमेंट 90 प्रतिशत hai.", BlockCategory.PLACEMENT),
    ("Fees ₹25000 hai.", BlockCategory.FEE),
    ("Package 6 lakh per annum tak.", BlockCategory.SALARY),
    ("Placement ratio 95% hai.", BlockCategory.PLACEMENT),
    ("Abhi 20% discount chal raha hai.", BlockCategory.DISCOUNT),
    ("Hum Google certified course dete hain.", BlockCategory.CERT),
)

ALLOWED_LINES: tuple[str, ...] = (
    "Wo ₹5000 fees nahi, offer message hai.",
    "No-cost EMI available hai.",
)


@dataclass(frozen=True)
class Finding:
    """One failed expectation. `check` groups them; `where` locates them."""

    check: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.where}: {self.detail}"


@dataclass
class TurnResult:
    """What one replayed turn produced, as the runner observed it."""

    index: int
    lead: str
    roma: str
    phase: str
    expect_phase: str = ""
    win: bool = False
    slot_status: str = "none"


@dataclass
class ScriptResult:
    """One script's replay, plus everything the checks found in it."""

    name: str
    turns: list[TurnResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    outcome: str = ""
    win: bool = False
    live: bool = False
    cost_inr: float = 0.0
    halted: str = ""

    @property
    def ok(self) -> bool:
        return not self.findings and not self.halted


def check_phase_hits(result: ScriptResult) -> list[Finding]:
    """The machine landed where the script says it should, every turn.

    Checked per turn, not just at the end: a call that reaches P7 by the wrong route has
    still mis-answered an objection or skipped discovery, and an end-state-only assertion
    cannot see that.
    """
    out = []
    for t in result.turns:
        if t.expect_phase and t.phase != t.expect_phase:
            out.append(
                Finding(
                    "phase-hits",
                    f"{result.name} turn {t.index}",
                    f"expected {t.expect_phase}, landed {t.phase} (lead: {t.lead!r})",
                )
            )
    return out


def check_word_caps(result: ScriptResult) -> list[Finding]:
    """Roma's line fits the phase's word cap (docs/11).

    The cap is a SHAPE target enforced by the prompt text, not a hard truncation — so this
    reports rather than aborts. Offline it is checking the fixtures are realistic; live it
    is checking the model, which is the only mode where it says anything about production.
    The phase used is the one the turn LANDED in, because that is the phase whose fragment
    and `max_tokens` produced the line.
    """
    out = []
    for t in result.turns:
        if not t.roma:
            continue
        cap = PHASE_WORD_CAPS.get(t.phase)
        if cap is None:
            continue
        n = len(t.roma.split())
        if n > cap:
            out.append(
                Finding(
                    "word-caps",
                    f"{result.name} turn {t.index} ({t.phase})",
                    f"{n} words over cap {cap}: {t.roma!r}",
                )
            )
    return out


def check_slots(
    result: ScriptResult, expect_slots: dict, expect_outcome: str, expect_win: "bool | None"
) -> list[Finding]:
    """The declared end-state was reached: slots filled, outcome classified, win correct.

    `locked_slot` is the win condition (docs/03 P7) and a soft "dekhta hoon" is not a win —
    so `expect_outcome` is asserted separately from the slots rather than inferred.
    """
    out = []
    for key, want in expect_slots.items():
        got = result.slots.get(key)
        if want is None:
            if got is not None:
                out.append(
                    Finding("slots", f"{result.name}", f"{key} should be unfilled, got {got!r}")
                )
        elif got != want:
            out.append(
                Finding("slots", f"{result.name}", f"{key}: expected {want!r}, got {got!r}")
            )
    if expect_outcome and result.outcome != expect_outcome:
        out.append(
            Finding(
                "slots",
                f"{result.name}",
                f"outcome: expected {expect_outcome!r}, got {result.outcome!r}",
            )
        )
    if expect_win is not None and result.win != expect_win:
        out.append(
            Finding("slots", f"{result.name}", f"win: expected {expect_win}, got {result.win}")
        )
    return out


def check_filter(result: ScriptResult) -> list[Finding]:
    """Nothing Roma says reaches TTS unscreened, and the filter still catches what it must.

    Two halves, because "the filter didn't block anything" is ambiguous between "the lines
    were clean" and "the filter is broken", and the session that produced this harness is
    exactly the second case wearing the first one's clothes.

    Half one: every replayed Roma line goes through `screen()`. A blocked line is reported —
    offline that means the fixture's reference line is unrealistic; live it means the model
    produced something that would have been substituted, which is a prompt problem worth
    seeing even though the filter did its job.

    Half two runs regardless of the script, in `check_canaries`.
    """
    out = []
    for t in result.turns:
        if not t.roma:
            continue
        verdict = screen(t.roma)
        if not verdict.allowed:
            out.append(
                Finding(
                    "filter-leak",
                    f"{result.name} turn {t.index}",
                    f"Roma's line would be substituted ({verdict.reason}, "
                    f"matched={list(verdict.matched)}): {t.roma!r}",
                )
            )
    return out


def check_no_phantom_confirmation(result: ScriptResult) -> list[Finding]:
    """Roma never tells a lead a visit is booked that the controller refused.

    The worst outcome of this call is not a lost booking — it is a lead who hangs up
    believing in one that does not exist. That happened live on 2026-07-26 (CAfa2a011):
    the lead asked for "kal subah chhe baje", `resolve_time_slot` refused 06:00 and
    `accepted_slot` stayed None, and Roma said "aapki visit confirm ho gayi kal subah chhe
    baje" three times before signing off. The teardown line read `won=False`. Every
    machine-side assertion in this harness was satisfied by that call; none of them looked
    at what Roma actually SAID about it, which is the only thing the lead experienced.

    Offline this checks the fixtures stay honest. Live it checks the model — and live is
    where it earns its place, because this is a prompt-adherence failure and prompts drift.
    """
    out = []
    for t in result.turns:
        if not t.roma or t.slot_status not in REFUSAL_STATUSES:
            continue
        hits = sorted(set(tokens(t.roma)) & CONFIRMATION_CUES)
        if hits:
            out.append(
                Finding(
                    "phantom-confirmation",
                    f"{result.name} turn {t.index}",
                    f"controller refused the slot ({t.slot_status}) but Roma's line "
                    f"confirms it (matched={hits}): {t.roma!r}",
                )
            )
    return out


def check_canaries() -> list[Finding]:
    """The filter blocks what it must, and permits the one allow-case.

    Script-independent on purpose: this must run even against an empty corpus, because it
    is the check that would have caught the tokenizer failing open.
    """
    out = []
    for line, expected in CANARIES:
        verdict = screen(line)
        if verdict.allowed:
            out.append(
                Finding(
                    "filter-canary",
                    expected.value,
                    f"LEAKED — filter allowed a {expected.value} line: {line!r}",
                )
            )
        elif verdict.category is not None and verdict.category != expected:
            out.append(
                Finding(
                    "filter-canary",
                    expected.value,
                    f"blocked as {verdict.category.value}, expected {expected.value}: {line!r}",
                )
            )
        elif verdict.safe_line == HARD_FAIL_LINE:
            out.append(
                Finding(
                    "filter-canary",
                    expected.value,
                    f"hard-failed instead of matching {expected.value} ({verdict.reason}): {line!r}",
                )
            )
    for line in ALLOWED_LINES:
        verdict = screen(line)
        if not verdict.allowed:
            out.append(
                Finding(
                    "filter-canary",
                    "allow-case",
                    f"OVER-BLOCKED ({verdict.reason}): {line!r}",
                )
            )
    return out


def check_offer_recorded(result: ScriptResult) -> list[Finding]:
    """Any call that reached P5 must have offers on record.

    A STRUCTURAL check, and it exists because of the failure mode no behavioural check
    catches: `state.slots_offered` was declared, checkpointed and rehydrated for four build
    steps while never being written by a single line of production code. Every script
    passed the whole time. Roma invented her slots each turn instead, so a lead who accepted
    one had nothing to be resolved against.

    Nothing here asserts WHICH slots — that is the calendar's business. It asserts only that
    the mechanism is connected, which is precisely what nobody was checking.
    """
    if not any(t.phase in ("p5_pivot", "p7_close") for t in result.turns):
        return []
    if result.slots.get("slots_offered"):
        return []
    return [
        Finding(
            "offer-recorded",
            result.name,
            "the call reached P5 but state.slots_offered is empty — Roma is inventing her "
            "own slots, so an accepted slot cannot be resolved",
        )
    ]


def run_checks(result: ScriptResult, script) -> list[Finding]:
    """Every per-script check. Canaries are run once per suite, not per script."""
    findings = []
    findings += check_phase_hits(result)
    findings += check_word_caps(result)
    findings += check_slots(
        result, script.expect_slots, script.expect_outcome, script.expect_win
    )
    findings += check_filter(result)
    findings += check_no_phantom_confirmation(result)
    findings += check_offer_recorded(result)
    return findings


__all__ = [
    "Finding",
    "TurnResult",
    "ScriptResult",
    "run_checks",
    "check_phase_hits",
    "check_word_caps",
    "check_slots",
    "check_filter",
    "check_no_phantom_confirmation",
    "check_offer_recorded",
    "check_canaries",
    "REFUSAL_STATUSES",
    "CANARIES",
    "ALLOWED_LINES",
]
