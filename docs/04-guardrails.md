# 04 — Guardrails (GATE-ZERO)

**Status:** Implemented and covered by deterministic multilingual tests. Structured database audit
records and safety analytics are planned.

**Learning objective:** Treat AI safety as an enforceable backend policy with fail-safe behavior,
not as a sentence in a prompt.

The pre-TTS filter is the single component that never ships without. No call goes out without
it. It is a **router**, not a censor: on a catch it **substitutes** a safe line, it does not
delete-and-leave-dead-air.

## Where it sits
Between the LLM (post sentence-chunk) and Bulbul TTS. Reads the **normalized** text of Roma's
intended line. Sub-10ms. Lexicon + compiled regex. **No model call.**

## The three override rules it enforces
1. **No money** — only permitted money line is "No-cost EMI available."
2. **No guarantees** — skills-outcome framing only.
3. **No hallucination** — bridge to counsellor, never invent.

## The five block categories (each traced to a real source-call violation)
| # | Catch | Pattern (on normalized text) | Source |
|---|---|---|---|
| 1 | Fee figures | any ₹/amount, "fees … <number>", "<n> hazaar/k" | boundary tests |
| 2 | Salary/package | `lakh`, `LPA`, `per annum`, `package`; and a pay noun + figure — `salary`/`tankhwah`/`pagar`/`ctc` in all three scripts (`सैलरी`, `તનખ્વાહ`, `પગાર`, …) number-gated so "salary achhi milti hai" stays a prompt problem | Sachin; 2026-08-01 probe ("Tankhwah 30000 milegi" passed clean) |
| 3 | Placement stats | `placement ratio/rate/record`, `%`, `percent`, "<n> to <n>%" | Nikunj |
| 4 | Discount/offer | `discount`, `offer` (running promo), `scheme` | Krupal, Nikunj |
| 5 | Cert claims | Google/Meta/IBM/government + cert word — Weltec certificate only (see below) | D1 pending |

Match on **token boundaries, not substrings** (`fees` must not fire inside `coffees`). Lexicon
is the **superset** of Hindi/Gujarati/English + transliteration spellings — you match the ones
you list; leads use the ones you didn't. Include `lakh/laakh/लાख/લાખ`, `discount/डिस्काउंट`,
`percent/percentage/%`. Runs on the normalized transcript (NFC → script-fold → lowercase);
brand variants like "Valtech"/"Welltech" normalize to Weltec.

## The ONE allow-case (do not over-block)
A number **originating from the lead**, being corrected/pushed back on, is allowed (e.g. lead
cites an ad's ₹5000, Roma says "wo fees nahi, offer message hai"). A number **originating from
Roma** is blocked. This heuristic is the highest false-positive risk — pressure-test it.

## Prior-quote defense (treat as in-conversation injection)
A lead claiming "someone quoted me a fee yesterday" must NOT unlock a number. Hard rule in the
prompt; the filter catches any number that slips regardless. Never concede.

## Cancellation routes THROUGH the filter
On barge-in the in-flight generation is killed and Bulbul + Twilio buffers flushed. The filter
sits on that path — a half-generated blocked line must not leak to TTS during teardown.

## Substitution lines (what plays after a catch)
The authoritative wordings live in `guardrails/lexicon.py` `SUBSTITUTIONS` — edit them there,
never here. Current shape:
- Money/fee/discount: "Fees aapki situation ke hisaab se counselling meeting mein discuss
  hogi — isiliye visit best rahega." (The earlier "…number nahi de sakti, call bhi record ho
  raha hai" wording was RETIRED as a hard-rule-5 breach — an unprompted recording disclosure
  plus a refusal nobody asked for, on the most common turn of the call.)
- Salary/outcome: "Outcome aapke effort aur interview pe depend karta hai — hum skills aur
  placement support dete hain."
- Placement %: bridge to the counsellor visit, no number.
- Cert: Weltec certificate only, detail at the visit.
- Only permitted money statement anywhere: "No-cost EMI available hai." (nothing more)

## Certs — until D1 is answered
Claim ONLY the Weltec certificate. Block/soften any Google/Meta/IBM/government cert claim.

## Fail-safe
If the filter errors or is unavailable, the pipeline must **hard-fail the turn to a safe
canned line**, never pass raw LLM output. Absence of the filter = absence of a callable build.

## Roadmap bridge

The next step is to emit a structured `SafetyEvent` for every block or substitution and store it
durably without retaining unnecessary PII. That creates an auditable governance feature and a
real analytics dataset while the runtime guard remains deterministic.
