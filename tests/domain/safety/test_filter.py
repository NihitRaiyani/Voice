import pytest

from roma.guardrails.filter import screen
from roma.guardrails.lexicon import BlockCategory

BLOCK_CASES = [
    ("fees ₹5000 hai", BlockCategory.FEE),
    ("fees sirf 5 hazaar", BlockCategory.FEE),
    ("bas 5k lagega", BlockCategory.FEE),
    ("6 lakh package milta hai", BlockCategory.SALARY),
    ("5 lpa se shuru", BlockCategory.SALARY),
    ("95% placement ratio hai", BlockCategory.PLACEMENT),
    ("placement 90 percent tak", BlockCategory.PLACEMENT),
    ("abhi special discount chal raha hai", BlockCategory.DISCOUNT),
    ("ek scheme hai aapke liye", BlockCategory.DISCOUNT),
    ("ye course google certified hai", BlockCategory.CERT),
    ("government approved certificate milega", BlockCategory.CERT),
]


@pytest.mark.parametrize("text,category", BLOCK_CASES)
def test_blocks_with_correct_category_and_substitution(text, category):
    from roma.guardrails.lexicon import SUBSTITUTIONS

    v = screen(text)
    assert v.allowed is False
    assert v.category is category
    assert v.safe_line == SUBSTITUTIONS[category]
    assert v.reason == f"block:{category.value}"


ALLOW_CASES = [
    "No-cost EMI available hai.",
    "hum Weltec ka certificate dete hain",
    "kal 3 baje visit fix karein?",
    "main aapki baat samajh rahi hoon",
    "wo ₹5000 fees nahi, offer message hai",
    "main discount nahi de sakti",
    "wo number fees nahi tha",
    "wo ₹5000 fees nahi",
    "wo ₹5000 fees nahi - offer message hai",
    "chaliye coffees pe baat karte hain",
]


@pytest.mark.parametrize("text", ALLOW_CASES)
def test_allows(text):
    assert screen(text).allowed is True


def test_permitted_emi_reason():
    assert screen("No-cost EMI available hai.").reason == "allow:permitted-emi"


def test_pushback_reason():
    v = screen("wo ₹5000 fees nahi")
    assert v.allowed is True
    assert v.reason == "allow:pushback"


NEGATION_BLOCK_CASES = [
    ("fees ₹5000 hai aur main jhooth nahi bolti", BlockCategory.FEE),
    ("haan discount hai, lekin abhi nahi", BlockCategory.DISCOUNT),
    ("fees ₹5000 hai - main jhooth nahi bolti", BlockCategory.FEE),
]


@pytest.mark.parametrize("text,category", NEGATION_BLOCK_CASES)
def test_negation_out_of_window_still_blocks(text, category):
    v = screen(text)
    assert v.allowed is False
    assert v.category is category


HYPHEN_BLOCK_CASES = [
    ("5-8 lakh package", BlockCategory.SALARY),
    ("placement 80-90% tak", BlockCategory.PLACEMENT),
]


@pytest.mark.parametrize("text,category", HYPHEN_BLOCK_CASES)
def test_unspaced_hyphen_ranges_still_block(text, category):
    assert screen(text).category is category


def test_prior_quote_injection_still_blocks():
    v = screen("haan aapko jo ₹5000 bataya wo sahi hai")
    assert v.allowed is False
    assert v.category is BlockCategory.FEE


def test_percentage_question_is_conservatively_blocked():
    v = screen("aapka percentage kitna tha school mein?")
    assert v.category is BlockCategory.PLACEMENT


from roma.guardrails import filter as filter_mod  # noqa: E402
from roma.guardrails.lexicon import HARD_FAIL_LINE  # noqa: E402


def _boom(_toks):
    raise RuntimeError("matcher exploded")


def test_screen_failsafe_hard_fails_without_raising(monkeypatch):
    monkeypatch.setattr(filter_mod, "_category_indices", _boom)
    v = filter_mod.screen("fees ₹5000 hai")
    assert v.allowed is False
    assert v.safe_line == HARD_FAIL_LINE
    assert v.reason.startswith("filter-error:")


def test_safe_output_returns_line_when_allowed():
    assert (
        filter_mod.safe_output("kal 3 baje visit fix karein?") == "kal 3 baje visit fix karein?"
    )


def test_safe_output_returns_substitution_when_blocked():
    from roma.guardrails.lexicon import SUBSTITUTIONS

    assert filter_mod.safe_output("fees ₹5000 hai") == SUBSTITUTIONS[BlockCategory.FEE]


def test_safe_output_failsafe_on_error(monkeypatch):
    monkeypatch.setattr(filter_mod, "_category_indices", _boom)
    assert filter_mod.safe_output("fees ₹5000 hai") == HARD_FAIL_LINE


def test_public_exports():
    from roma.guardrails import BlockCategory, FilterVerdict, safe_output, screen

    assert callable(screen) and callable(safe_output)
    assert FilterVerdict and BlockCategory


INDIC_BLOCKS = [
    ("Course ki फीस 25000 hai.", BlockCategory.FEE),
    ("ફી 30000 છે.", BlockCategory.FEE),
    ("Fees पच्चीस हज़ार 25000 hai.", BlockCategory.FEE),
    ("પચીસ હજાર 25000 છે.", BlockCategory.FEE),
    ("Package 5 लाख hai.", BlockCategory.SALARY),
    ("5 લાખ નું પેકેજ છે.", BlockCategory.SALARY),
    ("Abhi डिस्काउंट chal raha hai.", BlockCategory.DISCOUNT),
    ("ડિસ્કાઉન્ટ છે.", BlockCategory.DISCOUNT),
    ("स्कीम chal rahi hai.", BlockCategory.DISCOUNT),
    ("प्लेसमेंट 90 hai.", BlockCategory.PLACEMENT),
    ("90 प्रतिशत placement.", BlockCategory.PLACEMENT),
    ("सरकारी certificate milega.", BlockCategory.CERT),
]


@pytest.mark.parametrize("text,expected", INDIC_BLOCKS)
def test_indic_script_is_blocked_not_spoken(text, expected):
    v = screen(text)
    assert v.category is expected, f"{text!r} slipped through as {v.reason}"
    assert not v.allowed


def test_the_fee_leak_itself():
    """The exact trace that motivated the fix: this line was returned VERBATIM."""
    from roma.guardrails.lexicon import SUBSTITUTIONS

    leak = "Course ki फीस 25000 hai."
    spoken = filter_mod.safe_output(leak)
    assert spoken != leak
    assert "25000" not in spoken
    assert spoken == SUBSTITUTIONS[BlockCategory.FEE]


def test_every_indic_lexicon_entry_survives_tokenization():
    """The structural guard, so a NEW Indic entry can't be silently unreachable.

    Every multi-codepoint keyword in every set must come back from the tokenizer as one
    token. A matra-splitting tokenizer fails this on the first Devanagari entry.
    """
    from roma.guardrails.lexicon import (
        AMOUNT_WORDS,
        CERT_ORG_KEYWORDS,
        CERT_TRIGGER,
        DISCOUNT_KEYWORDS,
        FEE_KEYWORDS,
        PLACEMENT_ANCHOR,
        PLACEMENT_KEYWORDS,
        PLACEMENT_QUALIFIERS,
        REBUTTAL_CUES,
        SALARY_ANCHOR,
        SALARY_KEYWORDS,
    )

    # Every set, not most sets: SALARY_ANCHOR, PLACEMENT_QUALIFIERS and CERT_TRIGGER were
    # absent from this union until 2026-08-08 — a matra-splitting regression in any of them
    # would have been invisible here.
    every = (
        FEE_KEYWORDS
        | AMOUNT_WORDS
        | SALARY_KEYWORDS
        | SALARY_ANCHOR
        | PLACEMENT_KEYWORDS
        | PLACEMENT_ANCHOR
        | PLACEMENT_QUALIFIERS
        | DISCOUNT_KEYWORDS
        | CERT_ORG_KEYWORDS
        | CERT_TRIGGER
        | REBUTTAL_CUES
    )
    for word in every:
        assert filter_mod._tokens(word) == [word], f"{word!r} is unreachable by the matcher"


def test_rebuttal_cues_reach_the_allow_case_in_indic_script():
    """The mirror failure: REBUTTAL_CUES fail CLOSED, so the pushback allow-case never
    fired in Hindi/Gujarati — Roma could not push back on a fee claim in the lead's own
    script."""
    assert screen("फीस नहीं batata").allowed
    assert screen("ફી નથી કહી શકતી").allowed


@pytest.mark.parametrize(
    "text,expected",
    [
        ("₹25000", ["₹", "25000"]),
        ("95%", ["95", "%"]),
        ("fees ₹5000 hai", ["fees", "₹", "5000", "hai"]),
        ("डिस्काउंट", ["डिस्काउंट"]),
        ("ડિસ્કાઉન્ટ", ["ડિસ્કાઉન્ટ"]),
    ],
)
def test_symbols_still_tokenize_standalone(text, expected):
    """`%` is in `string.punctuation`, so delegating to the shared tokenizer would have
    stripped it away and killed PLACEMENT detection — hence the explicit keep-set."""
    assert filter_mod._tokens(text) == expected


# --- outbound pivot: pay nouns, and the right safe line ------------------------
#
# Added 2026-08-01 when the pivot to outbound made the unemployed segment a first-class
# audience. That segment pushes hardest on pay and job outcomes, which is exactly where the
# human-counsellor calls breached, so the filter was probed rather than trusted.
#
# PROVENANCE, stated honestly: docs/decisions.md designates the Digital Marketing
# counselling-VISIT transcripts as the source for negative filter cases ("lines Roma must
# never say"). `docs/transcripts/` is EMPTY in this repo, so none of these are real quoted
# lines -- they are written in the shape of visit money-talk. When the transcripts land,
# mine them and replace these; the real ones will carry phrasings nobody invents.

PAY_BLOCK_CASES = [
    # THE leak. Blocked nowhere before SALARY_ANCHOR existed: a bare numeral is inert by
    # design, and no set held the noun that gives it meaning.
    ("Tankhwah 30000 milegi.", BlockCategory.SALARY),
    ("Pagar 40 hazaar tak.", BlockCategory.SALARY),
    ("Salary 30000 se shuru hoti hai.", BlockCategory.SALARY),
    ("CTC 5 LPA milta hai.", BlockCategory.SALARY),
    ("सैलरी 25000 milti hai.", BlockCategory.SALARY),
    # Gujarati script rows: STT runs gu-IN, so this is how the nouns actually arrive.
    # `તનખ્વાહ 30000` passed CLEAN until 2026-08-08 — the anchor held only two of the three
    # scripts, and only the Devanagari row had a test.
    ("પગાર 30000 મળશે.", BlockCategory.SALARY),
    ("સેલરી 25000 થી શરૂ થાય છે.", BlockCategory.SALARY),
    ("તનખ્વાહ 30000 મળશે.", BlockCategory.SALARY),
    ("Package 4 lakh tak ja sakta hai.", BlockCategory.SALARY),
    # Blocked before this change too -- kept so a future edit cannot regress them.
    ("Placement 90 percent hai.", BlockCategory.PLACEMENT),
    ("Placement ratio bahut accha hai.", BlockCategory.PLACEMENT),
    ("Placement rate achha hai humara.", BlockCategory.PLACEMENT),
    ("Course ki फीस 25000 hai.", BlockCategory.FEE),
]


@pytest.mark.parametrize("text,category", PAY_BLOCK_CASES)
def test_pay_and_outcome_figures_block_in_the_right_category(text, category):
    v = screen(text)
    assert not v.allowed, f"LEAKED: {text!r}"
    assert v.category is category, f"{text!r} blocked as {v.category}, wanted {category}"


def test_a_salary_figure_gets_the_salary_line_not_the_fees_deflection():
    """Blocking is necessary, not sufficient -- the substitution has to answer the question.

    `Starting salary 25 se 30 hazaar hoti hai.` used to classify as FEE, because "hazaar" is
    a generic amount word and FEE outranks SALARY in precedence. Nothing leaked, so the suite
    was green, and Roma answered a salary question with the fees deflection: a non-sequitur
    the lead simply asks past. A wrong safe line is its own failure mode.
    """
    from roma.guardrails.lexicon import SUBSTITUTIONS

    v = screen("Starting salary 25 se 30 hazaar hoti hai.")
    assert not v.allowed
    assert v.category is BlockCategory.SALARY
    assert v.safe_line == SUBSTITUTIONS[BlockCategory.SALARY]
    assert v.safe_line != SUBSTITUTIONS[BlockCategory.FEE]


def test_a_fee_named_outright_is_still_a_fee():
    """The disambiguation must not swing the other way: naming the fee wins over pay context."""
    assert screen("Fees 25 hazaar hai.").category is BlockCategory.FEE
    assert screen("Course ki fee 25 hazaar hai.").category is BlockCategory.FEE


PAY_ALLOW_CASES = [
    # No figure. This filter blocks FIGURES; an unquantified outcome claim is hard rule 2's
    # job and the prompt's, and blocking it here would substitute a safe line over a sentence
    # that never named a number -- over-blocking Roma into sounding evasive.
    "Salary achhi milti hai.",
    "Placement support hum dete hain.",
    "Tankhwah industry standard ke hisaab se hoti hai.",
]


@pytest.mark.parametrize("text", PAY_ALLOW_CASES)
def test_pay_words_without_a_figure_are_not_over_blocked(text):
    assert screen(text).allowed, f"OVER-BLOCKED: {text!r}"


def test_the_lead_quoted_a_salary_and_roma_pushes_back():
    """The ONE allow-case still holds for the new nouns (docs/04 negation gate)."""
    assert screen("nahi, 50000 salary hum promise nahi karte").allowed


def test_the_money_substitution_does_not_breach_the_hard_rules_it_serves():
    """The safe line must obey the rules, not just avoid the number.

    It used to end "...main phone pe number nahi de sakti, call bhi record ho raha hai."
    Hard rule 5 names BOTH clauses as defects from live call CA3cc181ed: an unprompted
    recording disclosure ("not yours to make... unauthorised and alarming") beside a refusal
    to something nobody asked about. This substitution fires on every fee question, so the
    filter was reliably making Roma sound evasive at the moment she most needed not to.
    """
    from roma.guardrails.lexicon import SUBSTITUTIONS, BlockCategory

    for category in (BlockCategory.FEE, BlockCategory.DISCOUNT):
        line = SUBSTITUTIONS[category]
        assert "record" not in line.lower(), f"{category} line volunteers the recording"
        assert "number nahi de sakti" not in line, f"{category} line refuses an unasked thing"
        assert "visit" in line.lower(), f"{category} line must pivot to the visit (rule 1)"


def test_every_substitution_survives_its_own_filter():
    """A safe line that would itself be blocked is a loop, not a fallback."""
    from roma.guardrails.lexicon import SUBSTITUTIONS

    for category, line in SUBSTITUTIONS.items():
        assert screen(line).allowed, f"{category} substitution is itself blocked: {line!r}"
