# Pre-TTS Filter — Design (Gate 0, second half)

**Date:** 2026-07-20
**Contract:** `docs/04-guardrails.md` (GATE-ZERO). This spec does not invent beyond it.
**Depends on:** Gate 0 first half (secret/logging scaffold, `src/roma/`).
**Status:** approved design → spec review → plan → implement.

## Purpose

The single component no callable build ships without. It sits between the LLM
(post sentence-chunk) and Bulbul TTS, reads the **normalized** text of Roma's
intended line, and is a **router, not a censor**: on a catch it substitutes a safe
line, it never deletes-and-leaves-dead-air. Sub-10ms, lexicon + compiled regex,
**no model call**.

It enforces the three override rules from `docs/04`: no money (only "No-cost EMI
available"), no guarantees (skills-outcome framing), no hallucination (bridge to
counsellor).

## Decisions locked in brainstorming

- **Decomposition (Approach B):** a small `guardrails/` package of three focused,
  independently-testable units — `normalize` → `lexicon` (the config) → `filter`
  (the engine). Rejected: a single flat module (tangles the enforceable lexicon with
  the engine) and a data-driven YAML rules file (adds a parse layer; doc wants
  compiled regex; no second consumer — YAGNI).
- **Allow-case = rebuttal-framing / negation-gated.** The filter reads Roma's line
  only (per doc). A number/keyword is allowed **only** when an explicit negation/
  pushback cue is co-located with it. This is the safe direction: it opens the gate
  only on a refusal/deflection; a bare affirmative always blocks. This is the doc's
  "highest false-positive risk — pressure-test it" case, covered by adversarial tests.
- **CERT ships as a real 5th category, toggle-gated.** D1 (which certs are real) is
  open, but the doc's rule ("claim ONLY the Weltec certificate; block/soften the
  rest") is already unambiguous, so it is enforced (default on), not stubbed-off.
  `CERT_BLOCK_ENABLED` flips when D1 lands.
- **skill-creator step runs LAST**, only after the module is green — so the enforced
  skill encodes *verified* behavior, not intended behavior.

## Layout & public interface

```
src/roma/guardrails/
  __init__.py      # exports: screen, safe_output, FilterVerdict, BlockCategory
  normalize.py     # normalize(text) -> str
  lexicon.py       # THE config: term sets, substitution lines, cues, cert toggle
  filter.py        # screen(text) -> FilterVerdict ; safe_output(text) -> str
tests/guardrails/
  test_normalize.py
  test_lexicon.py
  test_filter.py
```

The pipeline touches only:

- `screen(text: str) -> FilterVerdict` — **never raises**.
- `safe_output(text: str) -> str` — returns Roma's line if allowed, else the
  substitution/canned line. The **only** thing the TTS path calls.

`BlockCategory` — `Enum`: `FEE, SALARY, PLACEMENT, DISCOUNT, CERT`.

`FilterVerdict` — frozen dataclass:

| field | type | meaning |
|---|---|---|
| `allowed` | `bool` | pass Roma's line through as-is |
| `category` | `BlockCategory \| None` | which category caught it (None if allowed) |
| `safe_line` | `str \| None` | substitution to play if blocked (None if allowed) |
| `reason` | `str` | short trace string (`"allow:permitted-emi"`, `"block:FEE"`, `"allow:pushback"`, `"filter-error:…"`) |
| `matched` | `tuple[str, ...]` | matched tokens/spans, for tests & debugging |

**Scaffold tie-in:** the fail-safe path logs through `roma.logging_setup`'s configured
redacting logger. No secrets are read; the filter's rules are static config, not env.

## normalize.py

`normalize(text: str) -> str`, in order:

1. Unicode **NFC** normalize.
2. Strip zero-width / stray combining junk (ZWJ, ZWNJ, BOM).
3. `casefold()` (lowercase, Unicode-aware).
4. Collapse runs of whitespace to a single space; strip ends.
5. **Brand-fold:** `valtech`, `welltech`, `well-tech`, `weltech` → `weltec`.

No cross-script transliteration — we match the spellings the lexicon lists
(Devanagari/Gujarati/Latin variants); leads use the ones we didn't (accepted per doc).
Reusable by the STT path later; unit-tested in isolation.

## lexicon.py — the enforced config

Per-category signal sets (multilingual + transliteration). Keyword matching is on
**token boundaries, not substrings**: the normalized line is tokenized on Unicode
whitespace/punctuation and a keyword matches by **token equality** (so `fees` never
fires inside `coffees`). Only numbers / `%` / amount forms use regex.

| Cat | Signals |
|---|---|
| FEE | a currency/amount marker — `₹`, `rs`, `rupaye/rupees`, an amount-word (`hazaar/हज़ार/હજાર`, `Nk` as in `5k`) — **or** `fees/fee/फीस/ફી` co-located with a number |
| SALARY | `lakh/laakh/लाख/લાખ`, `lpa`, `per annum`, `package/पैकेज` |
| PLACEMENT | `placement ratio`, `%`, `percent/percentage/प्रतिशत`, `<n> to <n>` range |
| DISCOUNT | `discount/डिस्काउंट/ડિસ્કાઉન્ટ`, `offer`, `scheme/स्कीम` |
| CERT | claim of `google/meta/ibm/government/govt/सरकारी` certificate; `weltec` cert is allowed |

**Number detection (shared):** a "number" is a run of Unicode digits or an amount-word
(`hazaar`, `Nk`, `lakh`). A **bare number never triggers a category on its own** — it
only matters when co-located with a currency marker or a FEE/SALARY/PLACEMENT keyword,
or inside a `<n> to <n>` range. This is what lets `kal 3 baje visit` (test A3) pass
clean while `fees ₹5000` blocks.

Also defined here:

- `SUBSTITUTIONS: dict[BlockCategory, str]` — the four doc lines:
  - `FEE` / `DISCOUNT` → *"Ye sab counsellor visit pe aapki situation ke hisaab se
    batayenge — main phone pe number nahi de sakti, call bhi record ho raha hai."*
  - `SALARY` → *"Outcome aapke effort aur interview pe depend karta hai — hum skills
    aur placement support dete hain."*
  - `PLACEMENT` → *"Placement support hum dete hain, exact number counsellor batayenge
    visit pe."*
  - `CERT` → bridge/soften line: *"Certificate ke baare mein counsellor visit pe
    detail batayenge — hum Weltec ka certificate dete hain."*
- `PERMITTED_MONEY_LINE = "No-cost EMI available hai."` — the one permitted money
  statement anywhere (allowlisted exact match).
- `REBUTTAL_CUES` — negation/pushback tokens: `nahi/नहीं/નથી/નહીં`, `nahin`, `mat`,
  `not`, and deflection phrases handled as tokens.
- `HARD_FAIL_LINE` — generic safe canned line for the fail-safe path:
  *"Ye detail counsellor visit pe batayenge."*
- `CERT_BLOCK_ENABLED = True` — flips when D1 lands.
- `CLAUSE_DELIMS` = `, ; ।` and spaced dash `" - "` (see hyphen rule below).
- `NEGATION_WINDOW = 3` — max token distance between a signal and a rebuttal cue for
  the allow-case to fire.

## Decision logic — screen()

Operates on the normalized line. Steps, in order:

1. If the normalized line **equals** `PERMITTED_MONEY_LINE` → **ALLOW**
   (`reason="allow:permitted-emi"`).
2. Split the line into **clauses**. Delimiters: `,` `;` `।` and a **spaced dash**
   `" - "`. **Never split on an unspaced hyphen** — compounds (`no-cost`) and numeric
   ranges (`5-8 lakh`, `80-90%`) must stay intact inside one clause.
3. A clause is a **pushback clause** if it contains a category signal **and** a
   rebuttal cue **within `NEGATION_WINDOW` tokens** of that signal.
4. If **any** clause is a pushback clause → **ALLOW** the whole line
   (`reason="allow:pushback"`). (Negation-gated allow-case.)
5. Else if **any** clause contains a category signal → **BLOCK**. Category chosen by
   fixed precedence `[FEE, DISCOUNT, SALARY, PLACEMENT, CERT]`; `safe_line` from
   `SUBSTITUTIONS[category]`; `reason="block:<CAT>"`.
6. Else → **ALLOW** (`reason="allow:clean"`).

CERT signals are subject to the same pushback/negation gate (a refusal like "hum
Google cert nahi dete" is safe → allowed; an affirmative "Google certified course
hai" → blocked). CERT is skipped entirely when `CERT_BLOCK_ENABLED` is False.

## Fail-safe

- `screen()` wraps all detection in `try/except Exception` → returns
  `FilterVerdict(allowed=False, category=None, safe_line=HARD_FAIL_LINE,
  reason="filter-error:<type>", matched=())`, and logs the error via the scaffold's
  redacting logger. It **never re-raises** and **never returns raw LLM text**.
- `safe_output()` is likewise guarded: any failure → `HARD_FAIL_LINE`.
- Regex/lexicon compile happens at **import**; a compile failure fails the import.
  Absence of the filter = absence of a callable build (per doc).

## Test table (unit tests — Gate 0 requires it)

Each row is `input → expected (verdict / category)`. Table-driven.

### Block cases
| # | Input (Roma's line) | Expect |
|---|---|---|
| B1 | `fees ₹5000 hai` | BLOCK FEE |
| B2 | `fees sirf 5 hazaar` | BLOCK FEE |
| B3 | `bas 5k lagega` | BLOCK FEE |
| B4 | `6 lakh package milta hai` | BLOCK SALARY |
| B5 | `5 lpa se shuru` | BLOCK SALARY |
| B6 | `95% placement ratio hai` | BLOCK PLACEMENT |
| B7 | `placement 90 percent tak` | BLOCK PLACEMENT |
| B8 | `abhi special discount chal raha hai` | BLOCK DISCOUNT |
| B9 | `ek scheme hai aapke liye` | BLOCK DISCOUNT |
| B10 | `ye course google certified hai` | BLOCK CERT |
| B11 | `government approved certificate milega` | BLOCK CERT |

### Allow cases
| # | Input | Expect |
|---|---|---|
| A1 | `No-cost EMI available hai.` | ALLOW (permitted-emi) |
| A2 | `hum Weltec ka certificate dete hain` | ALLOW (clean; Weltec permitted) |
| A3 | `kal 3 baje visit fix karein?` | ALLOW (clean; innocent number) |
| A4 | `main aapki baat samajh rahi hoon` | ALLOW (clean) |

### Allow-case: negation-gated pushback
| # | Input | Expect | Pins |
|---|---|---|---|
| P1 | `wo ₹5000 fees nahi, offer message hai` | ALLOW (pushback) | canonical doc allow-case; comma clause-split |
| P2 | `main discount nahi de sakti` | ALLOW (pushback) | negation adjacent to keyword |
| P3 | `wo number fees nahi tha` | ALLOW (pushback) | lead-quoted number deflected |

### Negation-distance (the FP boundary — explicit)
| # | Input | Expect | Pins |
|---|---|---|---|
| N1 | `wo ₹5000 fees nahi` | ALLOW (pushback) | cue within WINDOW of signal |
| N2 | `fees ₹5000 hai aur main jhooth nahi bolti` | BLOCK FEE | `nahi` >WINDOW tokens from fee, modifies a different word — must NOT unlock |
| N3 | `haan discount hai, lekin abhi nahi` | BLOCK DISCOUNT | affirmative in clause 1; negation in a later clause does not rescue it |

### Hyphen-split (explicit)
| # | Input | Expect | Pins |
|---|---|---|---|
| H1 | `No-cost EMI available hai.` | ALLOW (permitted-emi) | unspaced hyphen NOT split; permitted line intact |
| H2 | `5-8 lakh package` | BLOCK SALARY | unspaced range hyphen kept in one clause; still detected |
| H3 | `placement 80-90% tak` | BLOCK PLACEMENT | unspaced range hyphen kept; `%` still detected |
| H4 | `wo ₹5000 fees nahi - offer message hai` | ALLOW (pushback) | spaced dash splits clauses; clause 1 is pushback |
| H5 | `fees ₹5000 hai - main jhooth nahi bolti` | BLOCK FEE | spaced dash splits; fee clause has no co-located negation |

### Token boundary
| # | Input | Expect | Pins |
|---|---|---|---|
| T1 | `chaliye coffees pe baat karte hain` | ALLOW (clean) | `fees` must NOT fire inside `coffees` |
| T2 | `aapka percentage kitna tha school mein?` | BLOCK PLACEMENT | `percentage` is a real placement signal token — documents that this is expected (Roma asking about marks still routes; acceptable over-block, safe direction) |

> T2 note: this is a deliberate conservative catch. If field testing shows Roma
> legitimately needs to ask a lead's academic percentage, revisit — but the safe
> direction is to block. Recorded so code review doesn't "fix" it as a bug.

### Prior-quote injection
| # | Input | Expect | Pins |
|---|---|---|---|
| I1 | `haan aapko jo ₹5000 bataya wo sahi hai` | BLOCK FEE | Roma affirmatively restating a lead-quoted number still blocks (filter sees only Roma's line; never concede) |

### Fail-safe
| # | Setup | Expect |
|---|---|---|
| F1 | monkeypatch a matcher to raise inside `screen()` | returns `allowed=False`, `safe_line=HARD_FAIL_LINE`, no exception propagates |
| F2 | `safe_output()` when `screen()` would raise | returns `HARD_FAIL_LINE` |

## Out of scope (this task)

- Wiring the filter into the live Pipecat→Bulbul path (that is Step 3 in
  `docs/10`; the cancellation-through-filter path is Step 5).
- Any live API call (no context7 needed — no live API is touched here).
- The D1 cert answer itself (owner: Weltec). We ship the toggle.

## Task exit

Module + tests green (`uv run pytest`) → then the **skill-creator** step: turn
`docs/04` into an enforced skill that points at `lexicon.py`, so the rules are loaded
not remembered, encoding the *verified* behavior. Then STOP and confirm before Step 1
(telephony spine).
