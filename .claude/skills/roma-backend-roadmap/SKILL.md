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

Use this skill to turn one mentor-roadmap topic into a focused, testable backend increment. The
authoritative project context is `CLAUDE.md`; the roadmap and tutorials are `docs/10-build-order.md`
and `docs/13-backend-roadmap.md` through `docs/17-placement-study-guide.md`.

## Start with status and scope

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
- Keep PostgreSQL transactions, recording persistence, summaries, analytics, and follow-up work
  out of the spoken-turn hot path.
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
- Migrations are versioned and reproducible; schema and seed/demo data remain separate.
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

Do not add Kafka, Kubernetes, microservices, a vector database, a large RAG pipeline, LangGraph, or
multiple databases unless measured scale, retrieval, deployment, or ownership pressure proves the
modular monolith insufficient. Record the evidence and decision in `docs/decisions.md`.

## Completion checklist

- User/product outcome works.
- Invariant is enforced at the correct boundary.
- Failure, duplicate, timeout, cancellation, and privacy cases are covered as relevant.
- Tests run without paid live calls by default.
- Current docs and roadmap status are updated.
- The student can explain context, invariant, design, failure mode, evidence, and trade-off.
