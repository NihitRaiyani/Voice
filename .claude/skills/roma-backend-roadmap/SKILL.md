---
name: roma-backend-roadmap
description: >-
  Guide staged backend-platform work in Roma. Load for architecture, PostgreSQL,
  Redis boundaries, appointments, transactions, APIs, authentication/RBAC,
  webhooks, background jobs, idempotency, audit/cost data, observability,
  testing, Docker, or CI/CD. Keeps implemented behavior separate from planned
  work and prevents decorative complexity from entering the realtime voice path.
---

# Roma backend roadmap

Use this skill to turn one requested OpenAI-roadmap level into a focused, testable backend increment. The
authoritative project context is `CLAUDE.md`; the selected source and gates are `docs/roadmaps/README.md`, `docs/levels/` and `docs/10-build-order.md`. Topic contracts and readiness are indexed by `docs/README.md`.

## Start with status and scope

Verify the repository is `/Users/nihitraiyani/Voice_Agent`. Implement only the level or section explicitly requested by the user; the current increment implements explicit official Whisper authentication and section 20 isolated microphone integration, preserving sections 14–19 and earlier foundations. Required TTS runtime/weight downloads were explicitly approved on 2026-10-19; The later owner approval allows only the pinned multilingual Whisper base download; inference stays cache-only. Live replies remain Hindi-base Hinglish, with separate multilingual labs. Existing PostgreSQL, booking repository, signed answer and Dramatiq jobs are reusable foundations, not full gate passes.

Before proposing a change:

1. Identify whether the capability is **Implemented**, **Next**, **Planned**, or **Optional**.
2. Select one vertical slice with a user/operator outcome; do not implement the whole roadmap.
3. State the business invariant, failure modes, and evidence required for completion.
4. Preserve the current Twilio voice pipeline unless the user explicitly changes it.

Documentation is not implementation. Never mark a module implemented until code, tests, failure
handling, and operator guidance exist.

## Architecture boundaries

- Default to a modular monolith.
- Keep realtime audio, VAD, STT, LLM, safety, and TTS latency-sensitive.
- Keep recording persistence, summaries, analytics and external follow-up off the spoken-turn hot path. A short database booking commit is permitted; never hold a transaction during inference/audio/network waits.
- PostgreSQL owns durable business facts; Redis owns active-call state, caches, locks, counters,
  and short-lived delivery state.
- Put business rules in domain/application code, transport mapping at API boundaries, persistence
  in repositories, provider SDK behavior in narrow adapters, and retryable work in workers.
- Add an abstraction only when it improves testability, centralizes real failure policy, or enables
  a credible provider substitution.

## Invariants by area

### Appointments and persistence

- One active appointment per branch/date/start-time slot.
- Enforce uniqueness in PostgreSQL, not only with an application availability check.
- Re-check inside a transaction; define lock strategy, rollback, and HTTP `409` behavior.
- Preserve applied Alembic history and append reviewed forward schema/index changes. Database tools use database-only settings; schema and opt-in dev/test demo data remain separate. Follow `docs/21-database-migrations.md` for rollback data loss and seed guards.
- A 100-request same-slot test must produce one winner before claiming concurrency safety.

### APIs and security

- Public contracts use versioned domain schemas and stable error codes, not provider payloads.
- Human/client authentication, Twilio signature verification, and worker credentials are distinct
  trust mechanisms.
- Authorization is denied by default and proven for Admin, Counsellor, and Viewer roles.
- Logs, metrics, traces, errors, and URLs do not expose secrets, full phone numbers, lead tokens,
  or unnecessary transcript content.

### Jobs and external providers

- At-least-once delivery must produce exactly one business effect through idempotency.
- Define job identity, payload version, timeout, retry/backoff, status, acknowledgement, and
  dead-letter/attention behavior before choosing a queue library.
- Paid provider calls are behind narrow adapters and fakes; automated tests use fakes by default.
- Retry only when the operation is safe and still useful within its latency budget.

### Observability and delivery

- Start from an operational question, then define events, metrics, and trace boundaries.
- Correlate request, call, turn, stage, and job without introducing PII.
- Scalability claims require repeatable P50/P95/P99, error-rate, and resource measurements.
- Containers and CI/CD must make an already-defined environment and quality gate reproducible; do
  not add them as résumé decoration.

## Complexity gate

The selected roadmap adds conditional governed RAG with local embeddings/pgvector at Level 9. Provider contracts precede local models; direct local speech inference precedes speech model serving; full Docker packaging is Level 13. Existing Compose is compatibility tooling. Do not add Kafka, Kubernetes, microservices, a separate vector database or LangGraph without a measured need. Record the evidence and decision in `docs/decisions.md`.

## Completion checklist

- User/product outcome works.
- Invariant is enforced at the correct boundary.
- Failure, duplicate, timeout, cancellation, and privacy cases are covered as relevant.
- Tests run without paid live calls by default.
- Current docs and roadmap status are updated.
- Commit the completed requested level/section changes and push to the configured GitHub branch automatically under the user's standing authorization. Verify checks first, exclude unrelated edits/secrets/runtime data, never force-push, and report the commit link. Do not start the next level implicitly.
- The student can explain context, invariant, design, failure mode, evidence, and trade-off.

Sections 15–17: no automatic ASR weights/downloads. HF tokens do not create inference capacity. The explicit `hf_public_lab` route uses declared public benchmark audio only, selected Indic Gujarati/Hindi and Whisper English Spaces, and keeps community Spaces credential-free; the later explicit official Whisper opt-in is restricted to the reviewed HF edge hosts. `hf_whisper` is a separately selected official authenticated router; 402 credits unavailable means stop without automatic retry/fallback, credit purchase or deployment. Three public-clip inference successes are access evidence, not representative WER/CER/native approval. Retain the local-speech target/current carrier path until native quality/latency gates pass. Endpointing is code-owned; caller-language state is persisted metadata and never permission to change Hindi-base Hinglish production output.


Latest authorized increment: explicit official Whisper HF-edge authentication and section 20 isolated microphone-to-speaker integration. A token unlocks account allowance, not unlimited compute: the authenticated comparison passed seven cases then exhausted quota, and the router reported unavailable credits. Section 20 defaults to mocks; local mode requires cache-only local ASR, local TTS, PostgreSQL/Redis and explicit microphone/paid-API/DB opt-ins. Only the subsequently approved multilingual Whisper base download is allowed; no other ASR weights or hosted/private-microphone workaround. Code/offline evidence is not the unfinished native/all-local gate; production behavior remains unchanged.


Current approved follow-up: provision only `Systran/faster-whisper-base` at the pinned artifact revision, evaluate all 15 public cases as a separate local run, and attempt the real microphone prototype. CPU INT8 is the existing runtime; no Metal claim. Local execution passed 15/15 but Gujarati/Hindi/stitched recognition quality failed. Preserve all hosted reports, production Sarvam/Twilio, OpenAI GPT-4o and Hindi-base Hinglish policy. Default prototype remains mocked; actual microphone/native gate evidence belongs to Level 6. No larger/Indic ASR downloads, HF paid compute or provider switch is authorized.

Owner steering during the local-base follow-up: **do not use OpenAI now; quota is exhausted**. Further microphone checks use explicit `--llm-provider mock` with real local ASR/TTS; no OpenAI settings/client construction or paid opt-in. OpenAI stays the main production LLM for later authorized testing. A mock-LLM hardware result cannot pass the real OpenAI conversation gate.
