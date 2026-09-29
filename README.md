# Roma Voice Agent

Roma is a backend-only voice-agent system for institute counselling and appointment
booking. The project is built around one practical goal: handle real-time phone calls
while teaching production backend engineering through a real codebase.

The system currently focuses on Twilio voice calls, a software-owned conversation
state machine, PostgreSQL as the durable system of record, Redis for live transient
state, and a concurrency-safe appointment booking module.

There is intentionally no web frontend in this repository.

## What This Project Does

Roma can receive and place Twilio voice calls, stream audio through the real-time
pipeline, run conversation logic through backend-owned stages, persist durable
business data in PostgreSQL, and keep short-lived operational state in Redis.

The backend is designed as a learning-friendly modular monolith. A new developer
should be able to understand which layer owns which responsibility without hunting
through one large file.

```text
Twilio call event
    |
    v
FastAPI routes
    |
    v
Application services
    |
    +--> Domain rules
    |       appointment booking
    |       conversation stage machine
    |       safety and cost rules
    |
    +--> PostgreSQL
    |       durable records:
    |       callers, calls, turns, appointments, costs, audit logs
    |
    +--> Redis
            live state:
            active call context, conversation checkpoint, locks, cached data
```

## Current Capabilities

- Twilio HTTP and WebSocket callback handling.
- Authenticated outbound call API.
- Real-time media pipeline for speech, LLM response generation, and TTS playback.
- Seven-stage conversation state machine:
  `open`, `discover`, `value`, `structure`, `pivot`, `objection`, `close`.
- PostgreSQL schema for durable business records.
- Redis-backed call status and conversation state.
- Transactional appointment booking with slot locking and conflict handling.
- Offline tests for domain rules, repositories, Redis state, media behavior, and
  provider adapters.
- Backend-only structure with no frontend dependency.

## Architecture

Roma follows a layered modular-monolith structure.

```text
roma/
|-- api/             # FastAPI route adapters
|-- core/            # configuration, database setup, logging
|-- domain/          # provider-independent business rules
|-- services/        # application use cases
|-- repositories/    # PostgreSQL and Redis persistence adapters
|-- providers/       # external provider integrations
|-- realtime/        # latency-sensitive audio pipeline
|-- workers/         # background/post-call processing
|-- eval/            # offline evaluation tools
|-- prompts/         # runtime prompt files
`-- main.py          # application composition root
```

Dependency direction:

```text
api -> services -> domain
services -> repositories / providers through clear boundaries
domain -> no FastAPI, no Twilio, no Redis, no PostgreSQL
```

This keeps business logic outside route handlers and makes the system easier to
test without paid provider calls.

## PostgreSQL And Redis

PostgreSQL is the durable system of record. It stores data the business must keep
after a call ends.

Examples:

- callers
- institutes
- branches
- courses
- counsellors
- calls
- call turns
- call events
- appointments
- appointment slots
- safety events
- provider usage
- call costs
- recordings
- follow-up jobs
- users, roles, and audit logs

Redis is used for fast transient state during live operation.

Examples:

- active call context
- current conversation checkpoint
- call status polling
- cached opener audio
- short-lived counters and operational state
- post-call queue state

The project uses both because they solve different problems. PostgreSQL protects
business history and relationships. Redis keeps live-call operations fast.

## Appointment Booking

Appointment booking is treated as the main database-concurrency module.

The booking flow is:

```text
1. Check candidate slot availability.
2. Begin a PostgreSQL transaction.
3. Lock the selected appointment slot.
4. Re-check availability inside the transaction.
5. Create the appointment.
6. Mark the slot as booked.
7. Commit if the invariant still holds.
8. Roll back and return a conflict if another caller already booked it.
```

The important backend invariant is that the same branch cannot confirm two active
appointments for the same date and time.

This is where the project teaches:

- transactions
- row-level locking
- race conditions
- rollback
- atomic operations
- conflict responses
- database constraints as business protection

## Conversation State Machine

The language model does not own the business flow. The backend owns it.

Valid conversation stages:

```text
open -> discover -> value -> structure -> pivot -> objection -> close
```

The state machine decides when the call can move forward, pause, handle an
objection, recover from interruption, or return to missing information. The LLM
generates wording inside these backend-owned guardrails.

## Providers

Roma keeps provider integrations at the edge of the system.

Current provider areas:

- Twilio for telephony.
- Sarvam for STT/TTS configuration.
- OpenAI for LLM responses.
- Google Calendar adapter for calendar reads.

The goal is to keep provider details out of core business logic so fake providers
can be used in tests and real providers can be used in production.

## Local Setup

Install dependencies:

```bash
uv sync --extra telephony --extra dev
```

Copy the environment template:

```bash
cp .env.example .env
```

Important local values:

```dotenv
DATABASE_URL=postgresql+asyncpg://roma_dev:roma_dev_password@127.0.0.1:5432/roma_dev
REDIS_URL=redis://:roma_dev_redis@127.0.0.1:6379/0
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
SARVAM_API_KEY=
OPENAI_API_KEY=
API_TOKEN=
PUBLIC_BASE_URL=https://your-public-host.example
PII_HASH_KEY=
```

Never commit `.env` or real credentials.

Start local infrastructure:

```bash
docker compose up -d postgres redis
```

Apply database migrations:

```bash
uv run --extra dev alembic upgrade head
```

Run the backend:

```bash
./scripts/start_roma.sh
```

## API Surface

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health check |
| `POST /answer` | Twilio voice webhook |
| `WS /ws` | Twilio bidirectional media stream |
| `POST /api/call` | Authenticated outbound-call request |
| `GET /api/call/{request_uuid}` | Authenticated call-status lookup |

Example outbound-call request:

```bash
curl -X POST http://127.0.0.1:8020/api/call \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to_number":"+91XXXXXXXXXX"}'
```

This can place a real paid call. Use only approved test numbers.

## Testing

Run the main offline test suite:

```bash
uv run --extra telephony --extra dev pytest -q
```

Run linting:

```bash
uv run --extra telephony --extra dev ruff check roma tests scripts
```

Run PostgreSQL repository tests:

```bash
uv run --extra dev pytest -q tests/repositories/postgres
```

Run appointment-concurrency tests:

```bash
uv run --extra dev pytest -q tests/repositories/postgres/test_appointment_concurrency.py
```

Run conversation-state tests:

```bash
uv run --extra dev pytest -q tests/domain/conversation tests/repositories/redis/test_conversation_state.py
```

Automated tests should not place live calls or spend paid AI budget.

## Useful Files

| File | Why it matters |
| --- | --- |
| `roma/main.py` | FastAPI application composition |
| `roma/core/config.py` | Environment-driven configuration |
| `roma/core/database.py` | SQLAlchemy engine/session setup |
| `roma/domain/persistence.py` | Durable domain records and repository contracts |
| `roma/domain/conversation/stage.py` | Canonical conversation stage enum |
| `roma/domain/conversation/state_machine.py` | State-machine API |
| `roma/domain/conversation/turn.py` | Turn-level conversation advancement |
| `roma/repositories/postgres/repositories.py` | PostgreSQL repository implementations |
| `roma/repositories/postgres/unit_of_work.py` | Transaction boundary |
| `roma/repositories/redis/conversation_state.py` | Redis-backed call-state checkpointing |
| `roma/realtime/pipeline.py` | Live media pipeline orchestration |
| `migrations/versions/` | Database schema history |
| `compose.yaml` | Local PostgreSQL and Redis services |

## Developer Learning Path

Recommended order for understanding the codebase:

1. Read this README.
2. Inspect `roma/main.py` to see how the app is assembled.
3. Read `roma/domain/conversation/stage.py` and
   `roma/domain/conversation/state_machine.py`.
4. Read `roma/domain/persistence.py`.
5. Read `roma/repositories/postgres/repositories.py`.
6. Read the appointment-concurrency test.
7. Read `roma/realtime/pipeline.py` only after the backend layers make sense.
8. Use `docs/` for deeper design notes and roadmap context.

## Documentation

Start here:

- `docs/README.md`
- `docs/00-project-charter.md`
- `docs/01-architecture.md`
- `docs/03-stage-machine.md`
- `docs/10-build-order.md`
- `docs/13-backend-roadmap.md`
- `docs/17-placement-study-guide.md`

## Project Status

Implemented:

- Backend-only project structure.
- Twilio-based call entry points.
- Redis-backed live call state.
- PostgreSQL schema and repository layer.
- Appointment booking concurrency protection.
- Seven-stage conversation state machine.
- Offline tests for core backend behavior.

Still evolving:

- Full appointment API integration.
- Production deployment hardening.
- Observability dashboards.
- Complete fake-provider test mode for STT, LLM, TTS, and telephony.
- Authentication and authorization expansion.
