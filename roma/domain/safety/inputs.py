"""Bound untrusted prompt data without pretending to solve semantic injection."""

import re
import unicodedata

CALLER_TEXT_LIMIT = 4096
SLOT_TEXT_LIMIT = 120
BRANCHES = frozenset({"Vadodara", "Ahmedabad"})
DISCOVERY_FIELDS = frozenset(
    {"lead_name", "current_status", "education", "passing_year", "city", "timing_constraint"}
)
_INJECTION = re.compile(
    r"\b(?:ignore|forget|override|disregard)\b.{0,60}\b(?:rules?|instructions?|system|prompt)\b|(?:नियम|निर्देश).{0,40}(?:भूल|अनदेखा)|(?:નિયમ|સૂચના).{0,40}(?:ભૂલી|અવગણ)",
    re.I,
)
_CONTROL_WORDS = re.compile(
    r"\b(?:ignore|override|disregard|system|developer|assistant|prompt|instructions?)\b|नियम.*(?:भूल|अनदेखा)|નિયમ.*(?:ભૂલી|અવગણ)",
    re.I,
)


def caller_text(text: str) -> str:
    if not isinstance(text, str) or len(text) > CALLER_TEXT_LIMIT:
        raise ValueError("Caller text exceeds the turn limit")
    text = unicodedata.normalize("NFC", text)
    if len(text) > CALLER_TEXT_LIMIT:
        raise ValueError("Caller text exceeds the turn limit")
    if any(unicodedata.category(c).startswith("C") or c in "<>{}`" for c in text):
        raise ValueError("Caller text contains unsupported characters")
    return text.strip()


def injection_attempt(text: str) -> bool:
    return bool(_INJECTION.search(text))


def slot_text(value: str | None, *, limit: int = SLOT_TEXT_LIMIT) -> str | None:
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > limit or _CONTROL_WORDS.search(value):
        return None
    if any(not (unicodedata.category(c)[0] in "LMN" or c in " .-_'’/(),&+:") for c in value):
        return None
    return value


def discovery_value(field: str, value: str | None) -> str | None:
    if field not in DISCOVERY_FIELDS:
        return None
    result = slot_text(value, limit=64 if field == "lead_name" else SLOT_TEXT_LIMIT)
    if field == "passing_year" and result is not None:
        return result if re.fullmatch(r"(?:19|20|21)[0-9]{2}", result) else None
    return result


def branch_value(value: str) -> str:
    return value if isinstance(value, str) and value in BRANCHES else "Vadodara"
