# Data, transactions and concurrency

**Implemented foundation:** async PostgreSQL lifecycle, Alembic schema/repositories, slot locking/active-booking uniqueness and durable webhook/job transactions. **Pending:** live booking integration, durable conversation restore, cancellation and complete retention/API workflows.

## Schema ownership

| Records | Present model role / remaining integration |
|---|---|
| Callers, institute/branch/course/counsellor reference data | Durable identity/configuration; approval/seeding is separate from migrations |
| Calls, turns, events | Lifecycle/turn narrative; answer and post-call paths write records, not all live milestones |
| Appointment slots/appointments | Offerable slot and active booking constraints; live controller not yet wired |
| Safety/usage/cost/recording models | Durable target entities; model existence does not prove live audit/usage collection |
| `followup_jobs` | Existing physical ledger for background work and inactive follow-up intents |
| Users/roles/audit | Data foundation, not implemented login/RBAC |
| `webhook_receipts` | Independent retained event identity for answer acceptance |

The initial migration is `20260920_0001`; `20260930_0002` adds webhook receipts. Preserve migration history; add reviewed migrations rather than rewriting applied ones. Seeds/demo data stay separate. `roma/core/database.py` and `PostgresUnitOfWork` own session/transaction lifetimes.

## Actual appointment invariant

Slots are unique by branch/date/start time. Appointments have a partial unique index on that tuple for statuses `booked` and `confirmed`, plus a matching-slot foreign key. `book()` locks the existing slot with `FOR UPDATE`, checks availability, inserts the appointment and marks the slot booked inside one unit of work.

The schema has a positive `capacity` field, but the active-slot uniqueness currently enforces **one** active appointment. Do not claim capacity greater than one works. A cancelled appointment alone does not make a `booked` slot available; cancellation must atomically update both sides under policy.

The 100-attempt repository test is `tests/repositories/postgres/test_appointment_concurrency.py`. It expects one winner and 99 domain conflicts. Test presence is not fresh execution evidence; consult [baseline evidence](roadmaps/level-00-baseline.md).

## Level 3 integration

Expose availability/booking through application services and map domain conflicts to 409. Live offers/readback/confirmation must use committed truth. Advisory Redis holds cannot replace a database transaction. Existing-slot locking does not protect absent rows; rely on constraints when introducing slot creation.

Keep transactions short and release connections before inference/audio/Redis/external synchronization. Pool defaults are 5 + 10 overflow per process with 5-second wait; budget API and worker totals. Request idempotency, reschedule/cancellation and business visiting-hours policy must be explicit.

## Durable conversation and recovery

Redis currently stores one-hour checkpoints. L2/L7 add durable stage/slot/confirmation milestones, session identity, schema version and tested restore/checkpoint lag. Never write every audio frame/token to PostgreSQL. Distinguish same-SID reconnect from new-SID redial and observe PostgreSQL appointment truth during restore.

Learning exercises: inspect the ER constraints, run empty-database migration/rollback, explain a query plan, demonstrate a race/rollback, cancel and reuse a slot, and recover a checkpoint without inventing a booking. See [L1](levels/level-01.md), [L3](levels/level-03.md) and [webhooks](18-webhook-idempotency.md).
