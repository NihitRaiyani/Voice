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

Reuse `filter.py`, `lexicon.py` and `normalize.py` under `roma/domain/safety/`. Match multilingual/transliterated token boundaries and preserve Indic combining marks; ASCII-style `\w` splitting loses meaning. Avoid substring matches in unrelated words. Fee/salary anchors retain their category across punctuation adjacent to an amount; punctuation cannot turn a prohibited amount into an unanchored number.

The lexical/number gate uses clause-scoped, per-category negation heuristics. A negated clause cannot unlock a separate affirmative claim; a fresh amount after negation and “not only” do not grant permission. Unknown certificate issuers are blocked as unverified, not just the original named issuers. Job/placement guarantees are blocked in English, Hindi and Gujarati. It sees Roma's intended line and cannot prove caller provenance or factual truth. Tests must cover false negatives/positives, quote pressure and split-chunk boundaries. Do not describe it as complete hallucination detection.

`safe_output` returns clean text, a category substitution or a hard-fail safe line. If validation is unavailable/errors, never pass raw output and never leave unexplained dead air. Cancellation clears partial generation and queued speech without opening an unfiltered path.

## Structured audit and storage

Level 2 section 12 implements `roma/domain/safety/audit.py`, `roma/services/safety_service.py` and `roma/repositories/postgres/safety.py`. The pure checker measures rule latency and produces one activation per blocked category; one approved replacement answers the sentence. Confirmation, availability, close and time-talk substitutions have separate `control.*.v2` identities. Direct guarded-question deflections also emit `route.*.v2` activations when they skip the model. Policy identity is `roma-safety-2`.

The service records events before returning a blocked replacement to its caller. `roma/main.py` injects PostgreSQL into the existing live pre-TTS path; the standalone console defaults to an explicitly synthetic in-memory sink. Inject `SafetyService(PostgresSafetyEventStore(session_factory))` alongside the section 10 checkpoint adapter when running a text call that already exists durably. No call record is invented to make an audit write succeed.

Reuse the existing `safety_events` columns: `rule` is the public `rule_id`, `original_category` is `category`, and `created_at` is `detected_at`. Metadata stores approved replacement text or a controller replacement identity, check `latency_ms`, policy version, turn number, stage and source. No raw model/caller text, match tokens, names or phone numbers are persisted in these events. No migration is needed. A missing turn gets a transcript-free agent row keyed by call/turn number; an existing turn is reused without overwriting it. Live agent numbering includes the opener as turn 1; user-triggered responses use controller turn count plus one. The text console has no autonomous opener and starts its first response at turn 1. Exact event-ID replay is idempotent. Retention inherits the call policy; this section does not introduce an institute retention/deletion policy.

Writes use one short transaction and a finite positive deadline (default 500 ms); the session is closed before forwarding text to TTS. A storage error/timeout returns `HARD_FAIL_LINE` and emits a structured `safety_audit_write_failed` signal without raw text or exception messages. That signal is not a claim of durable persistence during an outage. Cancellation propagates and does not release pending speech. Full operational retry/recovery/alerting remains later work. Repeated/trailing substitutions may still be suppressed after a safe line has already been spoken; every activation is audited before that established audio deduplication.

Authenticated `GET /api/v1/safety/events`, `GET /api/v1/safety/stats` and `GET /api/v1/calls/{call_id}/safety-events` use the existing envelopes. Lists support call/rule/category filters, pagination and ordering. Statistics count activations by category/rule, not calls or sentences; soft-deleted events are excluded. Legacy rows expose missing new metadata as null, never invented measurements. The bearer token is not Level 8 RBAC.

## Untrusted inputs and limits

Caller text must be a string, is NFC-normalized and is limited to 4,096 characters before and after normalization; control/bidirectional characters and prompt markup delimiters are rejected. Extracted free-text slots enforce a 120-character bound (names 64) before and after NFC normalization, preserve Indic letters/combining marks and permit only explicit punctuation. Field names, branches, time/weekday/period/offer values and passing-year format have code-owned validation. Non-string/rejected values use the safe rejection path without entering transcript storage or prompt substitutions; saved raw state is not silently rewritten. City/education are open vocabularies, not invented closed lists.

Recognized English/Hindi/Gujarati instruction-override canaries are answered deterministically and audited as `INPUT`; rejected utterances are removed from future live user history. An explicit request to end the call still sets the code-owned stop flag. These checks reduce risk; lexical matching is not complete semantic injection protection or universal fact/PII detection. Final output checks and existing consent/slot/timeout/hang-up controls remain mandatory.

## Later grounding and language approval

Level 9 supplies approved active/effective scoped document evidence, grounding and no-evidence deferral. Retrieval evidence stays separate from instructions and cannot weaken speech rules. Booking/control/consent use direct deterministic routes.

Maintain Gujarati/Devanagari/Latin injection canaries and native language review. Changed fixed wording requires matching audio identity; stale clips must be refused. See [prompt contract](11-prompts.md), [audio assets](../roma/realtime/assets/README.md) and [verification](12-verification.md).

The boundary audit also tests exact known money labels attached directly to amounts (`fees25000`, `30000salary`, Gujarati/Hindi equivalents). The split is limited to known label/amount pairs, preserving Indic combining marks, standalone `20k`, substring guards and explicit corrections.


## Local approved-line pronunciation

Section 19 local synthesis retains canonical safety wording in `lexicon.py` and uses the finite exact/hash-pinned bindings in `pronunciation.py` solely for pronunciation. These render the same Hindi-base Hinglish words in Devanagari and select Hindi synthesis even for a Gujarati/English caller route. No arbitrary caller text, general translator or caller-provided allowlist can obtain a binding.

`SpeechSynthesisService` audits the canonical decision, then `SafetyService.check_pronunciation()` validates current lexicon membership/both hashes and screens the actual rendering before model/cache access. The generic certificate rule flags the approved deferral's phonetic clause; only its exact pinned binding with CERT as the sole violation is adjudicated. Any other category, checker error, missing mapping or changed text fails before audio. A structured PRONUNCIATION event retains canonical replacement plus binding identity; canonical violations retain their original events. Rendering audit failure uses the independently screened pinned hard-fail pronunciation, reports `audit_ok=false` and does not recurse. Cancellation propagates. Legacy global checking and approved live assets remain unchanged.

Every cache hit repeats the checks/audit. Cache identity includes actual text/language/voice/model, normalization and pronunciation identities. The worker still rejects unsupported lexical characters for ordinary text. The [actual local safety lab](levels/level-05.md#approved-safety-pronunciation-follow-up--2026-10-19) proves rendering/cache/transport mechanics, not native pronunciation approval or a complete production voice gate.


Section 20's isolated local prototype additionally renders exact controller discovery/booking/readback/sign-off lines in Devanagari. Dynamic wording must equal the pinned current template reconstructed from controller slots; branch values are closed and confirmation still requires the existing SQL-owned state. These pronunciations receive the ordinary complete final safety check and cannot invoke the six-line safety binding exception. Generated local dialogue requests the same Hindi-base Hinglish policy in the native script. Unsupported text or audit/storage failure never releases raw model output; production prompts/assets are unchanged.


The local-base microphone follow-up adds exact finite pronunciations for the two existing confirmation re-offer constants in `roma/services/voice_pronunciation.py`. Constant equality guards reject stale or extended wording; normal final safety still screens spoken text. This repairs local rendering when absent booking setup produces the existing re-offer, without changing business/safety wording, approving a booking or changing production assets. See [Level 6](levels/level-06.md#local-base-microphone-follow-up--2026-10-19).
