# Level 2 — Text conversation and OpenAI LLM

**Roadmap sections:** 10–12. **Status:** Section 10 state machine/checkpoint adapter implemented; section 11 OpenAI SDK/text integration implemented with offline evidence; section 12 safety audit remains pending.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement the thin OpenAI LLM provider/text console described in [the OpenAI contract](../25-openai-llm.md), with streaming, timeout/retry/fallback, per-call token/spend limits and actual usage metrics. Keep local ASR/TTS/embeddings for their later levels. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, `route_intent()`, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore remains the live hot path. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore through `PostgresConversationStateStore`, backed by `conversation_states` revision/schema/policy fields. Section 11 adds the thin OpenAI SDK provider, mock text console and separate 40-case lab. The structured safety audit remains section 12.

## Acceptance gate

- [x] Section 10 state-machine facade and latest checkpoint save/restore preserve stage/slots without inventing a booking.
- [x] Section 11 text controller/provider/safety/checkpoint wiring and mock console.
- [x] SDK streaming/usage, transient/quota errors, timeout/cancellation and per-call budgets pass fake-transport tests.
- [ ] Real paid TTFT/cost and native language-quality approval; quota unavailable.
- [ ] Blocked topics, prior quotes and prompt injection use safe substitution; safety events are PII-safe.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

No telephony/audio integration, local LLM download, model-owned FSM or LLM safety decisions.

## Evidence

Section 10 implementation is in source with these fresh checks: state-machine API tests passed **17/17**, focused PostgreSQL checkpoint tests passed **2/2**, Ruff passed across `roma`, `tests` and `scripts`, configured mypy passed, `make ci` passed its native-service/migration/mock-provider/API slice, the full offline suite passed **1,674 tests with 23 warnings**, and Markdown link/anchor verification covered **63 non-archive Markdown files and 274 local links/anchors with zero issues**.

This historical section 10 evidence proves the finite-state-machine facade, route classification seam and compatible latest-checkpoint save/restore. It did not prove section 11; the new offline section 11 evidence is separate below. Structured safety audit, native review, live integration, redial/session identity and L7 recovery drills remain pending. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

## Section 11 implementation and evidence

On 2026-10-12 the owner selected the OpenAI source. [Sections 1–10 were audited first](../roadmaps/sections-01-10-review.md); only then was the new section 11 text seam implemented. The main voice path remains GPT-4o with GPT-4o-mini extraction. No Qwen runtime, model weights, migration or working-data changes were introduced.

`OpenAILLM` satisfies `LLMProvider` using the installed OpenAI SDK 2.48.0. It streams chunks, reads final usage-only events and reports configured model, first-text TTFT, token counts, cached input, estimated/charged INR, attempts and usage-known status. A serialized instance owns one call's token/spend budgets. Every attempt reserves conservatively; trustworthy actual usage reconciles the reservation. Missing/invalid usage, failure and cancellation do not refund unknown charges. SDK retries are disabled; transient pre-text retries/backoff stay inside the total deadline. Quota/billing/auth failures and partial text are never retried.

The console/service traces the existing controller, locally parsed names, strict profile/time extraction, selected-state prompt, raw output, deterministic safety and final reply. It rejects contradictory days, absent offer selections and out-of-stage readback claims. Names and recognized identifiers are removed from outbound messages; free-text minimization is not a universal PII detector. Complete final text still crosses the existing safety and confirmation guards. The default checkpoint is process-local; section 10's PostgreSQL adapter can be injected for an existing persisted call. No appointment is committed.

Fresh final offline evidence: **1,716 tests passed, 23 existing dependency warnings, no skips**. Ruff passed; mypy passed across **23 files**; all pre-commit hooks passed across files. Bounded CI slices passed **56 provider/config tests** and **35 API/architecture/controller/evaluation/settings tests (1 warning)**. The 42 new cases include real-SDK HTTP mock transports, retry/quota/auth/budget/deadline/cancellation/accounting and default-budget SDK/controller integration checks, name privacy and code-owned discovery, schema/context rejection, final safety, one-call budget isolation, mock console traces and a private 40-case mock lab with blank scores. All validation runs came from test files; no paid inference or carrier call ran.

Markdown links/anchors were checked across **65 non-archive files and 293 local links/anchors with zero issues**. The unchanged new Word source is recorded by SHA-256 in the source mapping. The dependency lock is unchanged and resolves **108 packages**; local Qwen cache count remains zero. Current `roma` is at `20261004_0003 (head)` with 25 application tables, 0 application rows and metadata-matching keys/constraints/indexes, confirmed read-only before section 11.

The separate 40-case lab retains the owner-agreed means ≥4/5 per language, every score ≥3/5 and zero unsafe final replies. Native scoring and complete voice approval are deferred; quota is unavailable, so no real OpenAI TTFT/cost/quality claim is made. Section 12 durable safety events, broader injection/semantic-quality evidence, durable global spend reservations and later live audio integration remain open. Per-call budgets are process-local and do not replace existing production spend/dashboard controls. See [the provider contract and commands](../25-openai-llm.md).

Return to the [documentation index](../README.md).
