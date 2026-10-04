# Level 2 — Text conversation and local LLM

**v4 sections:** 10–12. **Status:** Deterministic cloud text logic present; local inference/durable restore gate pending.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement direct Qwen3-8B Transformers inference as a candidate provider; measure hardware, tokenization/decoding, TTFT, tokens/s, VRAM, quality and schema behavior. Preserve cloud generation as an explicit comparison profile. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore is transient.

## Acceptance gate

- [ ] Text flow and state restore preserve slots/phase without inventing a booking.
- [ ] Real local inference works without external AI calls; structured extraction is validated.
- [ ] Blocked topics, prior quotes and prompt injection use safe substitution; safety events are PII-safe.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

No telephony/audio integration, Ollama-first shortcut, vLLM, model-owned FSM or LLM safety decisions.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
