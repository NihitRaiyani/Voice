---
name: roma-guardrail
description: >-
  Roma's pre-TTS guardrail contract (docs/04-guardrails.md). Load this WHENEVER you
  touch the pre-TTS filter, the LLM→TTS path, sentence-chunking before Bulbul, the
  barge-in/cancellation teardown, or anything that decides what text Roma speaks —
  even if the change looks unrelated to safety. It defines what Roma may never say
  (fees, salary, placement stats, discounts, non-Weltec cert claims), the one
  allow-case, and the non-negotiable fail-safe. If you are wiring, editing, or
  reviewing code that produces Roma's spoken line, this contract governs it.
---

# Roma pre-TTS guardrail

The pre-TTS filter is the single component **no callable build ships without**. It sits
between the LLM (post sentence-chunk) and Bulbul TTS. It is a **router, not a censor**:
on a catch it substitutes a safe line — it never deletes-and-leaves-dead-air. Sub-10ms,
lexicon + compiled regex, **no model call**.

The behavior is already built and verified in `src/roma/guardrails/`.
**Do not re-implement it.** Use the module. The rules below are the contract it enforces;
the exhaustive, authoritative source is `docs/04-guardrails.md` and
`src/roma/guardrails/lexicon.py`.

## The one rule you must not break

Everything Roma is about to speak goes through the filter first:

```python
from roma.guardrails import safe_output

line_to_speak = safe_output(llm_sentence_chunk)  # never raises; never returns raw LLM text
tts.speak(line_to_speak)
```

`safe_output()` returns Roma's line if clean, else a safe substitution, else — on any
internal error — the canned `HARD_FAIL_LINE`. **Never send raw LLM output to Bulbul.**
Absence of the filter on this path = absence of a callable build. If you need the reason
(logging, tests), call `screen(text) -> FilterVerdict` instead.

## What the filter blocks (the four categories + CERT)

Each traces to a real source-call violation. The vocabularies (multilingual + translit)
live in `lexicon.py` — extend them there, never inline.

1. **FEE** — any ₹/amount, `fees … <number>`, `<n> hazaar`, `<n>k`.
2. **SALARY** — `lakh/LPA/per annum/package` fire alone; pay NOUNS
   (`salary/tankhwah/pagar/ctc` + Devanagari + Gujarati spellings, `SALARY_ANCHOR`) are
   number-gated — "salary achhi milti hai" is a prompt problem, "tankhwah 30000 milegi"
   must never reach the wire.
3. **PLACEMENT** — `placement ratio/rate/record`, `%`, `percent`, placement + number.
4. **DISCOUNT** — `discount`, `offer`, `scheme` (running promo).
5. **CERT** — any Google/Meta/IBM/government cert claim. Claim ONLY the Weltec
   certificate. Toggle `CERT_BLOCK_ENABLED` (default on) until D1 is answered.

Matching is on **token boundaries, not substrings** (`fees` must not fire inside
`coffees`), on the normalized line (NFC → casefold → brand-fold). A **bare number never
triggers** on its own.

## The ONE allow-case — do not over-block, do not under-block

A number the **lead** raised and Roma is **pushing back on** is allowed
(`"wo ₹5000 fees nahi, offer message hai"`). A number **originating from Roma** is
blocked. The filter reads Roma's line only, so this is **negation-gated**: a rebuttal cue
(`nahi/नहीं/…`) within `NEGATION_WINDOW` (=3) tokens of the signal opens the gate; a bare
affirmative always blocks. This is the highest false-positive risk — when you change it,
run the negation-distance tests (`N1–N3`) and the prior-quote injection test.

## Prior-quote defense (treat as in-conversation injection)

A lead claiming "someone quoted me a fee yesterday" must NOT unlock a number. The prompt
enforces "never concede"; the filter catches any number that slips regardless — because
it only ever sees Roma's own line. Do not add a code path that trusts lead-quoted numbers.

## Substitution lines

Defined in `lexicon.py` `SUBSTITUTIONS` (from the clean inquiry-team model). The only
permitted money statement anywhere is `"No-cost EMI available hai."` (allowlisted, exact).
Edit the wording there, not at call sites.

## Cancellation routes THROUGH the filter

On barge-in the in-flight generation is killed and Bulbul + Twilio buffers flushed. A
half-generated blocked line must not leak to TTS during teardown — the filter sits on that
path. When you build the cancellation lifecycle (Step 5), keep `safe_output()` on every
branch that can still emit audio, including teardown.

## Fail-safe (never bypass)

If the filter errors or is unavailable, the turn **hard-fails to a canned safe line** — it
never passes raw LLM output. `screen()` and `safe_output()` already guarantee this (they
catch everything and log via the redacting logger). Do not add a `try/except` around them
that falls back to the raw LLM text.

## When you change the rules

The lexicon and thresholds are the enforceable config. Change them in `lexicon.py`, add a
row to the spec test table (`tests/guardrails/test_filter.py` mirrors
`docs/superpowers/specs/2026-07-20-pretts-filter-design.md`), and keep the suite green.
This skill encodes **verified** behavior — if you change behavior, re-verify, then update
this contract. Do not invent rules that aren't in `docs/04`.
