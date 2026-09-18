"""Normalization for the pre-TTS filter: NFC -> zero-width strip -> casefold ->
whitespace collapse -> brand-fold. No cross-script transliteration; the lexicon
lists the spellings we match (docs/04).

`tokens()` is the one tokenizer every lexicon matcher shares (guardrails filter,
P6 objection classifier, barge-in backchannel guard). A per-module private copy
would drift, and the Indic strip rule below is exactly the kind of subtlety that
only stays correct in one place."""

import re
import string
import unicodedata  # helps work with characters from every language

_ZERO_WIDTH = dict.fromkeys((0x200B, 0x200C, 0x200D, 0xFEFF), None)
_WS_RE = re.compile(r"\s+")  # remove space , tabs , newlines
_BRAND_RE = re.compile(r"\b(?:valtech|welltech|well-tech|weltech)\b")


def normalize(text: str) -> str:
    """Return the canonical form the matcher operates on."""
    if not text:
        return ""
    text = unicodedata.normalize(
        "NFC", text
    )  # a same word can be used in different forms so we need to normalize it to a single form
    text = text.translate(_ZERO_WIDTH)
    text = text.casefold()  # this is like text.lower() but much better
    text = _WS_RE.sub(" ", text).strip()
    text = _BRAND_RE.sub("weltec", text)  # every
    return text


STRIP_CHARS = string.punctuation + "।॥…“”‘’—"


# tokenization function. Its job is to convert a piece of text into a list of clean words. It does this by normalizing the text, splitting it on whitespace, and trimming edge punctuation. Empty tokens are dropped. The `keep` parameter allows certain characters to survive as standalone tokens instead of being trimmed away as edge punctuation.
def tokens(text: str, *, keep: str = "") -> list[str]:
    """Normalize, split on whitespace, trim edge punctuation. Empty tokens dropped.

    `keep` lists characters that survive as STANDALONE tokens instead of being trimmed
    away as edge punctuation. The pre-TTS filter needs bare `₹` and `%` as tokens (it
    reports their positions), and `%` is in `string.punctuation`, so without this it
    would be stripped to nothing and every percentage claim would go unnoticed.
    """
    out = []
    for raw in normalize(text).split():
        tok = raw.strip(STRIP_CHARS)
        if not tok and len(raw) == 1 and raw in keep:
            tok = raw
        if tok:
            out.append(tok)
    return out
