"""The enforced config for the pre-TTS filter (docs/04-guardrails.md).

This file is the single artifact the guardrail skill points at, so the rules are
loaded, not remembered. Keyword sets are matched by TOKEN EQUALITY after
normalization (so 'fees' never fires inside 'coffees'); symbols (₹, %) and numbers
are matched separately in filter.py.
"""

from enum import Enum


class BlockCategory(Enum):
    FEE = "FEE"
    SALARY = "SALARY"
    PLACEMENT = "PLACEMENT"
    DISCOUNT = "DISCOUNT"
    CERT = "CERT"


FEE_CURRENCY = {"₹", "rs", "rs.", "rupaye", "rupees", "rupee"}
FEE_KEYWORDS = {"fees", "fee", "फीस", "ફી"}
AMOUNT_WORDS = {"hazaar", "हज़ार", "હજાર"}

# Unit words that ARE the figure — they block on their own, no number needed, because
# "package lakhon mein hai" quotes an outcome just as surely as "4 lakh".
SALARY_KEYWORDS = {"lakh", "laakh", "लाख", "લાખ", "lpa", "package", "पैकेज"}
SALARY_PHRASES = [["per", "annum"]]

# Nouns that NAME pay without being a figure. Number-gated, exactly like FEE_KEYWORDS:
# "salary achhi milti hai" carries no number and is a prompt problem (hard rule 2), while
# "tankhwah 30000 milegi" is a quoted outcome and must never reach the wire.
#
# Added 2026-08-01. Probing the filter for the outbound pivot found `Tankhwah 30000 milegi.`
# passing CLEAN: a bare numeral is deliberately inert on its own, and no set held the noun
# that gives it meaning. The unemployed segment is the one that asks this question, and it
# is where the human-counsellor calls breached.
SALARY_ANCHOR = {
    "salary",
    "salaries",
    "सैलरी",
    "સેલરી",
    "tankhwah",
    "tankhwa",
    "तनख्वाह",
    # Gujarati spellings — STT runs gu-IN, so the noun arrives in this script on exactly the
    # calls this anchor was added for. `તનખ્વાહ 30000` passed CLEAN until 2026-08-08.
    "તનખ્વાહ",
    "તનખ્વા",
    "pagar",
    "पगार",
    "પગાર",
    "ctc",
}

PLACEMENT_KEYWORDS = {"percent", "percentage", "प्रतिशत"}
PLACEMENT_ANCHOR = {"placement", "प्लेसमेंट", "પ્લેસમેન્ટ"}

# docs/04 blocks "placement ratio" by name, with no number required — a ratio is a claimed
# outcome whether or not a figure follows, and "placement ratio bahut accha hai" was reaching
# the wire because the anchor only fired alongside a number.
PLACEMENT_QUALIFIERS = {"ratio", "rate", "record", "रेशियो"}

DISCOUNT_KEYWORDS = {"discount", "डिस्काउंट", "ડિસ્કાઉન્ટ", "offer", "scheme", "स्कीम"}

CERT_ORG_KEYWORDS = {"google", "meta", "ibm", "government", "govt", "सरकारी"}
CERT_TRIGGER = {"cert", "certificate", "certified", "certification"}


REBUTTAL_CUES = {"nahi", "nahin", "नहीं", "નથી", "નહીં", "mat", "not", "na"}
NEGATION_WINDOW = 3

PERMITTED_MONEY_LINE = "No-cost EMI available hai."


# Hard rule 1's own wording, copied rather than composed — the substitution and the rule
# must say the same thing or the filter fights the prompt.
#
# It previously read "...main phone pe number nahi de sakti, call bhi record ho raha hai."
# Hard rule 5 names BOTH of those clauses as defects, from live call CA3cc181ed: an unprompted
# recording disclosure ("not yours to make... unauthorised and alarming") next to a refusal to
# something nobody asked. This line fires on every fee question — the most common turn on the
# call — so the filter was reliably making Roma sound defensive at the exact moment she needed
# to sound helpful. A safe line that breaches the rules is not a safe line.
_MONEY_LINE = (
    "Fees aapki situation ke hisaab se counselling meeting mein discuss hogi — "
    "isiliye visit best rahega."
)
SUBSTITUTIONS = {
    BlockCategory.FEE: _MONEY_LINE,
    BlockCategory.DISCOUNT: _MONEY_LINE,
    BlockCategory.SALARY: (
        "Outcome aapke effort aur interview pe depend karta hai — hum skills aur "
        "placement support dete hain."
    ),
    BlockCategory.PLACEMENT: (
        "Placement support institute provide karta hai. Counsellor visit ke dauran complete placement process aur opportunities explain karenge."
    ),
    BlockCategory.CERT: (
        "Certificate ke baare mein counsellor visit pe detail batayenge — hum Weltec "
        "ka certificate dete hain."
    ),
}

HARD_FAIL_LINE = "Ye detail counsellor visit pe batayenge."

CERT_BLOCK_ENABLED = True
