"""P6 objection classifier — a lexicon, NOT a model (docs/03).

docs/03 is explicit: the objection classifier is "a small fast call or a lexicon — NOT
the main LLM deciding its own state." We use a lexicon (dict + token match), mirroring
`roma.domain.safety.lexicon`: zero latency, deterministic, editable as text, and it keeps
state-selection out of the model's hands.

The starter taxonomy is the Proposed-7 (product-approved; editable here as calls come in).
It classifies the LEAD's utterance — distinct from the guardrails filter, which screens
ROMA's output — so an overlap like "placement"/"salary" is expected and correct.

Matching is token-equality after `guardrails.normalize` (so 'fee' never fires inside
'coffee'); multi-word cues are contiguous token subsequences. `classify_objection` returns
the first category (in CHECK_ORDER) that fires, or None, and never raises.
"""

from enum import Enum

from roma.domain.safety.normalize import tokens as _tokens


class Objection(Enum):
    COST = "cost"
    NO_TIME = "no_time"
    ASK_FAMILY = "ask_family"
    DISTANCE = "distance"
    MODE = "mode"
    PLACEMENT_DOUBT = "placement_doubt"
    THINK_ABOUT_IT = "think_about_it"


OBJECTION_KEYWORDS: dict[Objection, set[str]] = {
    Objection.COST: {
        "mehenga",
        "mehanga",
        "mahanga",
        "expensive",
        "costly",
        "kharch",
        "महंगा",
        "महँगा",
        "खर्च",
        "મોંઘું",
        "મોંઘુ",
        "mongu",
        "ખર્ચ",
        "ખર્ચો",
        "fee",
        "fees",
        "फीस",
        "ફી",
        "ફીસ",
    },
    Objection.NO_TIME: {
        "busy",
        "vyast",
        "व्यस्त",
        "વ્યસ્ત",
        "નવરાશ",
    },
    Objection.ASK_FAMILY: {
        "family",
        "parents",
        "papa",
        "mummy",
        "pati",
        "patni",
        "husband",
        "wife",
        "gharwale",
        "परिवार",
        "पापा",
        "पति",
        "घरवाले",
        "પરિવાર",
        "પપ્પા",
        "પતિ",
        "પત્ની",
        "ઘરવાળા",
    },
    Objection.DISTANCE: {
        "dur",
        "far",
        "distance",
        "travel",
        "दूर",
        "દૂર",
        "છેટે",
        "આઘું",
    },
    Objection.MODE: {
        "remote",
    },
    Objection.PLACEMENT_DOUBT: {
        "guarantee",
        "गारंटी",
        "ગેરંટી",
    },
    Objection.THINK_ABOUT_IT: {
        "sochenge",
        "sochunga",
        "sochkar",
        "later",
        "baadme",
        "सोचेंगे",
        "सोचूंगा",
        "વિચારીશ",
        "વિચારીને",
        "જોઈને",
    },
}

OBJECTION_PHRASES: dict[Objection, list[list[str]]] = {
    Objection.COST: [
        ["fees", "zyada"],
        ["paise", "nahi"],
        ["budget", "nahi"],
        ["પૈસા", "નથી"],
        ["પૈસા", "નહીં"],
        ["બજેટ", "નથી"],
        ["પૈસા", "ની", "તકલીફ"],
        ["पैसे", "नहीं"],
        ["बजट", "नहीं"],
    ],
    Objection.NO_TIME: [
        ["time", "nahi"],
        ["time", "nahin"],
        ["waqt", "nahi"],
        ["samay", "nahi"],
        ["टाइम", "नहीं"],
        ["સમય", "નથી"],
        ["ટાઈમ", "નથી"],
    ],
    Objection.ASK_FAMILY: [
        ["ghar", "mein", "baat"],
        ["ghar", "pe", "puchke"],
        ["family", "se", "puch"],
        ["puchke", "bataunga"],
        ["puchke", "bataungi"],
        ["ઘરે", "વાત"],
        ["પૂછીને", "કહીશ"],
        ["ઘરે", "પૂછીને"],
        ["ઘરવાળા", "ને", "પૂછી"],
    ],
    Objection.DISTANCE: [
        ["bahut", "door"],
        ["itni", "door"],
        ["aana", "mushkil"],
        ["બહુ", "દૂર"],
        ["આવવું", "અઘરું"],
    ],
    Objection.MODE: [
        ["online", "ho", "sakta"],
        ["online", "ho", "sakti"],
        ["ghar", "se", "online"],
        ["online", "nahi", "ho"],
        ["online", "kar", "sakte"],
        ["online", "class"],
        ["ઓનલાઇન", "થાય"],
        ["ઓનલાઇન", "થઈ"],
        ["ઓનલાઇન", "ચાલે"],
        ["ઓનલાઇન", "કરી"],
        ["ઓનલાઇન", "ના", "થાય"],
        ["ઓનલાઇન", "શક્ય"],
        ["ઓનલાઇન", "ક્લાસ"],
        ["ऑनलाइन", "हो", "सकता"],
        ["ऑनलाइन", "हो", "सकती"],
        ["ऑनलाइन", "क्लास"],
    ],
    Objection.PLACEMENT_DOUBT: [
        ["job", "milegi"],
        ["naukri", "milegi"],
        ["placement", "milega"],
        ["જોબ", "મળશે"],
        ["નોકરી", "મળશે"],
    ],
    Objection.THINK_ABOUT_IT: [
        ["dekhta", "hoon"],
        ["dekhti", "hoon"],
        ["dekhte", "hain"],
        ["soch", "ke"],
        ["baad", "mein"],
        ["think", "about"],
        ["let", "me", "think"],
        ["વિચારીને", "કહીશ"],
        ["પછી", "જોઈશ"],
    ],
}

CHECK_ORDER: tuple[Objection, ...] = (
    Objection.COST,
    Objection.PLACEMENT_DOUBT,
    Objection.NO_TIME,
    Objection.ASK_FAMILY,
    Objection.DISTANCE,
    Objection.MODE,
    Objection.THINK_ABOUT_IT,
)


PLACEMENT_TOPIC = {
    "placement",
    "placements",
    "naukri",
    "job",
    "jobs",
    "नौकरी",
    "જોબ",
    "નોકરી",
}

PLACEMENT_DOUBT_CUES = {
    "milega",
    "milegi",
    "milta",
    "milti",
    "hoga",
    "hogi",
    "pakka",
    "guarantee",
    "guaranteed",
    "sure",
    "chance",
    "nahi",
    "nahin",
    "kya",
    "kaise",
    "kitne",
    "मिलेगा",
    "मिलेगी",
    "पक्का",
    "गारंटी",
    "नहीं",
    "क्या",
    "મળશે",
    "મળે",
    "પાક્કુ",
    "ગેરંટી",
    "નહીં",
    "નથી",
    "કે",
}


def _is_placement_doubt(token_set: set) -> bool:
    """A placement TOPIC plus a DOUBT cue. Either alone is ordinary speech."""
    return bool(token_set & PLACEMENT_TOPIC) and bool(token_set & PLACEMENT_DOUBT_CUES)


def _has_phrase(tokens: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return any(tokens[i : i + n] == phrase for i in range(len(tokens) - n + 1))


def classify_objection(text: str) -> "Objection | None":
    """Return the lead's objection category, or None. Never raises (a classifier failure
    must not crash the turn — the machine simply sees 'no objection')."""
    try:
        if not text:
            return None
        tokens = _tokens(text)
        if not tokens:
            return None
        token_set = set(tokens)
        for cat in CHECK_ORDER:
            if any(_has_phrase(tokens, p) for p in OBJECTION_PHRASES.get(cat, [])):
                return cat
            if token_set & OBJECTION_KEYWORDS.get(cat, set()):
                return cat
            if cat is Objection.PLACEMENT_DOUBT and _is_placement_doubt(token_set):
                return cat
        return None
    except Exception:  # noqa: BLE001  # pragma: no cover - classifier must never raise
        return None


__all__ = ["Objection", "classify_objection", "OBJECTION_KEYWORDS", "OBJECTION_PHRASES"]
