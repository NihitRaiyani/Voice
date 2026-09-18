# 17 — Placement-Focused Study Guide

**Status:** Active learning guide.

The repository is the laboratory; interviews are the explanation test. Study by tracing behavior,
breaking assumptions in tests, and defending trade-offs—not by memorizing definitions.

## Study loop for every module

1. **Trace it:** follow one request, call, turn, transition, or job through the code and docs.
2. **Name the invariant:** state what must always be true.
3. **Find the failure:** identify race, timeout, duplicate, crash, invalid input, or privacy leak.
4. **Prove it:** point to a test, log, metric, constraint, or experiment.
5. **Explain the trade-off:** name a reasonable alternative and why it was not chosen.

## Four learning blocks

### Block 1 — Explain the existing realtime system

- Trace Twilio `/answer` to `/ws` and the bidirectional media frames.
- Explain VAD versus endpointing versus barge-in.
- Explain why the conversation controller owns state and the LLM owns wording.
- Demonstrate how the pre-TTS filter fails safe.
- Explain Redis keys, TTLs, caches, queues, and per-call isolation.

**Portfolio proof:** architecture diagram, one turn timeline, one interruption trace, and one safety
test explained in your own words.

### Block 2 — Build database depth

- Design the relational schema and migrations.
- Implement versioned resources through a service/repository boundary.
- Prove appointment locking and uniqueness under contention.
- Explain isolation, rollback, optimistic versus pessimistic control, and index choice.

**Portfolio proof:** ER diagram, migration history, query plan, API contract, and concurrency report.

### Block 3 — Build professional backend controls

- Implement auth/RBAC and webhook security.
- Design idempotent background jobs and audit/cost ledgers.
- Apply PII masking, retention, and deletion rules.
- Test provider failures using fakes instead of paid APIs.

**Portfolio proof:** permission matrix, duplicate-delivery test, retry timeline, audit example, and
privacy data-flow map.

### Block 4 — Prove production readiness

- Add structured logs, metrics, traces, and actionable alerts.
- Run unit, integration, concurrency, evaluation, and load tests.
- Reproduce the stack with containers and enforce checks in CI/CD.
- Measure the bottleneck before proposing scale infrastructure.

**Portfolio proof:** trace screenshot/export, metrics definition, load report, CI run, and an
incident-style explanation of a provider failure.

## Interview questions this project should answer

1. Why use both Redis and PostgreSQL?
2. How do you guarantee two callers cannot book the same slot?
3. Why is async I/O not the same as parallel execution or horizontal scaling?
4. What happens if a webhook or background job is delivered twice?
5. Where do you place transaction boundaries, and why?
6. How does barge-in cancel work already in flight without leaking audio?
7. Why can prompt instructions not replace a deterministic safety filter?
8. How do you test provider integrations without spending money?
9. Which identifiers connect logs, metrics, and traces without exposing PII?
10. When should a provider request be retried, failed fast, or degraded?
11. What evidence supports a scalability claim?
12. Why is a modular monolith the correct default here?
13. What would justify microservices, Kafka, or Kubernetes later?
14. How do schema migrations remain safe during deployment?
15. How would you investigate rising P95 turn latency?

## Answer pattern

Use this structure instead of giving textbook definitions:

```text
Context -> invariant -> design -> failure mode -> evidence -> trade-off
```

Example: "For appointments, the invariant is one active booking per branch/date/time. An
application availability check races, so PostgreSQL enforces uniqueness inside a transaction. A
loser rolls back and receives 409. A 100-request concurrency test proves one winner. Row locking is
an alternative; we choose based on contention and transaction shape."

## Weekly review

At the end of a learning cycle, update:

- the status table in `docs/13-backend-roadmap.md`;
- the relevant current-system chapter;
- tests and verification evidence;
- `docs/decisions.md` for durable trade-offs;
- a concise résumé/interview bullet that names scale or evidence honestly.

Never claim unmeasured traffic, unbuilt features, or production readiness from local happy-path
testing.
