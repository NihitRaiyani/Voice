# Backend architecture contract

**Level 1 section 6.** Roma remains a layered Python modular monolith. The
roadmap's `backend/app` tree maps to the existing `roma/` package; a package
rename would add churn without improving boundaries. The contract below is the
shape future level work must preserve.

## Package map

| v4 area | Current package | Owns |
|---|---|---|
| `app/main.py` | `roma/main.py` | Composition root for settings, database, routes, services and realtime media |
| `api/v1` | `roma/api/v1/` | FastAPI request parsing, authentication and response/error translation |
| `core` | `roma/core/` | Config, logging, database engine setup, migration settings and shared privacy helpers |
| `models` | `roma/repositories/postgres/models/` | SQLAlchemy table mappings owned by migrations |
| `schemas` | `roma/domain/persistence.py`, `roma/schemas/rest_api.py` | Transport-neutral records and versioned API contracts |
| `repositories` | `roma/repositories/` | PostgreSQL durable adapters and Redis transient adapters |
| `services` | `roma/services/` | Application use cases and orchestration across domain rules, providers and repositories |
| `providers` | `roma/providers/` | Twilio, Google Calendar, Dramatiq and future STT/LLM/TTS/local-model adapters |
| `voice` | `roma/realtime/` plus `roma/domain/conversation` and `roma/domain/safety` | Pipecat audio processors, VAD, turn-taking, conversation state, safety and recording handoff |
| `rag` | future `roma/rag/` at Level 9 | Governed ingestion, embeddings, retrieval and grounding |
| `workers` | `roma/workers/` | Retryable background effects and post-call processing |
| `db` | `migrations/`, `roma/core/database.py`, `roma/core/migration_settings.py` | Alembic history, engines, sessions and database-only operator tools |
| `tests` | `tests/` | Offline unit/integration/architecture contracts |
| `knowledge` | future `knowledge/` at Level 9 | Approved JSON/YAML facts before governed retrieval ingestion |
| `benchmarks` | `roma/eval/` and future corpus folders | Offline ASR/TTS/LLM/model evaluation |
| `configs` | `roma/core/config.py`, `.env.example` and future static config files | Runtime knobs and model/threshold paths |

## Dependency direction

`roma/main.py` is the only production composition root. It can assemble concrete
providers, repositories, services, API routes and realtime media. Other modules
must depend toward business rules, not outward toward transports.

The domain layer owns stage transitions, date/time checks, safety normalization,
pre-call policy, cost arithmetic and persistence protocols. Domain modules must
not import FastAPI, provider SDKs, SQLAlchemy, Redis clients, realtime processors,
workers or concrete repository packages. A focused architecture test enforces
that rule for `roma/domain/**`.

API routes translate HTTP only: parse input, authenticate, call a service and map
domain/application failures to HTTP. Versioned REST resources use the shared v1 envelope
and keep database work in `RestApiService`. Business decisions such as DND, call window,
budget, booking truth, safety and idempotency live outside route handlers.

Services coordinate one use case. They may use repositories and providers, but
they should expose transport-neutral results and errors. Repositories own storage
details and short database units of work. Providers own SDK payloads, credentials
and retry behavior. Workers own retryable asynchronous execution and use services
or repositories to produce one durable business effect.

## Section 6 code adjustment

Two existing outward imports were removed:

- `roma.domain.conversation` no longer re-exports the Redis checkpoint adapter.
  It exposes a domain protocol plus an in-memory adapter for tests/offline use.
  The realtime composition imports `RedisCallStateStore` from
  `roma.repositories.redis.conversation_state`.
- `roma.domain.costs.spend` no longer imports worker path helpers. Owner-only
  filesystem helpers now live in `roma.core.private_files`; post-call path code
  re-exports them for compatibility.

This keeps live behavior unchanged while making the boundary explicit and
testable. It does not claim the whole monolith is fully cleaned up: CI/pre-commit,
native one-command startup and remaining Level 1 section 9/full-gate work are still pending.

Return to the [documentation index](README.md).
