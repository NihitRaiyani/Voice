"""Small public-speech smoke scoring; no network, credentials or weights at import."""

import unicodedata
from collections.abc import Sequence


def normalized(text: str) -> str:
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("Smoke transcript exceeds the evaluation bound")
    text = unicodedata.normalize("NFC", text).casefold()
    if len(text) > 4096:
        raise ValueError("Normalized smoke transcript exceeds the evaluation bound")
    return " ".join(
        "".join(
            str(unicodedata.decimal(c))
            if c.isdecimal()
            else c
            if unicodedata.category(c)[0] in "LMN"
            else " "
            for c in text
        ).split()
    )


def edit_distance(a: Sequence, b: Sequence) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def score(reference: str, hypothesis: str) -> dict[str, int | float | None]:
    ref, hyp = normalized(reference), normalized(hypothesis)
    words, predicted = ref.split(), hyp.split()
    chars, predicted_chars = ref.replace(" ", ""), hyp.replace(" ", "")
    word_edits, char_edits = (
        edit_distance(words, predicted),
        edit_distance(chars, predicted_chars),
    )
    return {
        "reference_words": len(words),
        "word_edits": word_edits,
        "wer": word_edits / len(words) if words else 0.0 if not predicted else None,
        "reference_chars": len(chars),
        "char_edits": char_edits,
        "cer": char_edits / len(chars) if chars else 0.0 if not predicted_chars else None,
    }
