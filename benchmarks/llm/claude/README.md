# Claude Haiku migration evidence

These are explicitly authorized synthetic lab results, with no real caller records, secrets, recordings or OpenAI requests. Native reviewer scores remain blank. The active [Claude contract](../../../docs/25-claude-llm.md) owns settings/pricing and [Level 2](../../../docs/levels/level-02.md) owns the acceptance gate.

- [Model access](model-access.json): metadata retrieval confirms requested Haiku availability.
- [Initial adaptive run](initial-adaptive-review.json): 40 completed API requests, but only 38 text replies; two close cases exhausted output allowance without text. Retained as failure evidence, not a passed quality gate.
- [Final language run](language-review.json): same 40 [scripted cases](../language_cases.json), low effort and thinking disabled; all 40 returned nonempty text with trustworthy usage. Row `generation.temperature` is the old transport-neutral diagnostic field; Claude never receives it, as the artifact's `temperature_sent=false` states. Native scores are unfilled.
- [Schema readiness](schema-readiness.json): real validated student extraction, then repeat at the normal 4-second attempt budget; 807 cache-creation tokens followed by 807 cache-read tokens. Official SDK schema transformation retains full local Pydantic validation. The longer readiness ceiling is an explicitly isolated probe, not live-call behavior.
- [Final default extraction](default-extraction.json): thinking disabled, 4-second attempt / 10-second turn budget and one allowed retry; valid extraction succeeded in one attempt (1.654 s), recording 806 cache-creation tokens.
- [Local TTS](local-tts.json): an actual safe Hindi-base Hinglish Haiku line rendered through existing installed TTS; no new models or microphone use.
- [Summary](summary.json): recorded totals and timing scope. Estimated INR uses the inherited conservative 100 conversion factor and per-request paisa ceilings, not an invoice or exchange-rate claim.

The final 40-case run used 22,284 input and 3,824 output tokens, with estimated rounded charges ₹0.61. Median TTFT was 1.239 s across 40 text replies; this includes remote request/network timing and is not first audible response or a production p95. Short narrow dialogue prefixes did not generate cache hits; no padding was added. Cache functionality was separately demonstrated by the real structured-extraction prefix. Original ASR/TTS/microphone reports and failed adaptive output are preserved.
