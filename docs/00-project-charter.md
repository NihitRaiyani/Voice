# 00 — Product and Learning Charter

**Status:** Implemented purpose; the backend expansion is planned in stages.

## Product mission

Roma conducts multilingual counselling calls for Weltec Institute and aims to secure a specific
day, time, and branch for an in-person visit. A good call is not merely fluent: it is safe,
interruptible, recoverable, measurable, and ends with an explicit booking readback.

## Learning mission

Use one realistic system to build deep backend judgement for placements. The goal is not to list
technologies on a résumé. The goal is to explain why each component exists, which failure it
prevents, where its state lives, and how evidence proves it works.

By the end, a student should be able to discuss:

- FastAPI and Pydantic API design;
- relational modelling, indexes, migrations, transactions, and locking;
- Redis for transient state, caches, locks, counters, and queues;
- finite-state machines and business invariants;
- async workers, retry safety, and idempotency;
- authentication, RBAC, webhook security, PII protection, and audit trails;
- structured logs, metrics, traces, resilience, and provider boundaries;
- unit, integration, concurrency, and load tests;
- reproducible environments and CI/CD;
- trade-offs between a modular monolith and distributed systems.

## Engineering principle

Add a component only when it solves a concrete project problem and creates a testable learning
outcome. A strong modular monolith is better than decorative microservices.

Examples:

- PostgreSQL earns its place because appointments and audit records require durable constraints.
- Redis keeps its place because live-call state and short-lived counters need low latency.
- A worker earns its place because recording and summaries must not delay the spoken turn.
- Kafka or Kubernetes do not earn a place until measured scale or ownership boundaries require
  them.

## Definition of progress

A roadmap task is complete only when all four forms of evidence exist:

1. **Behavior:** the intended user or operator outcome works.
2. **Invariant:** the rule that must always hold is encoded at the right boundary.
3. **Verification:** automated tests or measurements would fail if the behavior regressed.
4. **Explanation:** the student can defend the design and rejected alternatives without reading a
   script.

## Scope boundaries

- Backend only; no product frontend is planned for the core curriculum.
- The current Twilio/Sarvam/OpenAI voice layer remains intact unless a focused task changes it.
- Paid live calls are manual, approved verification—not part of automated tests.
- Safety, security, latency, privacy, and cost are first-class requirements.
- Target architecture is guidance, not permission to implement the entire roadmap at once.
