# Final speech safety and grounding

Every generated spoken sentence must pass deterministic safety before synthesis. This includes cloud/local models, retrieved answers, streamed chunks and interruption/teardown. Cached fixed speech must be approved and match its manifest.

## Policy and ownership

| Category | Prohibited output | Approved route |
|---|---|---|
| Fee/EMI | Amounts, ranges, instalment figures | Counselling bridge; approved no-cost EMI statement only |
| Salary/package | Pay figures and outcome promises | Skills/support wording without promised outcomes |
| Placement | Statistics, ratios and guarantees | Qualified support, no promised placement |
| Discount | Discounts, offers and promotional schemes | Approved counselling bridge |
| Certificate | Unapproved Google/Meta/IBM/government/external claims | Weltec certificate only until owner approval |

Do not invent course or personal facts. Caller quotes and retrieved documents cannot unlock blocked claims. Exact approved substitution wording is in `roma/domain/safety/lexicon.py`, not copied into this document.

## Enforcement and limits

Reuse `filter.py`, `lexicon.py` and `normalize.py` under `roma/domain/safety/`. Match multilingual/transliterated token boundaries and preserve Indic combining marks; ASCII-style `\w` splitting loses meaning. Avoid substring matches in unrelated words.

The current lexical/number gate uses negation heuristics. It sees Roma's intended line and cannot prove caller provenance or factual truth. Tests must cover false negatives/positives, quote pressure and split-chunk boundaries. Do not describe it as complete hallucination detection.

`safe_output` returns clean text, a category substitution or a hard-fail safe line. If validation is unavailable/errors, never pass raw output and never leave unexplained dead air. Cancellation clears partial generation and queued speech without opening an unfiltered path.

## v4 additions

Level 2 adds structured safety audit events with rule/action/policy identity and PII-safe correlation. Level 9 supplies approved active/effective scoped document evidence, grounding and no-evidence deferral. Retrieval evidence stays separate from instructions and cannot weaken speech rules. Booking/control/consent use direct deterministic routes.

Maintain Gujarati/Devanagari/Latin injection canaries and native language review. Changed fixed wording requires matching audio identity; stale clips must be refused. See [prompt contract](11-prompts.md), [audio assets](../roma/realtime/assets/README.md) and [verification](12-verification.md).
