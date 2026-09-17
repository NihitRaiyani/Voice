# Pre-TTS Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone, unit-tested pre-TTS guardrail filter (`docs/04-guardrails.md`) — four block categories + CERT, the negation-gated allow-case, substitution lines, and the fail-safe — that no callable build ships without.

**Architecture:** A `src/roma/guardrails/` package of three focused units: `normalize.py` (NFC → casefold → brand-fold), `lexicon.py` (the enforced config: term sets, substitution lines, cues, toggles), and `filter.py` (compiled matchers + `screen()`/`safe_output()`). The filter reads Roma's normalized line only; on a catch it substitutes a safe line; on any internal error it hard-fails to a canned safe line. No model call.

**Tech Stack:** Python 3.11+, `re` + `unicodedata` (stdlib only — no new deps), pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-07-20-pretts-filter-design.md`. Read it before starting.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/roma/guardrails/__init__.py` | Public exports: `screen`, `safe_output`, `FilterVerdict`, `BlockCategory` |
| `src/roma/guardrails/normalize.py` | `normalize(text) -> str` — NFC, zero-width strip, casefold, whitespace collapse, brand-fold |
| `src/roma/guardrails/lexicon.py` | `BlockCategory` enum + all config constants (term sets, `SUBSTITUTIONS`, cues, `PERMITTED_MONEY_LINE`, `HARD_FAIL_LINE`, toggles, `NEGATION_WINDOW`) |
| `src/roma/guardrails/filter.py` | `FilterVerdict` dataclass, tokenizer, clause split, category detection, `screen()`, `safe_output()`, fail-safe |
| `tests/guardrails/test_normalize.py` | normalize unit tests |
| `tests/guardrails/test_lexicon.py` | lexicon sanity + self-safety (substitution lines don't self-trigger) |
| `tests/guardrails/test_filter.py` | the full spec test table (block / allow / pushback / negation-distance / hyphen / token-boundary / injection / fail-safe) |

Run tests with `uv run pytest`. Ensure dev deps: `uv sync --extra dev`.

---

## Task 1: Package skeleton + normalize.py

**Files:**
- Create: `src/roma/guardrails/__init__.py`
- Create: `src/roma/guardrails/normalize.py`
- Create: `tests/guardrails/__init__.py` (empty, so the test dir is a package)
- Test: `tests/guardrails/test_normalize.py`

- [ ] **Step 1: Create the empty package markers**

Create `src/roma/guardrails/__init__.py` with a placeholder docstring (exports added in Task 5):

```python
"""Roma pre-TTS guardrail filter (docs/04-guardrails.md)."""
```

Create `tests/guardrails/__init__.py` as an empty file (0 bytes).

- [ ] **Step 2: Write the failing test**

Create `tests/guardrails/test_normalize.py`:

```python
from roma.guardrails.normalize import normalize


def test_lowercases_and_collapses_whitespace():
    assert normalize("  Fees   ₹5000  ") == "fees ₹5000"


def test_nfc_normalizes():
    # 'क' + nukta (U+0915 U+093C) NFC-composes to क़ (U+0958)
    assert normalize("क़") == normalize("क़")


def test_strips_zero_width_chars():
    assert normalize("fe​es") == "fees"


def test_casefolds_unicode():
    assert normalize("LPA") == "lpa"


def test_brand_fold_variants_to_weltec():
    for variant in ("Valtech", "Welltech", "Well-Tech", "Weltech"):
        assert normalize(f"{variant} course") == "weltec course"


def test_empty_input():
    assert normalize("") == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/guardrails/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roma.guardrails.normalize'`

- [ ] **Step 4: Write the implementation**

Create `src/roma/guardrails/normalize.py`:

```python
"""Normalization for the pre-TTS filter: NFC -> zero-width strip -> casefold ->
whitespace collapse -> brand-fold. No cross-script transliteration; the lexicon
lists the spellings we match (docs/04)."""

import re
import unicodedata

# Zero-width / BOM characters to delete outright.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_WS_RE = re.compile(r"\s+")
# Brand variants fold to the canonical 'weltec' (matched after casefold).
_BRAND_RE = re.compile(r"\b(?:valtech|welltech|well-tech|weltech)\b")


def normalize(text: str) -> str:
    """Return the canonical form the matcher operates on."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_ZERO_WIDTH)
    text = text.casefold()
    text = _WS_RE.sub(" ", text).strip()
    text = _BRAND_RE.sub("weltec", text)
    return text
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/guardrails/test_normalize.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add src/roma/guardrails/__init__.py src/roma/guardrails/normalize.py tests/guardrails/__init__.py tests/guardrails/test_normalize.py
git commit -m "feat(guardrails): add text normalization for pre-TTS filter"
```

---

## Task 2: lexicon.py — the enforced config

**Files:**
- Create: `src/roma/guardrails/lexicon.py`
- Test: `tests/guardrails/test_lexicon.py`

- [ ] **Step 1: Write the failing test**

Create `tests/guardrails/test_lexicon.py`:

```python
from roma.guardrails import lexicon as lex
from roma.guardrails.lexicon import BlockCategory


def test_every_category_has_a_substitution_line():
    for cat in BlockCategory:
        assert cat in lex.SUBSTITUTIONS
        assert lex.SUBSTITUTIONS[cat].strip()


def test_permitted_money_line_present():
    assert lex.PERMITTED_MONEY_LINE == "No-cost EMI available hai."


def test_hard_fail_line_nonempty():
    assert lex.HARD_FAIL_LINE.strip()


def test_negation_window_is_positive_int():
    assert isinstance(lex.NEGATION_WINDOW, int) and lex.NEGATION_WINDOW >= 1


def test_cert_block_enabled_default_on():
    assert lex.CERT_BLOCK_ENABLED is True


def test_rebuttal_cues_include_core_negations():
    assert {"nahi", "नहीं"} <= lex.REBUTTAL_CUES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/guardrails/test_lexicon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roma.guardrails.lexicon'`

- [ ] **Step 3: Write the implementation**

Create `src/roma/guardrails/lexicon.py`:

```python
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


# --- Category signal vocabularies (normalized, i.e. casefolded) -----------------

# FEE: a currency marker, an amount-word, an 'Nk' token, or 'fees'+number.
FEE_CURRENCY = {"₹", "rs", "rs.", "rupaye", "rupees", "rupee"}
FEE_KEYWORDS = {"fees", "fee", "फीस", "ફી"}
AMOUNT_WORDS = {"hazaar", "हज़ार", "હજાર"}  # count only alongside a number

# SALARY
SALARY_KEYWORDS = {"lakh", "laakh", "लाख", "લાખ", "lpa", "package", "पैकेज"}
SALARY_PHRASES = [["per", "annum"]]

# PLACEMENT: '%' or percent-words, or the 'placement' anchor beside a number.
PLACEMENT_KEYWORDS = {"percent", "percentage", "प्रतिशत"}
PLACEMENT_ANCHOR = {"placement", "प्लेसमेंट"}

# DISCOUNT
DISCOUNT_KEYWORDS = {"discount", "डिस्काउंट", "ડિસ્કાઉન્ટ", "offer", "scheme", "स्कीम"}

# CERT: an org cert-claim (Weltec is allowed and deliberately absent here).
CERT_ORG_KEYWORDS = {"google", "meta", "ibm", "government", "govt", "सरकारी"}
CERT_TRIGGER = {"cert", "certificate", "certified", "certification"}

# --- Allow-case machinery -------------------------------------------------------

REBUTTAL_CUES = {"nahi", "nahin", "नहीं", "નથી", "નહીં", "mat", "not", "na"}
NEGATION_WINDOW = 3  # max token distance between a signal and a cue for pushback

PERMITTED_MONEY_LINE = "No-cost EMI available hai."

# --- Substitution lines (docs/04, from the clean inquiry-team model) ------------

_MONEY_LINE = (
    "Ye sab counsellor visit pe aapki situation ke hisaab se batayenge — main "
    "phone pe number nahi de sakti, call bhi record ho raha hai."
)
SUBSTITUTIONS = {
    BlockCategory.FEE: _MONEY_LINE,
    BlockCategory.DISCOUNT: _MONEY_LINE,
    BlockCategory.SALARY: (
        "Outcome aapke effort aur interview pe depend karta hai — hum skills aur "
        "placement support dete hain."
    ),
    BlockCategory.PLACEMENT: (
        "Placement support hum dete hain, exact number counsellor batayenge visit pe."
    ),
    BlockCategory.CERT: (
        "Certificate ke baare mein counsellor visit pe detail batayenge — hum Weltec "
        "ka certificate dete hain."
    ),
}

# Generic canned line for the fail-safe path (filter error / unavailable).
HARD_FAIL_LINE = "Ye detail counsellor visit pe batayenge."

# CERT stays enforced (default on) even with D1 open; flip when D1 is answered.
CERT_BLOCK_ENABLED = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/guardrails/test_lexicon.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/roma/guardrails/lexicon.py tests/guardrails/test_lexicon.py
git commit -m "feat(guardrails): add lexicon config (categories, substitutions, cues)"
```

---

## Task 3: filter.py — matchers + block path

**Files:**
- Create: `src/roma/guardrails/filter.py`
- Test: `tests/guardrails/test_filter.py`

This task builds the tokenizer, per-category detection, and a **block-only** `screen()`
(allow-case added in Task 4). Tests exercise the public `screen()`.

- [ ] **Step 1: Write the failing test (block rows B1–B11)**

Create `tests/guardrails/test_filter.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/guardrails/test_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roma.guardrails.filter'`

- [ ] **Step 3: Write the implementation**

Create `src/roma/guardrails/filter.py`:

```python
"""The pre-TTS filter (docs/04-guardrails.md). Router, not censor: on a catch it
substitutes a safe line; on any internal error it hard-fails to a canned line.
Reads Roma's normalized line only. No model call."""

import logging
import re
from dataclasses import dataclass

from roma.guardrails.lexicon import (
    AMOUNT_WORDS,
    BlockCategory,
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
    REBUTTAL_CUES,
    SALARY_KEYWORDS,
    SALARY_PHRASES,
    SUBSTITUTIONS,
)
from roma.guardrails.normalize import normalize

_log = logging.getLogger("roma.guardrails")

# Fixed precedence for reporting when several categories match one clause.
_PRECEDENCE = [
    BlockCategory.FEE,
    BlockCategory.DISCOUNT,
    BlockCategory.SALARY,
    BlockCategory.PLACEMENT,
    BlockCategory.CERT,
]

# A token is a word-run OR one of the standalone symbols we care about, so
# "₹5000" -> ["₹", "5000"] and "95%" -> ["95", "%"] with real positions.
_TOKEN_RE = re.compile(r"[₹%]|\w+")
# Clause split: comma / semicolon / danda / a SPACED dash. Never an unspaced hyphen.
_CLAUSE_RE = re.compile(r"\s+-\s+|[,;।]")
_NK_RE = re.compile(r"\d+k")
_PERMITTED_NORM = normalize(PERMITTED_MONEY_LINE)


@dataclass(frozen=True)
class FilterVerdict:
    allowed: bool
    category: "BlockCategory | None"
    safe_line: "str | None"
    reason: str
    matched: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


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

    fee = []
    for i, t in enumerate(toks):
        if t in FEE_CURRENCY or _NK_RE.fullmatch(t):
            fee.append(i)
        elif t in AMOUNT_WORDS and has_num:
            fee.append(i)
        elif t in FEE_KEYWORDS and has_num:
            fee.append(i)
    if fee:
        out[BlockCategory.FEE] = fee

    sal = [i for i, t in enumerate(toks) if t in SALARY_KEYWORDS]
    for phrase in SALARY_PHRASES:
        sal += _phrase_indices(toks, phrase)
    if sal:
        out[BlockCategory.SALARY] = sorted(sal)

    place = [i for i, t in enumerate(toks) if t == "%" or t in PLACEMENT_KEYWORDS]
    if not place and has_num:
        place = [i for i, t in enumerate(toks) if t in PLACEMENT_ANCHOR]
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
    """Screen Roma's intended line. Never raises."""
    try:
        norm = normalize(text)
        clauses = _clauses(norm)
        for toks in clauses:
            cats = _category_indices(toks)
            for cat in _PRECEDENCE:
                if cat in cats:
                    matched = tuple(toks[i] for i in cats[cat])
                    return FilterVerdict(
                        False, cat, SUBSTITUTIONS[cat], f"block:{cat.value}", matched
                    )
        return FilterVerdict(True, None, None, "allow:clean", ())
    except Exception as exc:  # fail-safe — never leak raw text
        _log.error("pre-TTS filter error, hard-failing turn: %s", type(exc).__name__)
        return FilterVerdict(
            False, None, HARD_FAIL_LINE, f"filter-error:{type(exc).__name__}", ()
        )
```

> Note: `screen()` here is block-only (no allow-case yet); the permitted-EMI line and
> the pushback allow are added in Task 4. The fail-safe wrapper is already in place.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/guardrails/test_filter.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/roma/guardrails/filter.py tests/guardrails/test_filter.py
git commit -m "feat(guardrails): add matchers and block path for four categories + cert"
```

---

## Task 4: Allow-case (permitted-EMI, negation-gated pushback, hyphen rule)

**Files:**
- Modify: `src/roma/guardrails/filter.py` (rewrite `screen()` body — add allow-case steps before the block loop)
- Test: `tests/guardrails/test_filter.py` (append allow / pushback / negation-distance / hyphen / token-boundary / injection rows)

- [ ] **Step 1: Write the failing tests (append)**

Append to `tests/guardrails/test_filter.py`:

```python
# --- Allow (clean / permitted) ---
ALLOW_CASES = [
    "No-cost EMI available hai.",
    "hum Weltec ka certificate dete hain",
    "kal 3 baje visit fix karein?",
    "main aapki baat samajh rahi hoon",
    # pushback (negation-gated)
    "wo ₹5000 fees nahi, offer message hai",
    "main discount nahi de sakti",
    "wo number fees nahi tha",
    "wo ₹5000 fees nahi",
    # hyphen: spaced dash splits -> clause 1 is pushback
    "wo ₹5000 fees nahi - offer message hai",
    # token boundary: 'fees' must not fire inside 'coffees'
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


# --- Negation-distance boundary (must still BLOCK) ---
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


# --- Hyphen: unspaced hyphen not split; ranges still detected ---
HYPHEN_BLOCK_CASES = [
    ("5-8 lakh package", BlockCategory.SALARY),
    ("placement 80-90% tak", BlockCategory.PLACEMENT),
]


@pytest.mark.parametrize("text,category", HYPHEN_BLOCK_CASES)
def test_unspaced_hyphen_ranges_still_block(text, category):
    assert screen(text).category is category


# --- Prior-quote injection: Roma affirmatively restating a number still blocks ---
def test_prior_quote_injection_still_blocks():
    v = screen("haan aapko jo ₹5000 bataya wo sahi hai")
    assert v.allowed is False
    assert v.category is BlockCategory.FEE


# --- T2: documented conservative over-block ---
def test_percentage_question_is_conservatively_blocked():
    assert screen("aapka percentage kitna tha school mein?").category is BlockCategory.PLACEMENT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/guardrails/test_filter.py -v`
Expected: FAIL — several allow/pushback cases fail (e.g. `allow:permitted-emi` reason missing; `wo ₹5000 fees nahi` currently blocks FEE instead of allowing).

- [ ] **Step 3: Rewrite `screen()` to add the allow-case**

In `src/roma/guardrails/filter.py`, replace the entire `screen()` function with:

```python
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

        # Allow-case: any clause where a rebuttal cue is co-located with a signal
        # marks the whole line as a lead-pushback deflection.
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
    except Exception as exc:  # fail-safe — never leak raw text
        _log.error("pre-TTS filter error, hard-failing turn: %s", type(exc).__name__)
        return FilterVerdict(
            False, None, HARD_FAIL_LINE, f"filter-error:{type(exc).__name__}", ()
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/guardrails/test_filter.py -v`
Expected: PASS (all block + allow + pushback + negation + hyphen + injection rows green)

- [ ] **Step 5: Commit**

```bash
git add src/roma/guardrails/filter.py tests/guardrails/test_filter.py
git commit -m "feat(guardrails): add permitted-EMI allowlist and negation-gated pushback allow-case"
```

---

## Task 5: Fail-safe test + safe_output + public exports

**Files:**
- Modify: `src/roma/guardrails/filter.py` (add `safe_output`)
- Modify: `src/roma/guardrails/__init__.py` (public exports)
- Test: `tests/guardrails/test_filter.py` (append fail-safe + safe_output)

- [ ] **Step 1: Write the failing tests (append)**

Append to `tests/guardrails/test_filter.py`:

```python
from roma.guardrails import filter as filter_mod
from roma.guardrails.lexicon import HARD_FAIL_LINE


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
    from roma.guardrails.lexicon import SUBSTITUTIONS, BlockCategory

    assert filter_mod.safe_output("fees ₹5000 hai") == SUBSTITUTIONS[BlockCategory.FEE]


def test_safe_output_failsafe_on_error(monkeypatch):
    monkeypatch.setattr(filter_mod, "_category_indices", _boom)
    assert filter_mod.safe_output("fees ₹5000 hai") == HARD_FAIL_LINE


def test_public_exports():
    from roma.guardrails import BlockCategory, FilterVerdict, safe_output, screen

    assert callable(screen) and callable(safe_output)
    assert FilterVerdict and BlockCategory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/guardrails/test_filter.py -v`
Expected: FAIL — `AttributeError: module 'roma.guardrails.filter' has no attribute 'safe_output'` and the exports import fails.

- [ ] **Step 3: Add `safe_output` to filter.py**

Append to `src/roma/guardrails/filter.py`:

```python
def safe_output(text: str) -> str:
    """The only thing the TTS path calls: Roma's line if allowed, else the safe
    substitution/canned line. Never raises."""
    try:
        verdict = screen(text)
        return text if verdict.allowed else verdict.safe_line
    except Exception as exc:  # defense in depth — screen() already guards
        _log.error("safe_output error, hard-failing turn: %s", type(exc).__name__)
        return HARD_FAIL_LINE
```

- [ ] **Step 4: Wire public exports**

Replace `src/roma/guardrails/__init__.py` with:

```python
"""Roma pre-TTS guardrail filter (docs/04-guardrails.md).

No callable build ships without this. The TTS path calls `safe_output`.
"""

from roma.guardrails.filter import FilterVerdict, safe_output, screen
from roma.guardrails.lexicon import BlockCategory

__all__ = ["screen", "safe_output", "FilterVerdict", "BlockCategory"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/guardrails/ -v`
Expected: PASS (whole guardrails suite green)

- [ ] **Step 6: Commit**

```bash
git add src/roma/guardrails/filter.py src/roma/guardrails/__init__.py tests/guardrails/test_filter.py
git commit -m "feat(guardrails): add safe_output entrypoint, fail-safe test, public exports"
```

---

## Task 6: Full suite + docs + session bookkeeping

**Files:**
- Modify: `docs/decisions.md`
- Modify: `LOG.md`
- Modify: `SESSION.md`

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -v`
Expected: PASS — the prior 18 scaffold tests plus the new guardrails tests, all green. If any pre-existing test broke, stop and fix before continuing.

- [ ] **Step 2: Record decisions**

In `docs/decisions.md`, under `## Locked`, append:

```markdown
- **Pre-TTS filter (Gate 0, docs/04):** standalone `src/roma/guardrails/` package
  (normalize → lexicon → filter). Allow-case is **negation-gated pushback** (filter
  reads Roma's line only; a cue within `NEGATION_WINDOW=3` tokens of a signal opens the
  gate). Clauses split on `, ; ।` and a spaced dash — never an unspaced hyphen.
  CERT is a real 5th category, `CERT_BLOCK_ENABLED=True` pending D1. Fail-safe hard-fails
  the turn to a canned line; absence of the filter = absence of a callable build.
  Spec: `docs/superpowers/specs/2026-07-20-pretts-filter-design.md`.
```

- [ ] **Step 3: Append to the build log**

In `LOG.md`, add a new entry at the top (below the header line):

```markdown
## 2026-07-20 — Gate 0 second half: pre-TTS guardrail filter
Brainstormed → approved spec → plan → TDD execution. `src/roma/guardrails/`:
`normalize.py`, `lexicon.py` (the enforced config), `filter.py` (`screen`/`safe_output`).
Four block categories + CERT, negation-gated pushback allow-case, per-category
substitution lines, permitted-EMI allowlist, fail-safe hard-fail line. Full spec test
table green (block / allow / pushback / negation-distance / hyphen / token-boundary /
prior-quote injection / fail-safe). Stdlib only, no new deps.

---
```

- [ ] **Step 4: Update SESSION.md**

Set `## Building now` to reflect: module complete and green; next sub-step is the
skill-creator step (turn `docs/04` into an enforced skill pointing at `lexicon.py`),
then STOP before Step 1. Set `## Half-done / careful` to note the skill-creator step is
pending and the Step-1 stop gate still holds.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions.md LOG.md SESSION.md
git commit -m "docs(guardrails): record pre-TTS filter decisions, log, session state"
```

---

## After the plan: skill-creator step (LAST — only after all tests green)

Per the spec's task exit and `CLAUDE.md` skill wiring: invoke **skill-creator** to turn
`docs/04-guardrails.md` into an enforced skill that points at `src/roma/guardrails/lexicon.py`,
so the filter rules are loaded, not remembered — encoding the **verified** behavior now
that the module is green. This is a separate step, not a task above.

Then **STOP and confirm with the user** before starting Step 1 (telephony spine) in
`docs/10-build-order.md`.

---

## Self-Review (completed by plan author)

**Spec coverage:** three override rules → enforced by the four categories + CERT (Task 3);
four block categories → Task 3 (B1–B11); the ONE allow-case (negation-gated) → Task 4
(P1–P3, N1–N3); permitted-EMI allowlist → Task 4; substitution lines → lexicon (Task 2),
asserted in Task 3; prior-quote injection → Task 4 (I1); token-boundary → Task 4 (T1);
hyphen-split → Task 4 (H1–H5); negation-distance → Task 4 (N1–N3); fail-safe → Task 5
(F1–F2); cert toggle → lexicon + Task 3 (B10–B11); `safe_output` entrypoint → Task 5.
No spec section is left without a task.

**Placeholder scan:** none — every code and test step shows complete content.

**Type consistency:** `FilterVerdict(allowed, category, safe_line, reason, matched)`,
`BlockCategory`, `screen`, `safe_output`, `normalize`, `_category_indices`,
`NEGATION_WINDOW`, `PERMITTED_MONEY_LINE`, `HARD_FAIL_LINE`, `SUBSTITUTIONS` — names are
identical across all tasks and match the lexicon definitions.
