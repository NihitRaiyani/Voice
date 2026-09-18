# 13 — Backend Platform Roadmap

**Status:** Target architecture and progress map. Only rows marked **Implemented** exist today.

## From voice application to backend platform

```text
                         Twilio Voice
                    HTTP webhook + Media Stream
                                |
                         FastAPI gateway
                                |
          +---------------------+---------------------+
          |                     |                     |
     Call service       Conversation engine     Booking service
          |                     |                     |
          +---------------------+---------------------+
                                |
                 +--------------+--------------+
                 |                             |
             PostgreSQL                     Redis
           durable records          live state/cache/locks
                 |                             |
                 +-------- Pipecat voice ------+
                           STT -> LLM -> TTS
                                |
                         Background jobs
               recording / summary / analytics / follow-up
                                |
                 logs / metrics / traces / alerts
```

This remains a modular monolith: logical boundaries improve reasoning and testing without forcing
network boundaries between modules.

## Module status map

| Module | Status | Current evidence or target outcome |
|---|---|---|
| Twilio call transport | **Implemented** | Signed `/answer`, `/ws`, outbound Calls API |
| Realtime voice pipeline | **Implemented** | VAD, STT, controller, LLM, safety, TTS, barge-in |
| Conversation state machine | **Implemented** | Seven stages and deterministic transitions |
| Redis operational layer | **Implemented** | Live state, caches, status, counters, queue |
| Recording workflow | **Implemented** | Capture, spool, worker, storage/retention controls |
| PostgreSQL system of record | **Next** | Durable callers, calls, turns, slots, appointments |
| Versioned CRUD/API resources | **Next** | `/api/v1` schemas, pagination, filtering, errors |
| Appointment transaction engine | **Next** | One winner under concurrent booking attempts |
| Authentication and RBAC | **Planned** | Admin/Counsellor/Viewer permissions |
| Safety and audit event ledger | **Planned** | Queryable policy and administrative evidence |
| Provider usage and cost ledger | **Planned** | Per-turn/provider cost plus aggregates |
| Durable background jobs | **Planned** | Retry, idempotency, status, dead-letter handling |
| Analytics API | **Planned** | Conversion, latency, cost, language, safety metrics |
| Structured observability | **Planned** | Correlated logs, metrics, traces, alerts |
| Containerized environment and CI/CD | **Planned** | Reproducible local stack and quality gates |
| Supervisor event channel | **Optional** | WebSocket/PubSub only after event contract is stable |
| Microservices/Kafka/Kubernetes | **Optional** | Require measured scale or ownership pressure |

## Recommended module boundaries

These are responsibilities, not a command to reorganize everything before delivering value.

- **API:** HTTP/WebSocket parsing, authentication context, validation, status mapping.
- **Application services:** use-case orchestration such as placing a call or booking a visit.
- **Domain:** conversation transitions, booking invariants, safety decisions, cost rules.
- **Repositories:** durable queries and transaction boundaries.
- **Providers:** Twilio, Sarvam, OpenAI, calendar, and future storage adapters.
- **Workers:** retryable post-call jobs outside realtime latency budgets.
- **Core:** configuration, security helpers, logging, metrics, and shared error vocabulary.

Dependencies should point toward domain rules. A route should not contain SQL transaction logic;
an appointment rule should not import FastAPI; provider SDK exceptions should not leak through the
public API.

## Provider abstraction: when it earns its place

Create a narrow interface when at least one is true:

- tests otherwise require paid/external calls;
- provider errors need domain-level mapping;
- timeout/retry behavior belongs in one boundary;
- a real provider switch is plausible.

Do not build a universal provider framework. Start with the operation the product uses, such as
`generate(messages)`, `transcribe(stream)`, `speak(text)`, or `place_call(request)`.

## Roadmap completion rule

A module moves to **Implemented** only when its behavior, failure cases, tests, operator guidance,
and interview explanation all exist. Updating this table is the final step of that module—not the
first.
