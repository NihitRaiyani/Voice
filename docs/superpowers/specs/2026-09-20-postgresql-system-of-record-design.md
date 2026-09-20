# PostgreSQL System of Record Design

**Date:** 2026-09-20  
**Status:** Approved for implementation

## Objective

Introduce PostgreSQL as Roma's durable system of record while retaining Redis for transient,
latency-sensitive operational state. The result remains one modular monolith: services depend on
repository contracts, PostgreSQL and Redis are adapters, and domain rules import neither database.

This phase delivers the relational foundation only. It does not add the later REST resources,
authentication flows, analytics API, job retry engine, or observability platform.

## Storage ownership

| PostgreSQL: durable business facts | Redis: transient operational state |
|---|---|
| callers and organization reference data | active-call conversation context |
| call lifecycle, turns, and events | short-lived lead handoff tokens |
| slots and appointments | cached opener and generated audio |
| safety evidence and provider usage | call-status polling views |
| normalized call costs | locks and rate-limit counters |
| recording metadata and follow-up jobs | post-call work transport until durable jobs arrive |
| users, roles, counsellors, and audit history | expiring coordination data |

Redis is never the source of truth for a committed appointment, historical call, cost, or audit
record. PostgreSQL is not placed in the audio-frame path and does not store raw audio frames.

## Technology and dependency direction

- PostgreSQL 16 is the reference database.
- SQLAlchemy 2.x async sessions provide typed mappings and transaction boundaries.
- `asyncpg` is the runtime driver.
- Alembic owns forward and rollback schema changes.
- Application services depend on repository protocols and a unit-of-work protocol.
- `roma/repositories/postgres/` contains SQLAlchemy models and adapters.
- Domain types remain plain Python and do not import SQLAlchemy.
- Integration tests run against PostgreSQL, not SQLite, because partial indexes, row locks,
  timezone behavior, and concurrent uniqueness are part of the contract.

Target additions:

```text
alembic.ini
migrations/
|-- env.py
`-- versions/
roma/
|-- core/database.py
|-- domain/persistence.py
`-- repositories/
    |-- interfaces/durable.py
    `-- postgres/
        |-- models.py
        |-- repositories.py
        `-- unit_of_work.py
tests/repositories/postgres/
```

## Relational conventions

- UUID primary keys use `gen_random_uuid()` so database-created and application-created rows share
  one identifier policy.
- All instants use timezone-aware `TIMESTAMPTZ`; appointment wall-clock components use `DATE` and
  `TIME` together with the branch IANA timezone.
- Money uses `NUMERIC`, never floating point, and stores an ISO-4217 currency.
- Mutable rows have `created_at` and `updated_at`; append-only ledgers only have `created_at` or
  `occurred_at`.
- Status-like values are strings protected by check constraints. PostgreSQL native enums are
  avoided because adding/removing enum values complicates safe migrations.
- Provider deliveries and ledger writes carry unique idempotency keys where duplicate processing
  could create a second business effect.
- Phone numbers are not used as identifiers. `phone_hash` is a deterministic HMAC-SHA256 digest of
  canonical E.164 input using an environment secret; raw phone storage is outside this phase.

## Tables and invariants

### Organization and identity

#### `institutes`

- `id` UUID primary key
- `name` and stable `code`; `code` is unique
- `is_active`, `created_at`, `updated_at`

Deletion is restricted while branches or courses reference the institute.

#### `branches`

- `id` UUID primary key; `institute_id` required foreign key to `institutes`
- stable `code`, `name`, `city`, `timezone`, `is_active`
- unique `(institute_id, code)` and index `(institute_id, is_active)`

Deletion is restricted while slots, appointments, counsellors, or course offerings reference it.

#### `courses`

- `id` UUID primary key; `institute_id` required foreign key
- stable `code`, `name`, optional `description`, `is_active`
- unique `(institute_id, code)` and active-course index

#### `branch_courses`

Many-to-many junction for courses offered at branches. Composite primary key
`(branch_id, course_id)`; both foreign keys restrict deletion. This avoids duplicating a course for
every branch.

#### `roles`, `users`, and `user_roles`

- `roles`: UUID key, unique machine-readable `name`, description, timestamps
- `users`: UUID key, unique case-insensitive email, display name, password hash, active flag,
  timestamps
- `user_roles`: composite primary key `(user_id, role_id)` with cascade only for deleting the
  association owner

The schema supports later RBAC without implementing login or authorization in this phase.

#### `counsellors`

- UUID key; required `branch_id`; optional unique `user_id`
- employee code, display name, active flag, timestamps
- unique `(branch_id, employee_code)` and branch/active index

A counsellor may exist before receiving a user login. Branch and referenced user deletion are
restricted while the counsellor remains.

### Caller and call history

#### `callers`

- UUID key; unique `phone_hash`
- optional `name`, preferred language, city, education, current status
- `created_at`, `updated_at`, optional `anonymized_at`

Callers are anonymized rather than routinely hard-deleted. Existing business history may set its
caller foreign key to null only during an approved purge.

#### `calls`

- UUID key; nullable `caller_id` with `ON DELETE SET NULL`
- unique nullable `provider_call_id`, direction, language, lifecycle status
- `started_at`, `answered_at`, `ended_at`, final stage, booking status
- `total_cost NUMERIC(14,4)`, `currency`, optional recording URL compatibility field
- `created_at`, `updated_at`
- indexes on `(caller_id, created_at)`, `(status, created_at)`, and `started_at`

The provider identifier is not the primary key because provider choice and callback timing are
external concerns. The internal UUID exists before a provider accepts an outbound call.

#### `call_turns`

- UUID key; required `call_id` with `ON DELETE CASCADE`
- positive `turn_number`, constrained speaker, optional transcript, conversation stage, route,
  non-negative latency, created timestamp
- unique `(call_id, turn_number)` plus `(call_id, created_at)` index

Turns contain finalized business-level utterances, never interim STT fragments or audio frames.

#### `call_events`

- UUID key; required `call_id` with `ON DELETE CASCADE`
- event type, optional conversation stage, JSONB payload, event timestamp
- unique nullable `idempotency_key`; index `(call_id, occurred_at)`

This append-only stream records lifecycle checkpoints such as initiated, answered, stage changed,
booking offered, completed, and failed.

### Appointment model

#### `appointment_slots`

- UUID key; required `branch_id`, appointment date, local start/end time, capacity, status
- unique `(branch_id, appointment_date, start_time)`
- check `end_time > start_time` and `capacity > 0`
- composite unique key retained for appointment referential integrity
- availability index `(branch_id, appointment_date, status, start_time)`

#### `appointments`

- UUID key; nullable caller and originating call references use `ON DELETE SET NULL`
- required branch/date/start fields plus optional counsellor and course
- required composite foreign key `(branch_id, appointment_date, start_time)` to the matching slot
- status, created and updated timestamps
- partial unique index on `(branch_id, appointment_date, start_time)` for active statuses
  `booked` and `confirmed`
- caller/date and branch/date/status query indexes

The partial database index is the final concurrency guard: application-level “is free?” checks are
advisory. Booking later runs in one transaction and maps the losing unique violation to a domain
conflict.

### Safety, usage, cost, recording, and work ledgers

#### `safety_events`

- UUID key; nullable call and turn references use `ON DELETE SET NULL`
- rule, original category, replacement type, JSONB metadata, created timestamp
- call/time and rule/time indexes

Safety evidence survives transcript retention deletion and contains categories rather than copied
unsafe text unless policy explicitly requires it.

#### `provider_usage`

- UUID key; nullable call/turn references use `ON DELETE SET NULL`
- provider, service (`telephony`, `stt`, `llm`, `tts`), model, measured units, unit name
- JSONB provider metadata, occurred timestamp, unique idempotency key
- call/service/time and provider/service/time indexes

#### `call_costs`

- UUID key; nullable call and provider-usage references use `ON DELETE SET NULL`
- provider, service, model, quantity, unit, unit price, amount, currency, pricing version
- occurred timestamp and unique idempotency key
- checks prevent negative quantity, unit price, or amount
- call/time and provider/service/time indexes

`provider_usage` preserves raw metering evidence; `call_costs` is the normalized financial ledger.
`calls.total_cost` is a query convenience updated from ledger entries, not the accounting source.

#### `recordings`

- UUID key; required call reference with `ON DELETE CASCADE`
- storage provider, object key, media type, duration, size, checksum
- status, consent timestamp, retention deadline, deletion timestamp, created timestamp
- unique `(storage_provider, object_key)` and retention/status index

URLs are generated at access time; durable public URLs are not stored. The legacy call URL remains
nullable only for compatibility during migration.

#### `followup_jobs`

- UUID key; nullable call/appointment references use `ON DELETE SET NULL`
- job type, JSONB payload, status, attempts, availability time, lock owner/time, last error,
  timestamps, unique idempotency key
- runnable-work index `(status, available_at)`

This table establishes durable job identity without replacing the current Redis post-call queue in
this phase. A later worker milestone will define claiming, retries, and dead-letter behavior.

#### `audit_logs`

- UUID key; nullable actor user foreign key uses `ON DELETE SET NULL`
- action, resource type, resource UUID, request/correlation identifier, JSONB metadata, timestamp
- actor/time and resource/time indexes

Audit rows are append-only. Resource references are intentionally polymorphic and therefore not a
foreign key; the immutable resource UUID remains meaningful after retention or anonymization.

## Transaction and failure boundaries

- A unit of work opens one async session and commits or rolls back the entire use case.
- Repository methods never commit independently.
- Database exceptions are translated at the adapter boundary into stable repository conflicts or
  availability errors; SQLAlchemy/asyncpg exceptions do not escape into routes or domain rules.
- PostgreSQL unavailability fails closed for a new durable business write. A Redis cache/status
  failure follows the existing explicit degraded behavior and does not rewrite PostgreSQL history.
- No database call is made for each audio frame, partial transcript, or streamed model token.

## Retention and privacy defaults

These are application policy inputs, not hard-coded deletion schedulers:

- raw/final transcripts: retain 30 days by default, then redact or delete text while preserving
  non-PII turn metrics;
- recordings: existing 90-day default, then object deletion followed by metadata tombstone;
- caller profile: retain while there is a business relationship, then anonymize identifying fields;
- safety events: retain one year with minimized metadata;
- provider usage and cost ledger: retain seven years for reconciliation;
- audit logs: retain at least one year, append-only;
- appointment history: retain according to institute policy, then detach/anonymize caller identity.

Retention jobs are later work. This phase records `anonymized_at`, `retention_until`, and deletion
metadata needed to implement the policy safely.

## Migration and seed strategy

1. Alembic creates the complete schema from an empty PostgreSQL database.
2. Downgrade removes objects in reverse dependency order; production rollback guidance prefers a
   forward corrective migration after data exists.
3. Reference seed data is explicit and idempotent; migrations do not insert demo callers or calls.
4. Initial role names may be seeded separately for later RBAC.

## Verification

- Schema metadata test asserts every required table, key, foreign key, unique/check constraint, and
  important index.
- Repository unit tests use fakes only for application-service boundaries.
- PostgreSQL integration tests apply Alembic from zero and exercise round trips, rollback, duplicate
  provider IDs, duplicate turn numbers, idempotent ledgers, and slot conflicts.
- A concurrency test demonstrates one active appointment winner for one slot.
- Redis tests continue unchanged, proving the existing transient responsibilities remain.
- Ruff and the complete offline test suite remain green; no test calls a paid provider.

## Completion criteria

- All requested durable tables exist through an Alembic migration.
- The application exposes an async database/session factory and unit-of-work boundary.
- Repository contracts and PostgreSQL adapters cover core creation/read/update operations without
  leaking ORM types into services or domain code.
- PostgreSQL-specific integration evidence exists; SQLite is not presented as equivalent proof.
- Documentation clearly distinguishes durable PostgreSQL facts from expiring Redis state.
- Existing live-call, Redis, and provider behavior remains unchanged.
