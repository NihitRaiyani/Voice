# Level 2 — Text conversation and local LLM

**v4 sections:** 10–12. **Status:** Section 10 state machine/checkpoint adapter implemented; sections 11–12 local inference and structured safety audit pending.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement direct Qwen3-8B Transformers inference as a candidate provider; measure hardware, tokenization/decoding, TTFT, tokens/s, VRAM, quality and schema behavior. Preserve cloud generation as an explicit comparison profile. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, `route_intent()`, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore remains the live hot path. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore through `PostgresConversationStateStore`, backed by `conversation_states` revision/schema/policy fields. Direct-inference runners and the structured safety audit still need implementation.

## Acceptance gate

- [x] Section 10 state-machine facade and latest checkpoint save/restore preserve stage/slots without inventing a booking.
- [ ] Full text-flow demo wiring with local inference still needs sections 11–12.
- [ ] Real local inference works without external AI calls; structured extraction is validated.
- [ ] Blocked topics, prior quotes and prompt injection use safe substitution; safety events are PII-safe.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

No telephony/audio integration, Ollama-first shortcut, vLLM, model-owned FSM or LLM safety decisions.

## Evidence

Section 10 implementation is in source with these fresh checks: state-machine API tests passed **17/17**, focused PostgreSQL checkpoint tests passed **2/2**, Ruff passed across `roma`, `tests` and `scripts`, configured mypy passed, `make ci` passed its native-service/migration/mock-provider/API slice, the full offline suite passed **1,674 tests with 23 warnings**, and Markdown link/anchor verification covered **63 non-archive Markdown files and 274 local links/anchors with zero issues**.

This evidence proves the finite-state-machine facade, route classification seam and compatible latest-checkpoint save/restore. It does not prove sections 11–12: local Qwen inference, structured safety-event audit, native language review, redial/session identity or L7 recovery drills. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
