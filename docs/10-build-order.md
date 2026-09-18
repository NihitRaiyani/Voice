# 10 — Backend Engineering Build Order

**Status:** Curriculum authority. The realtime voice baseline is implemented; the backend platform
levels below are future increments unless marked otherwise.

Build one vertical slice at a time. Each slice must include behavior, persistence or state rules,
failure handling, tests, documentation, and an explanation a student can give in an interview.

## Level 0 — Realtime voice baseline (implemented)

- Twilio outbound calls, signed answer webhook, and bidirectional media stream.
- Pipecat pipeline with Silero VAD, Sarvam STT/TTS, and OpenAI.
- Seven-stage software-owned conversation machine.
- Deterministic pre-call and pre-TTS safety gates.
- Redis live state, caches, status, spend controls, and post-call queue.
- Barge-in, per-call isolation, recording workflow, and offline regression coverage.

Before extending the platform, a student should be able to trace one turn, name every trust
boundary, and explain why slow work stays outside the audio path.

## Level 1 — Strong backend foundation (next)

### Milestone 1A: PostgreSQL foundation

- Introduce PostgreSQL, SQLAlchemy 2.x, and Alembic.
- Model callers, calls, call turns/events, branches, and appointment slots.
- Keep Redis for active-call state and caches.
- Add migrations, constraints, indexes, seed data, repository boundaries, and integration tests.

### Milestone 1B: Versioned REST resources

- Add `/api/v1` call, lead, and appointment resources with Pydantic schemas.
- Standardize pagination, filtering, sorting, success envelopes, and domain error codes.
- Keep route handlers thin; business invariants belong in services/domain code.

### Milestone 1C: Appointment engine

- Book, reschedule, and cancel visits through database transactions.
- Enforce `UNIQUE(branch_id, appointment_date, start_time)` or an equivalent slot invariant.
- Re-check availability while holding the chosen locking strategy.
- Return HTTP `409` for a valid request that loses a booking race.
- Prove that 100 simultaneous attempts yield one appointment.

**Exit evidence:** migrations run from zero, API integration tests use PostgreSQL, and the
appointment concurrency test has exactly one winner.

## Level 2 — Professional backend

### Milestone 2A: Authentication and authorization

- Add password hashing, access/refresh token policy, and logout/revocation design.
- Model users, roles, and permissions for Admin, Counsellor, and Viewer.
- Protect recordings, appointments, analytics, safety controls, and user management by role.

### Milestone 2B: Durable work and auditability

- Move recording, summary, analytics, and follow-up work to explicit background jobs.
- Define retry, dead-letter, job-status, and idempotency behavior.
- Store safety events, audit logs, provider usage, and normalized cost entries.
- Add PII masking, retention, deletion/anonymization, and signed recording access.

### Milestone 2C: Analytics API

- Compute call outcomes, conversion funnel, duration, turns, languages, safety hits, latency, and
  cost through SQL aggregation.
- Expose backend metrics through versioned endpoints; a frontend is not required.

**Exit evidence:** role tests prove forbidden operations fail, duplicate jobs/webhooks create one
business effect, and analytics totals reconcile with source rows.

## Level 3 — Advanced backend

- Validate every provider callback and reject invalid/stale/replayed requests.
- Add Redis-backed rate limits for login, admin, analytics, callbacks, and expensive test routes.
- Introduce narrow provider interfaces where fake implementations remove paid calls from tests.
- Define timeout budgets, safe retries with backoff, domain error mapping, fallbacks, and graceful
  degradation for STT, LLM, TTS, Twilio, Redis, and PostgreSQL.
- Add structured JSON logs with request, call, turn, stage, and job correlation identifiers.
- Optionally add a supervisor event channel using WebSockets and Redis Pub/Sub after the event
  contract is stable; a visual dashboard is not required.

**Exit evidence:** replay and duplicate-delivery tests pass, failure injection produces expected
domain behavior, and logs correlate one call without exposing PII.

## Level 4 — Production engineering

- Unit, API, PostgreSQL/Redis integration, worker, concurrency, and load tests.
- P50/P95/P99 metrics for endpointing, STT, LLM first token, TTS first audio, total turn, Redis,
  database, and HTTP operations.
- OpenTelemetry traces, Prometheus metrics, and Grafana dashboards when the measurement contract is
  ready.
- Docker and Docker Compose for FastAPI, PostgreSQL, Redis, worker, and observability services.
- CI/CD gates for Ruff, type checking, tests, security checks, migrations, and image build.
- Measured load stages at 1, 10, 25, 50, and 100 sessions with error rate and resource usage.

**Exit evidence:** a clean machine can start the environment reproducibly, CI enforces the quality
gate, and the load report names the first measured bottleneck.

## Five mandatory additions

If time is limited, prioritize these in order because they create the strongest backend depth
without weakening the realtime path:

1. **Appointment concurrency:** transactions, uniqueness, locking, rollback, and conflict handling.
2. **Persistent conversation state:** durable call, turn, stage, and booking checkpoints.
3. **Asynchronous jobs:** recording, summaries, analytics, and follow-up outside the live path.
4. **Security and auditability:** auth/RBAC, webhook validation, PII-safe logs, safety/audit events.
5. **Observability and testing:** metrics, traces, structured logs, integration/concurrency/load tests.

## Deliberately not on the default roadmap

Do not add Kafka, Kubernetes, microservices, a vector database, a large RAG pipeline, LangGraph, or
multiple databases for appearance. Reconsider only when a measured scaling, retrieval, deployment,
or team-ownership problem makes the current modular monolith insufficient.

## How to choose the next task

1. Select the earliest incomplete milestone that unlocks a concrete behavior.
2. State its business invariant and failure cases before choosing tools.
3. Write a small design and test plan.
4. Implement one vertical slice, not an entire technical layer.
5. Verify offline and update the status map in `docs/13-backend-roadmap.md`.
6. Record a reusable decision in `docs/decisions.md`.
