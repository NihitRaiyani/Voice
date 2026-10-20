# Claude LLM provider and text conversation

**Level 2 section 11; current source aligned through section 20.** The owner selected [the Claude roadmap](roadmaps/README.md) on 2026-10-20. Claude Haiku 5.5 (`claude-haiku-5-5`) is the main wording/extraction model behind `LLMProvider`; the API key is `ANTHROPIC_API_KEY` in the existing private `.env`. The official async Anthropic SDK stays behind `ClaudeLLM`, with `ClaudeLLMSettings` for model, deadlines, retries and per-call token/spend limits. No OpenAI request or fallback is allowed.

## Streaming, caching and accounting

Use `AsyncAnthropic` and its Messages async streaming helper. Keep stable system rules first and eligible stable content marked for ephemeral prompt caching; pass current state and one narrow task separately. Use `max_tokens` and `output_config={"effort": "low"}`. Do not send temperature, top_p or top_k. For these short code-owned dialogue/extraction tasks, Haiku's optional thinking is explicitly disabled while effort stays low; two initial adaptive-thinking close cases exhausted the output cap before producing text. Empty completions are a metered safe failure, never successful speech. Haiku 5.5 supports prompt caching from 512 tokens; cache eligibility is not proof that a request hit the cache. Do not pad prompts to force eligibility or claim a hit from the marker alone. See the official [effort settings](https://platform.claude.com/docs/en/build-with-claude/effort) and [prompt-caching contract](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Emit transport-neutral text chunks; capture first-text TTFT and reconcile final Messages usage. Distinguish uncached input, cache-creation input, cache-read input and output tokens. Use the exact configured model's reviewed pricing, with separate cache-write/read rates; never apply a universal cache discount or silently price an unknown model as another model. Report usage-known status, attempts, actual usage and estimated INR without treating an estimate as an invoice. Provider TTFT is not end-of-speech-to-first-audible-response. [Anthropic streaming documentation](https://platform.claude.com/docs/en/build-with-claude/streaming) defines content and usage events.

Disable SDK retries so the adapter owns one total deadline. Retry only safe transient pre-text connection/timeout, 429 or 5xx failures within that budget, honoring bounded retry-after advice. Auth, invalid requests, billing/credit errors and exhausted budgets fail immediately. Never replay a partially emitted stream. Close the stream on success, error and cancellation; propagate cancellation. Generation failure discards partial wording and uses an approved safe line.

A call-scoped instance owns conservative token/spend admission. Reserve a conservative input bound plus output cap before each attempt; reconcile only trustworthy final usage. Missing usage, cancellation and ambiguous failures retain the reservation. Cache creation may cost more than ordinary input, so admission must include the applicable cache-write charge. Exhausted budgets make no SDK call. These guards are process-local; durable cross-process reservations and comprehensive metering remain later work. Preserve the protected live ledger's fail-closed behavior.

## Reviewed model price and settings

The official [model overview](https://platform.claude.com/docs/en/models/overview) identifies Haiku 5.5. Its reviewed [pricing table](https://platform.claude.com/docs/en/about-claude/pricing), for prompts at or below 100,000 tokens, lists USD prices per million tokens:

| Usage | USD / million tokens |
|---|---:|
| Ordinary input | 0.10 |
| Output | 0.50 |
| 5-minute cache creation | 0.125 |
| Cache read | 0.01 |

The retained conversion factor of 100 INR/USD is a conservative budget estimate, not a market exchange-rate claim or invoice. Cache creation, reads and ordinary input are separately charged; caching does not make unknown usage free. The adapter is validated against Anthropic SDK 0.125.0.

`ClaudeLLMSettings` keeps the narrow text lab at 16 KiB prompt bytes, 20,000 call tokens and ₹2 per-call spend cap. General application settings preserve the larger existing production prompt envelope with 32 KiB and 64,000 call tokens, retaining the same ₹2 cap; this accommodates established prompt material without weakening stage or safety rules. Deadline/retry bounds remain explicit environment settings. These are development guardrails, not accepted latency SLOs or guaranteed provider capacity.

## Controller, privacy and safety

Code chooses stage, dates, valid offers, booking commit, consent and hang-up. Await bounded schema-validated extraction before controller decisions. Parse names locally, minimize selected state fields and redact recognized names/phone/hash/email/recording identifiers before outbound requests. Do not send full caller records, phone numbers, hashes or recordings. Redaction cannot guarantee arbitrary prose contains no personal data.

The console displays synthetic state, extracted slots, prompt, raw output, safety result and final reply. Raw chunks are diagnostic; final deterministic safety owns all speech. A conversational locked slot is not a reservation. Text-console startup does not seed or commit appointments. Production output remains Hindi-base Hinglish, feminine self-reference and respectful plural address. The separate multilingual lab covers Gujarati, Hindi, English and code-mix without changing production policy.

## Commands and boundaries

```bash
make text-console                  # mock, no keys/network/DB/carrier
make language-lab                  # mock cases with blank native reviews
make voice-prototype               # synthetic microphone/speaker exercise
make test-ci                       # mock SDK/provider and bounded service checks

# Authorized synthetic Claude text verification; reads the existing private .env.
uv run --no-sync python scripts/text_console.py --provider claude --allow-paid-api
uv run --no-sync python scripts/claude_language_lab.py --provider claude --allow-paid-api
```

Both scripts default to mock even when the environment selects Claude. Paid selection requires the explicit CLI opt-in. `ANTHROPIC_MODEL=claude-haiku-5-5` selects the reviewed default; `.env.example` owns all supported anthropic deadline/retry/output/token/spend settings. No key values or remote error bodies belong in output or Git. Lab artifacts use private ignored runtime storage; native scores remain blank until a named reviewer supplies them.

The 40-case lab retains the agreed native gate: mean correctness and naturalness at least 4/5 per language, every score at least 3/5, zero unsafe final replies. Mock captures and a paid synthetic smoke do not pass native quality or microphone/booking approval. Higher effort/Sonnet evaluation needs a failed quality gate and an explicit bounded decision; no automatic model switch.

Section 20 accepts `--llm-provider claude|mock`; local capture requires microphone/database opt-ins, and Claude additionally requires `--allow-paid-api`. Its ASR/TTS remain cache-only local adapters. The only approved ASR weights are the pinned Whisper base artifact; multilingual quality failed in its separate completed benchmark. Hosted ASR cannot substitute for the Claude-only-cloud microphone requirement. Full native multilingual voice acceptance remains pending.

Source modules: `roma/providers/ai/claude_llm.py`, `roma/core/claude_llm_settings.py`, `roma/core/prompt_privacy.py`, `roma/services/text_conversation_service.py` and `roma/services/conversation_prompts.py`. [Provider contracts](23-provider-contracts.md) own shared types; [Level 2](levels/level-02.md) and [Level 6](levels/level-06.md) own evidence; [the runbook](runbook.md) owns operation. The [old OpenAI contract](25-openai-llm.md) is historical only.


## Migration verification — 2026-10-20

The existing private key authenticated to the model metadata endpoint and two bounded synthetic speech-text requests. The first 40-case low-effort lab completed with trustworthy usage for all cases, but two close-stage cases returned no text at the output limit. That report is retained separately; final runtime sends `thinking={"type":"disabled"}` at low effort, which [Haiku supports](https://platform.claude.com/docs/en/build-with-claude/effort#recommended-effort-levels-for-claude-haiku-55). Output-token caps, safety and native-quality gates remain unchanged.

Real structured extraction initially rejected unsupported numeric schema constraints. `ClaudeLLM` now uses the official SDK `transform_schema` for the wire schema; application Pydantic validation retains the original bounds. A separate readiness run used a 15-second attempt ceiling, followed by a successful ordinary **4-second**-deadline request. Both extracted the synthetic student status and advanced to the next missing discovery field. Actual usage showed **807 cache-creation tokens**, then **807 cache-read tokens**, with no invented hit discount. The [final default-configuration check](../benchmarks/llm/claude/default-extraction.json), with thinking disabled, also validated extraction in one attempt (1.654 s) and recorded 806 cache-creation tokens. Readiness is an explicit synthetic verification action, not permission for hidden paid startup calls or a relaxed live-turn deadline.

Actual safe Hindi-base Hinglish text from Haiku also synthesized through existing local TTS with `audit_ok=true`, 2.822 seconds of PCM and no new weights or microphone use. This is an integration check, not native listening or a full three-language microphone/booking pass. The earlier approved English microphone/mock-LLM evidence remains historical and valid for that narrower boundary.


The final [40-case report](../benchmarks/llm/claude/language-review.json) with thinking disabled returned **40 nonempty text replies / 40 requests**, all with trustworthy usage: **22,284 input / 3,824 output tokens**, estimated paisa-rounded lab charges **₹0.61** using the retained conservative conversion factor. Median first-text TTFT was **1.239 s** (range 1.015–2.760 s); this is not end-to-end audio or production p95. The [initial adaptive report](../benchmarks/llm/claude/initial-adaptive-review.json) remains separate with its two empty close cases. Both reports leave native scores blank. The short dialogue prefixes recorded zero cache tokens; no padding was introduced. The [separate real extraction check](../benchmarks/llm/claude/schema-readiness.json) demonstrates actual 807-token cache creation/read behavior. See the [evidence index](../benchmarks/llm/claude/README.md).
