# Level 2 — Text conversation and local LLM

**v4 sections:** 10–12. **Status:** Sections 10–11 state machine/checkpoint adapter, direct Transformers provider, text console and language lab implemented. Qwen3-8B suitable-host inference/native quality review and section 12 structured safety audit remain pending.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement direct Qwen3-8B Transformers inference as a candidate provider; measure hardware, tokenization/decoding, TTFT, tokens/s, VRAM, quality and schema behavior. Preserve cloud generation as an explicit comparison profile. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, `route_intent()`, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore remains the live hot path. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore through `PostgresConversationStateStore`, backed by `conversation_states` revision/schema/policy fields. Section 11 now adds `Qwen3TransformersLLM`, `TextConversationService`, console and language-evaluation runners, local-only settings and token-level timing. Structured safety audit and real 8B/native evidence remain pending. See [direct local inference](../25-local-transformers-llm.md).

## Acceptance gate

- [x] Section 10 state-machine facade and latest checkpoint save/restore preserve stage/slots without inventing a booking.
- [x] Section 11 text-flow console wired to the provider/FSM/extraction/safety/checkpoint contracts; mock smoke verified.
- [x] 40-case multilingual corpus and agreed per-language score gate; native review cannot be auto-filled.
- [ ] Real local inference works without external AI calls; structured extraction is validated.
- [ ] Blocked topics, prior quotes and prompt injection use safe substitution; safety events are PII-safe.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

No telephony/audio integration, Ollama-first shortcut, vLLM, model-owned FSM or LLM safety decisions.

## Evidence

Section 10 implementation is in source with these fresh checks: state-machine API tests passed **17/17**, focused PostgreSQL checkpoint tests passed **2/2**, Ruff passed across `roma`, `tests` and `scripts`, configured mypy passed, `make ci` passed its native-service/migration/mock-provider/API slice, the full offline suite passed **1,674 tests with 23 warnings**, and Markdown link/anchor verification covered **63 non-archive Markdown files and 274 local links/anchors with zero issues**.

This section 10 evidence proves the finite-state-machine facade, route classification seam and compatible latest-checkpoint save/restore. It does not prove real Qwen3-8B inference, structured safety-event audit, native language review, redial/session identity or L7 recovery drills. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

## Section 11 implementation and verification

The adapter uses direct Transformers tokenization/device placement/decoding, lazy optional SDK loading, non-thinking Qwen chat templates, configured completion/prompt limits and serialized cancellable generation. Terminal chunks carry model/revision, prompt/generated token counts, TTFT, tokens/second, load time and CUDA memory when applicable. The text service reuses controller/date/guardrail code with validated local extraction; the console displays every requested turn boundary. No new migration or working-database change is needed.

The multilingual lab has 40 synthetic prompts across all seven stages and Gujarati/Hindi/English/code-mix. On 2026-10-07 the owner agreed mean correctness/naturalness ≥4/5 **per language**, every score ≥3/5 and zero unsafe final replies. Native reviewer assignment and actual scores are pending. The current live policy stays Hindi-base Hinglish.

Fresh offline evidence on 2026-10-07: the full suite passed **1,705 tests, 23 existing warnings**, including loading/admission checks. The expanded CI test slice passed **59 provider/config tests** and **21 unit/API/architecture/evaluation tests (1 existing warning)**. Ruff passed; mypy passed across **21 source files**. All pre-commit hooks passed across files. Markdown verification checked **64 non-archive Markdown files and 287 local links/anchors with zero issues**. Console mock smoke displayed the full trace, mock lab generation produced **40 outputs**, and unscored native review correctly exited nonzero. Existing `.env` HF credential alias was recognized without exposing its value.

Hardware evidence: `sysctl -n hw.memsize hw.machine` reported **8,589,934,592 bytes and arm64**. Qwen3-8B full precision is not admitted on this host; no 8B TTFT, throughput or native quality score is claimed. The locked optional runtime installed (Torch **2.14.1**, Transformers **4.57.6**). Real `check_transformers_runtime.py` smoke passed with a tiny random-weight CPU/FP32 Qwen3 model: five prompt tokens, three generated tokens, first-token timing and terminal metrics present. This is SDK-mechanics evidence only. A real runtime admission check selected MPS/FP16 and refused the 8B load before any weights/download. The [local LLM guide](../25-local-transformers-llm.md) owns commands/precision/score procedure. Section 12 durable structured safety events, suitable-host/native review, live integration and wider recovery evidence remain open.

Return to the [documentation index](../README.md).
