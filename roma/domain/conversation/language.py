"""Deterministic caller-language evidence; never a production reply-language policy.

Scripts identify Indic text; small token vocabularies distinguish Latin transliteration
from English. Unknown/short acknowledgements retain context. Two matching informative
turns are required to change an established route. No transcript is retained or rewritten.
"""

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from roma.domain.safety.normalize import tokens


class Language(StrEnum):
    GUJARATI = "GUJARATI"
    HINDI = "HINDI"
    ENGLISH = "ENGLISH"
    GUJARATI_ENGLISH = "GUJARATI_ENGLISH"
    HINDI_ENGLISH = "HINDI_ENGLISH"


HISTORY_SIZE = 5
_DEFAULT = Language.HINDI_ENGLISH
_GUJARATI = frozenset(
    "hu hun mane mare maru tamne tame tamaru shu su kem kemcho cho chho chhu chu che "
    "chhe chheji nathi karvu karva karvo karvi joiye joie shikhvu shikhva avu aavish "
    "aavvu kyare ketla ketlu kyaare mara tamara vishe janvu janavsho samjavo "
    "samjavu chalse barabar saru majama hoy pan ma aa kyathi thase".split()
)
_HINDI = frozenset(
    "main mein mujhe muje mera meri mere aap aapka aapki aapko tum tumhara kya kyu "
    "kyun kaise kab kitna kitni kitne hai hain hoon hun nahi nahin karna karne karta "
    "karti karte chahiye seekhna sikhna batao bataye bataiye samjhaiye jaankari "
    "jaanna aana aaunga aayega padhai kar raha rahi aur lekin bahut achha accha".split()
)
# Shared words cannot decide between two Latin-script languages by themselves.
_SHARED = _GUJARATI & _HINDI
_GUJARATI = _GUJARATI - _SHARED
_HINDI = _HINDI - _SHARED
_ENGLISH = frozenset(
    "i me my we our you your would could can want need please explain tell about "
    "what when where why how is are am the this that these those to of and for with "
    "in on have has do does learn learning interested information details course "
    "classes class schedule duration weekend weekdays available prefer speak understand "
    "visit tomorrow today working student graduate completed study studying join".split()
)
# These remain byte-for-byte intact in the caller text and downstream wording. A lone
# borrowed term (e.g. SEO) never supplies enough evidence for an English/mixed switch.
_COURSE_TERMS = frozenset("seo google ads digital marketing".split())


def _language(value: object) -> Language | None:
    if not isinstance(value, str):
        return None
    try:
        return Language(value)
    except ValueError:
        return None


@dataclass
class LanguageRouterState:
    current: Language = _DEFAULT
    recent: list[Language] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.current = _language(self.current) or _DEFAULT
        recent = self.recent if isinstance(self.recent, (list, tuple)) else []
        self.recent = [
            language
            for item in recent[-HISTORY_SIZE:]
            if (language := _language(item)) is not None
        ]

    def to_dict(self) -> dict:
        return {
            "current": self.current.value,
            "recent": [language.value for language in self.recent[-HISTORY_SIZE:]],
        }

    @classmethod
    def from_dict(cls, data: object) -> "LanguageRouterState":
        if not isinstance(data, dict):
            return cls()
        return cls(current=data.get("current", _DEFAULT), recent=data.get("recent", []))


def _base(language: Language | None) -> Language | None:
    if language in (Language.GUJARATI, Language.GUJARATI_ENGLISH):
        return Language.GUJARATI
    if language in (Language.HINDI, Language.HINDI_ENGLISH):
        return Language.HINDI
    return None


def detect_language(text: str, *, previous: Language | None = None) -> Language | None:
    """Classify one informative utterance, or abstain on ambiguous/empty input.

    Script ratios select the Indic base, then English token evidence selects code-mix.
    Shared Latin acknowledgements and names are not evidence of a language switch.
    """
    words = set(tokens(text))
    if not words:
        return None
    counts = {Language.GUJARATI: 0, Language.HINDI: 0}
    for char in text:
        if unicodedata.category(char)[0] not in "LM":
            continue
        if "\u0a80" <= char <= "\u0aff":
            counts[Language.GUJARATI] += 1
        elif "\u0900" <= char <= "\u097f":
            counts[Language.HINDI] += 1
    total = sum(counts.values())
    base = None
    if total:
        dominant = max(counts, key=counts.__getitem__)
        # A near tie can be ASR script alternation; use the recent accepted base.
        base = dominant if counts[dominant] / total >= 0.6 else _base(previous) or dominant
    else:
        gujarati, hindi = len(words & _GUJARATI), len(words & _HINDI)
        if max(gujarati, hindi) >= 2 and gujarati != hindi:
            base = Language.GUJARATI if gujarati > hindi else Language.HINDI
        elif gujarati and hindi and _base(previous):
            base = _base(previous)
    english = words & _ENGLISH
    borrowed = words & _COURSE_TERMS
    if base:
        if len(english) >= 2 or len(borrowed) >= 2:
            return (
                Language.GUJARATI_ENGLISH
                if base == Language.GUJARATI
                else Language.HINDI_ENGLISH
            )
        return base
    return Language.ENGLISH if len(english) >= 2 else None


def route_language(state: LanguageRouterState, text: str) -> Language:
    """Observe one finalized caller turn; keep only bounded transcript-free evidence."""
    detected = detect_language(text, previous=state.current)
    if detected is None:
        return state.current
    fresh = not state.recent
    state.recent.append(detected)
    del state.recent[:-HISTORY_SIZE]
    if fresh or (len(state.recent) >= 2 and state.recent[-2] == detected):
        state.current = detected
    return state.current


__all__ = [
    "HISTORY_SIZE",
    "Language",
    "LanguageRouterState",
    "detect_language",
    "route_language",
]
