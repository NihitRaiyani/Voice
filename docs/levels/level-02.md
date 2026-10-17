# Level 2 — Text conversation and OpenAI LLM

**Roadmap sections:** 10–12. **Status:** Section 10 state machine/checkpoint adapter implemented; section 11 OpenAI SDK/text integration implemented with offline evidence; section 12 deterministic safety/audit/input guards and query endpoints implemented with offline evidence; the whole Level 2 gate remains pending native/paid quality approval.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement the thin OpenAI LLM provider/text console described in [the OpenAI contract](../25-openai-llm.md), with streaming, timeout/retry/fallback, per-call token/spend limits and actual usage metrics. Keep local ASR/TTS/embeddings for their later levels. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, `route_intent()`, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore remains the live hot path. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore through `PostgresConversationStateStore`, backed by `conversation_states` revision/schema/policy fields. Section 11 adds the thin OpenAI SDK provider, mock text console and separate 40-case lab. Section 12 adds structured safety audit, bounded caller/slot inputs and query endpoints; the existing pre-TTS processor shares the same audit service.

## Acceptance gate

- [x] Section 10 state-machine facade and latest checkpoint save/restore preserve stage/slots without inventing a booking.
- [x] Section 11 text controller/provider/safety/checkpoint wiring and mock console.
- [x] SDK streaming/usage, transient/quota errors, timeout/cancellation and per-call budgets pass fake-transport tests.
- [ ] Real paid TTFT/cost and native language-quality approval; quota unavailable.
- [x] Section 12 blocked topics, prior quotes and injection canaries use safe substitution with PII-minimized structured audit and protected queries.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

Section 12 connects audit to the existing pre-TTS processor without changing carriers or integrating the section 11 text orchestrator into live audio. Local speech models, full voice approval and production telemetry stay at their later gates. No local LLM download or model-owned FSM/safety decisions.

## Evidence

Section 10 implementation is in source with these fresh checks: state-machine API tests passed **17/17**, focused PostgreSQL checkpoint tests passed **2/2**, Ruff passed across `roma`, `tests` and `scripts`, configured mypy passed, `make ci` passed its native-service/migration/mock-provider/API slice, the full offline suite passed **1,674 tests with 23 warnings**, and Markdown link/anchor verification covered **63 non-archive Markdown files and 274 local links/anchors with zero issues**.

This historical section 10 evidence proves the finite-state-machine facade, route classification seam and compatible latest-checkpoint save/restore. It did not prove section 11; the new offline section 11 evidence is separate below. At that section 10 checkpoint, safety audit was still pending; section 12 now implements it. Native review, full live integration, redial/session identity and L7 recovery drills remain pending. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

## Section 11 implementation and evidence

On 2026-10-12 the owner selected the OpenAI source. [Sections 1–10 were audited first](../roadmaps/sections-01-10-review.md); only then was the new section 11 text seam implemented. The main voice path remains GPT-4o with GPT-4o-mini extraction. No Qwen runtime, model weights, migration or working-data changes were introduced.

`OpenAILLM` satisfies `LLMProvider` using the installed OpenAI SDK 2.48.0. It streams chunks, reads final usage-only events and reports configured model, first-text TTFT, token counts, cached input, estimated/charged INR, attempts and usage-known status. A serialized instance owns one call's token/spend budgets. Every attempt reserves conservatively; trustworthy actual usage reconciles the reservation. Missing/invalid usage, failure and cancellation do not refund unknown charges. SDK retries are disabled; transient pre-text retries/backoff stay inside the total deadline. Quota/billing/auth failures and partial text are never retried.

The console/service traces the existing controller, locally parsed names, strict profile/time extraction, selected-state prompt, raw output, deterministic safety and final reply. It rejects contradictory days, absent offer selections and out-of-stage readback claims. Names and recognized identifiers are removed from outbound messages; free-text minimization is not a universal PII detector. Complete final text still crosses the existing safety and confirmation guards. The default checkpoint is process-local; section 10's PostgreSQL adapter can be injected for an existing persisted call. No appointment is committed.

Fresh final offline evidence: **1,716 tests passed, 23 existing dependency warnings, no skips**. Ruff passed; mypy passed across **23 files**; all pre-commit hooks passed across files. Bounded CI slices passed **56 provider/config tests** and **35 API/architecture/controller/evaluation/settings tests (1 warning)**. The 42 new cases include real-SDK HTTP mock transports, retry/quota/auth/budget/deadline/cancellation/accounting and default-budget SDK/controller integration checks, name privacy and code-owned discovery, schema/context rejection, final safety, one-call budget isolation, mock console traces and a private 40-case mock lab with blank scores. All validation runs came from test files; no paid inference or carrier call ran.

Markdown links/anchors were checked across **65 non-archive files and 293 local links/anchors with zero issues**. Hosted CI for implementation commit `f7df8e0` completed successfully on 2026-10-12 ([run](https://github.com/NihitRaiyani/Voice/actions/runs/38220411614)). It includes lint, type checks, native service startup, empty-database migration and the mocked section 11/provider/API slices. This hosted result is separate from the full 1,716-test local integration suite.

The unchanged new Word source is recorded by SHA-256 in the source mapping. The dependency lock is unchanged and resolves **108 packages**; local Qwen cache count remains zero. Current `roma` is at `20261004_0003 (head)` with 25 application tables, 0 application rows and metadata-matching keys/constraints/indexes, confirmed read-only before section 11.

The separate 40-case lab retains the owner-agreed means ≥4/5 per language, every score ≥3/5 and zero unsafe final replies. Native scoring and complete voice approval are deferred; quota is unavailable, so no real OpenAI TTFT/cost/quality claim is made. Section 12 durable safety events, broader injection/semantic-quality evidence, durable global spend reservations and later live audio integration remain open. Per-call budgets are process-local and do not replace existing production spend/dashboard controls. See [the provider contract and commands](../25-openai-llm.md).

Return to the [documentation index](../README.md).


## Section 12 implementation and evidence

On 2026-10-17 section 12 extended the existing filter instead of replacing it. Speech rules now collect every blocked category, scope negation per clause/category, reject unknown/external certification wording and cover English/Hindi/Gujarati money, discount and job-guarantee canaries. Approved substitution wording and its audio assets remain unchanged. Direct guarded-question deflections also create audit events when they skip generation.

`SafetyService` connects PII-minimized rule activations to an injected event store in the text seam and existing pre-TTS processor. Production composition injects PostgreSQL. Writes reuse the existing safety/turn schema, close their short transaction before forwarding replacement text and have a 500 ms deadline. Exact event replay is idempotent; storage failure/timeout produces safe fallback speech and an explicit failure signal, not a false durable-success claim. See [audit field mapping and outage limits](../04-guardrails.md#structured-audit-and-storage).

Prompt inputs have character/length limits and code-owned field/branch/time domains. Recognized override canaries skip generation, are audited and cannot override an explicit stop request. Saved state is not silently rewritten by prompt validation. Authenticated event lists, grouped statistics and call-specific safety events expose stable envelopes, filtering/pagination and private 401/404/422/503 failures. This does not complete Level 8 RBAC or universal semantic injection/factual verification.

Final local evidence: **1,790 tests passed, 23 existing dependency warnings, no skips**; Ruff passed; mypy passed across **31 files**; every pre-commit hook passed. `make test-ci` passed **56 provider/config tests** plus **197 bounded safety/API/controller/evaluation tests (2 warnings)**. The suite covers multilingual generated claims and caller questions, deterministic deflections, quote/negation and certificate bypasses, poisoned slots, audit outage/timeout/cancellation, pre-TTS substitution/deduplication, turn correlation, native disposable PostgreSQL replay/FKs/retention/soft-delete/query counts and real database-outage envelopes. Native PostgreSQL safety fixtures are short-lived and run without Docker. Database suites run serially to avoid local shared-memory pressure. Existing local tokenizer test data was restored without network download.

The configured database was checked read-only at **20261004_0003 (head)**. No working-database migration/seed/data change, provider call or model download was performed. The selected Word source and dependency lock are unchanged. Markdown local links/anchors and whitespace checks passed. Hosted CI includes the new safety slice; its run result is reported separately after publication.

Section 12 implementation is complete with offline evidence. **The overall Level 2 gate is still pending native Gujarati/Hindi quality review and real paid timing/voice approval**, as previously deferred by the owner. Production speech remains Hindi-base Hinglish, with multilingual output in its separate lab.
