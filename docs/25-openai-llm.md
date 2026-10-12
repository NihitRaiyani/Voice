# OpenAI LLM provider and text conversation

**Level 2 section 11.** The selected [OpenAI roadmap](roadmaps/README.md) requires a thin `OpenAILLM` behind `LLMProvider`, with a text-only console. The design reuses the installed OpenAI SDK and current seven-stage controller. It preserves the existing GPT-4o/GPT-4o-mini voice baseline and does not reinstall local LLMs or change speech providers.

## Implementation contract

Use an injectable `AsyncOpenAI` Chat Completions client. It fits the existing SDK and model family; wrapping Pipecat would hide direct token accounting, while changing API/model family would add migration scope. Read API key, model, generation limits, timeout/retry limits and per-call token/spend caps from environment settings. Default factory/console tests to mocks. Selecting `openai` requires an explicit model/key and a separately authorized live run; no SDK/client is constructed for mock use.

Stream text chunks and collect the final usage-only event. Report request-to-first-text TTFT, attempts, input/output/cached tokens, estimated INR and whether usage is known. This is model/API timing, not end-of-speech-to-audio. Disable SDK automatic retries so one bounded retry policy owns the total deadline. Retry only transient connection/timeout/rate-limit/5xx failures before any text, with backoff; quota, billing, auth and invalid requests fail immediately. Never replay a partially emitted stream. Close streams on completion/error/cancellation, and propagate cancellation.

Each text call owns one provider/budget instance. Reserve a conservative input-byte bound plus the output cap before every attempt; reconcile only trustworthy reported usage. Missing usage, timeout, cancellation and ambiguous remote failures retain the reservation. Exhausted token/spend budgets make no SDK request and use the existing approved fallback. These are process-local per-call guards, not an invoice or durable global reservation system; keep the current live spend ledger/dashboard protection.

The controller runs before wording. Separate schema-validated profile/time extraction from dialogue, await critical results, resolve dates and confirmations in existing code, and checkpoint after safety. Names are parsed locally; known names, phone numbers, hashes, email/recording URLs and local recording paths are excluded/redacted from outbound messages. Use selected state fields only, never full caller/call records. Free text may contain unrecognized personal data: minimization/redaction is defense in depth, not a universal PII detector. Console traces are synthetic and never ordinary production logs.

The console shows state, extracted slots, prompt, raw output, safety and final reply. Raw streamed text is diagnostic only; safety must approve complete sentences before speech. Provider errors discard partial wording in the text service and choose the approved safe line. The console commits no appointments, and conversational `locked_slot` is not a database reservation.

## Quality and integration boundaries

Live/text production policy remains Hindi-base Hinglish. A separate 40-case Gujarati/Hindi/English/code-mix lab retains the agreed native review gate: mean correctness and naturalness at least 4/5 per language, every score at least 3/5 and zero unsafe final replies. Mock artifacts cannot pass; never manufacture native scores or measured OpenAI latency while quota is unavailable. Native scoring and complete voice/interruption approval remain deferred by owner instruction.

Section 12 owns durable structured safety events and broader injection evidence. Later speech/telephony levels own integration of this service with the audio path, real TTFA, cancellation/capacity and target local ASR/TTS. The existing Pipecat voice path still uses its established direct SDK processors; this text seam does not silently change live behavior.

## Official API references

[Streaming Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) documents streaming and final usage reporting. [Error codes](https://developers.openai.com/api/docs/guides/error-codes) and [rate limits](https://developers.openai.com/api/docs/guides/rate-limits) distinguish transient retryable failures from quota/billing errors. Implementation tests use the installed SDK and fake transports; no paid API calls run.

## Commands and files

`roma/providers/ai/openai_llm.py` owns SDK streaming/retry/accounting. `roma/core/openai_llm_settings.py` owns validated environment settings. `roma/core/prompt_privacy.py` removes recognized identifiers. `roma/services/text_conversation_service.py` composes the existing controller and safety; `conversation_prompts.py` builds its narrow prompt. No dependency or model download is needed beyond the installed locked `telephony` extra (OpenAI SDK 2.48.0).

```bash
make text-console   # synthetic callers, mock LLM, no keys or carrier/DB work
make language-lab   # 40 mock outputs; blank native scores, never quality evidence
make test-ci        # SDK mock transports + mocked API/controller/evaluation checks
make test           # full existing offline suite, disposable databases only
```

The scripts default to `--provider mock` even if `.env` selects a paid adapter. OpenAI selection refuses startup unless `--allow-paid-api` is explicitly supplied. While quota is unavailable, use the mock commands/tests. No automatic paid fallback to another model/provider exists.

For a later authorized text run, set `OPENAI_API_KEY` and `OPENAI_MODEL` in the environment and select `--provider openai --allow-paid-api` on `scripts/text_console.py` or `scripts/openai_language_lab.py`. The current supported pricing registry is GPT-4o/GPT-4o-mini; another model requires reviewed pricing/API compatibility first. `.env.example` lists all timeout, retry, generation and per-call budget settings. Values there are development defaults, not accepted SLOs or billing guarantees. General app settings still default factory calls to mocks; the existing audio baseline keeps its established GPT-4o model.

Lab captures use owner-only ignored `var/roma/openai-lab/`. A later native reviewer fills the existing 1–5 scores and reviewer name, then uses `--score <artifact>`; stale prompts/decoding settings, missing reviewers or mock-only outputs cannot pass. We only exercise mock captures/pending-review behavior inside test files during this increment.

Local-name parsing is deliberately conservative: unclear/mixed name declarations re-ask locally. It is not an arbitrary-name NLP model. A restarted process creates a new text budget; durable cross-process/global spending and audio integration retain their later gates and the existing production protection.

Final verification evidence is recorded in the level brief. See [Level 2](levels/level-02.md), [the sections 1–10 review](roadmaps/sections-01-10-review.md) and [the runbook](runbook.md).
