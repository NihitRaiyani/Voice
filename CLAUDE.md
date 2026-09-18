# Roma engineering constitution

Roma is a backend-only Twilio voice agent and a backend-engineering learning project. Changes must
protect the live voice path while making one understandable, testable improvement at a time.

Read `docs/README.md`, `docs/00-project-charter.md`, `docs/10-build-order.md`, and
`docs/decisions.md` before proposing architectural work.

## Current system versus target system

Never describe roadmap work as implemented merely because it appears in documentation.

- **Implemented now:** Twilio bidirectional calling, Pipecat, Silero, Sarvam STT/TTS, OpenAI,
  deterministic safety filtering, a seven-stage controller, Redis live state/caches/status/queue,
  recording workflow, cost controls, and extensive offline tests.
- **Recommended next:** PostgreSQL + SQLAlchemy + Alembic, beginning with durable call/lead records
  and transaction-safe appointment booking.
- **Planned later:** versioned admin APIs, authentication/RBAC, idempotent workers, audit and cost
  ledgers, observability, load tests, Docker, and CI/CD.
- **Optional only with evidence:** WebSocket supervisor UI, provider switching, circuit breakers,
  and distributed infrastructure.

## Locked current stack

| Layer | Current choice |
|---|---|
| Telephony | Twilio Programmable Voice + bidirectional Media Streams |
| Realtime orchestration | Pipecat |
| VAD | Silero |
| STT/TTS | Sarvam Saaras / Bulbul |
| LLM | OpenAI |
| Transient state/cache/queue | Redis |
| API runtime | FastAPI + Pydantic + Uvicorn |

PostgreSQL is the target durable system of record; it does not replace Redis's transient role.

## Non-negotiable runtime boundaries

1. Every outbound call passes the pre-call gates before Twilio is contacted.
2. Every generated spoken line passes the deterministic pre-TTS guard.
3. Twilio HTTP and WebSocket callbacks validate `X-Twilio-Signature`.
4. Lead tokens are never logged and travel as Twilio Stream custom parameters.
5. Credentials, full phone numbers, and test destinations never enter tracked files or logs.
6. Automated verification never places a live call or consumes paid provider APIs by default.
7. Per-call state is isolated; cancellation cannot leak audio or state across calls.
8. Preserve `var/roma` runtime data and its privacy controls.
9. Keep slow persistence, recording, summaries, analytics, and follow-up work off the audio path.
10. The application owns conversation stages and booking invariants; the LLM owns wording only.

## Build discipline

- Start from a user-visible or learning outcome and define evidence that proves it.
- Prefer a modular monolith and the smallest abstraction that solves a demonstrated problem.
- Separate route handling, business rules, persistence, and provider adapters when the separation
  improves testability; do not create empty layers for appearance.
- Use interfaces at paid or failure-prone provider boundaries so tests can use fakes.
- Make retries safe through idempotency. Put database invariants in the database, not only Python.
- For stateful or concurrent work, name the invariant, race, transaction boundary, and rollback.
- Each change updates its tests and documentation. Record meaningful architectural choices in
  `docs/decisions.md`.
- Do not add Kafka, Kubernetes, microservices, a vector database, or orchestration frameworks
  without a measured scaling, ownership, or retrieval problem.

## Documentation rules

- Use the status words **Implemented**, **Next**, **Planned**, **Optional**, and **Historical**.
- Current behavior belongs in `README.md` and `docs/01`–`docs/12`.
- Target architecture and tutorials belong in `docs/13`–`docs/17`.
- `LOG.md` and `docs/superpowers/` are historical evidence, not the current runbook.
- Runtime prompt files under `roma/prompts/` affect product behavior; do not treat them as
  ordinary prose documentation.

## Project-local skills

- Load `.claude/skills/roma-guardrail/SKILL.md` for any spoken-output or LLM-to-TTS change.
- Load `.claude/skills/roma-backend-roadmap/SKILL.md` for roadmap, persistence, API, queue,
  observability, testing, or architecture work.

The former React/Vite frontend is intentionally absent. Backend clients use the bearer-protected
call endpoints directly.
