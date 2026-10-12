"""Minimize recognized identifiers in model-bound text, never claim universal PII detection."""

import re

_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_HASH = re.compile(r"\b(?:[a-fA-F0-9]{32,128}|CA[a-fA-F0-9]{32})\b")
_LOCATION = re.compile(r"https?://\S+|(?:/Users/|/private/|var/roma/)\S+", re.I)


def redact_prompt(text: str, *, known_name: str | None = None) -> str:
    if known_name:
        text = re.sub(re.escape(known_name), "[caller]", text, flags=re.I)
    text = _PHONE.sub(
        lambda m: "[phone]" if sum(c.isdecimal() for c in m[0]) >= 10 else m[0], text
    )
    for pattern, label in (
        (_EMAIL, "[email]"),
        (_HASH, "[identifier]"),
        (_LOCATION, "[location]"),
    ):
        text = pattern.sub(label, text)
    return text
