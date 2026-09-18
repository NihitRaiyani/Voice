"""The pre-TTS filter (docs/04-guardrails.md). Router, not censor: on a catch it
substitutes a safe line; on any internal error it hard-fails to a canned line.
Reads Roma's normalized line only. No model call."""

import logging
import re
from dataclasses import dataclass

from roma.domain.safety.lexicon import (
    AMOUNT_WORDS,
    CERT_BLOCK_ENABLED,
    CERT_ORG_KEYWORDS,
    CERT_TRIGGER,
    DISCOUNT_KEYWORDS,
    FEE_CURRENCY,
    FEE_KEYWORDS,
    HARD_FAIL_LINE,
    NEGATION_WINDOW,
    PERMITTED_MONEY_LINE,
    PLACEMENT_ANCHOR,
    PLACEMENT_KEYWORDS,
    PLACEMENT_QUALIFIERS,
    REBUTTAL_CUES,
    SALARY_ANCHOR,
    SALARY_KEYWORDS,
    SALARY_PHRASES,
    SUBSTITUTIONS,
    BlockCategory,
)
from roma.domain.safety.normalize import normalize, tokens

_log = logging.getLogger("roma.domain.safety")

_PRECEDENCE = [
    BlockCategory.FEE,
    BlockCategory.DISCOUNT,
    BlockCategory.SALARY,
    BlockCategory.PLACEMENT,
    BlockCategory.CERT,
]

_SYMBOL_SPLIT_RE = re.compile(
    r"([₹%])"
)  # this regex is used to split the text on the symbols ₹ and % so that they can be treated as separate tokens ['Fee ', '₹', '25000']
_CLAUSE_RE = re.compile(
    r"\s+-\s+|[,;।]"
)  # it’s used to split text into clauses ["Fee ₹25000. , Hostel available ; Placement support."]
_NK_RE = re.compile(
    r"\d+k"
)  # This matches: any number followed by the letter 'k', such as 10k, 20k, 100k, etc
_PERMITTED_NORM = normalize(PERMITTED_MONEY_LINE)


@dataclass(frozen=True)
class FilterVerdict:
    allowed: bool
    category: "BlockCategory | None"
    safe_line: "str | None"
    reason: str
    matched: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    """Tokenize via the ONE shared tokenizer (`normalize.tokens`), never a local regex.

    This function used to be `re.compile(r"[₹%]|\\w+").findall(text)`, and that was a
    silent hole straight through this filter. Python's `\\w` matches only characters where
    `str.isalnum()` is true, and Indic combining marks — matras, anusvara, virama (Unicode
    category `Mn`) — are NOT alnum. So `\\w+` shattered every Devanagari and Gujarati word
    at each matra: `फीस` became `['फ', 'स']`, `ડિસ્કાઉન્ટ` became seven fragments. Every
    Indic entry in `lexicon.py` was therefore unreachable, and
    `safe_output("Course ki फीस 25000 hai.")` returned the fee VERBATIM while the log said
    `allow:clean` — the one thing docs/04 and CLAUDE.md gate 1 say must never happen.
    Only the romanized spelling was ever caught.

    `normalize.tokens` strips an EXPLICIT punctuation set precisely so that letters and
    marks in every script survive (see `normalize.STRIP_CHARS`). Do not reintroduce a
    `\\w`- or `\\W`-based tokenizer here or anywhere else.
    """
    return tokens(
        _SYMBOL_SPLIT_RE.sub(r" \1 ", text), keep="₹%"
    )  # regex.sub(replacement, text)


def _clauses(norm: str) -> list[list[str]]:
    parts = [c for c in _CLAUSE_RE.split(norm) if c.strip()]
    if not parts:
        parts = [norm]
    return [_tokens(p) for p in parts]


def _is_number_tok(t: str) -> bool:
    return bool(re.search(r"\d", t)) or t in AMOUNT_WORDS


def _phrase_indices(toks: list[str], phrase: list[str]) -> list[int]:
    n = len(phrase)
    return [i for i in range(len(toks) - n + 1) if toks[i : i + n] == phrase]


def _category_indices(toks: list[str]) -> dict[BlockCategory, list[int]]:
    """Per-clause: category -> token indices of its signal(s)."""
    has_num = any(_is_number_tok(t) for t in toks)
    out: dict[BlockCategory, list[int]] = {}

    # Does this clause name PAY? "hazaar" and "lakh" are generic amount words that belong to
    # whichever thing the clause is about, so the noun is what disambiguates fee from salary.
    pay_context = any(t in SALARY_ANCHOR or t in SALARY_KEYWORDS for t in toks)
    # ...unless the clause names the fee outright, in which case it really is a fee.
    fee_named = any(t in FEE_KEYWORDS or t in FEE_CURRENCY for t in toks)

    fee = []
    for i, t in enumerate(toks):
        if t in FEE_CURRENCY or _NK_RE.fullmatch(t):
            fee.append(i)
        elif t in AMOUNT_WORDS and has_num and not (pay_context and not fee_named):
            # `Starting salary 25 se 30 hazaar hoti hai.` used to land here and block as FEE.
            # It blocked, so nothing leaked -- but FEE substitutes the fees deflection, which
            # answers a salary question with a sentence about fees. A wrong safe line is its
            # own failure: the lead hears a non-sequitur and asks again.
            fee.append(i)
        elif t in FEE_KEYWORDS and has_num:
            fee.append(i)
    if fee:
        out[BlockCategory.FEE] = fee

    sal = [i for i, t in enumerate(toks) if t in SALARY_KEYWORDS]
    for phrase in SALARY_PHRASES:
        sal += _phrase_indices(toks, phrase)
    if has_num:
        # Number-gated, like FEE_KEYWORDS. A pay noun with no figure is a promise problem
        # (hard rule 2), which the prompt owns; this filter blocks FIGURES.
        sal += [i for i, t in enumerate(toks) if t in SALARY_ANCHOR]
    if sal:
        out[BlockCategory.SALARY] = sorted(set(sal))

    place = [i for i, t in enumerate(toks) if t == "%" or t in PLACEMENT_KEYWORDS]
    if not place:
        anchors = [i for i, t in enumerate(toks) if t in PLACEMENT_ANCHOR]
        # A number, OR a qualifier like "ratio" -- docs/04 blocks "placement ratio" by name
        # and it needs no figure to be an outcome claim.
        if anchors and (has_num or any(t in PLACEMENT_QUALIFIERS for t in toks)):
            place = anchors
    if place:
        out[BlockCategory.PLACEMENT] = sorted(place)

    disc = [i for i, t in enumerate(toks) if t in DISCOUNT_KEYWORDS]
    if disc:
        out[BlockCategory.DISCOUNT] = disc

    if CERT_BLOCK_ENABLED:
        orgs = [i for i, t in enumerate(toks) if t in CERT_ORG_KEYWORDS]
        if orgs and any(t in CERT_TRIGGER for t in toks):
            out[BlockCategory.CERT] = orgs

    return out


def _is_pushback(toks: list[str]) -> bool:
    """A clause where a rebuttal cue sits within NEGATION_WINDOW tokens of a signal."""
    sig_positions = [i for idxs in _category_indices(toks).values() for i in idxs]
    if not sig_positions:
        return False
    cue_positions = [i for i, t in enumerate(toks) if t in REBUTTAL_CUES]
    return any(abs(s - c) <= NEGATION_WINDOW for s in sig_positions for c in cue_positions)


def screen(text: str) -> FilterVerdict:
    """Screen Roma's intended line. Never raises.

    Order: permitted-EMI allowlist -> negation-gated pushback allow -> block by
    precedence -> allow clean.
    """
    try:
        norm = normalize(text)
        if norm == _PERMITTED_NORM:
            return FilterVerdict(True, None, None, "allow:permitted-emi", ())

        clauses = _clauses(norm)

        if any(_is_pushback(toks) for toks in clauses):
            return FilterVerdict(True, None, None, "allow:pushback", ())

        for toks in clauses:
            cats = _category_indices(toks)
            for cat in _PRECEDENCE:
                if cat in cats:
                    matched = tuple(toks[i] for i in cats[cat])
                    return FilterVerdict(
                        False, cat, SUBSTITUTIONS[cat], f"block:{cat.value}", matched
                    )
        return FilterVerdict(True, None, None, "allow:clean", ())
    except Exception as exc:  # noqa: BLE001 — fail-safe, never leak raw text
        _log.error("pre-TTS filter error, hard-failing turn: %s", type(exc).__name__)
        return FilterVerdict(
            False, None, HARD_FAIL_LINE, f"filter-error:{type(exc).__name__}", ()
        )


def safe_output(text: str) -> str:
    """The only thing the TTS path calls: Roma's line if allowed, else the safe
    substitution/canned line. Never raises."""
    try:
        verdict = screen(text)
        return text if verdict.allowed else verdict.safe_line
    except Exception as exc:  # noqa: BLE001 — defense in depth, screen() already guards
        _log.error("safe_output error, hard-failing turn: %s", type(exc).__name__)
        return HARD_FAIL_LINE
