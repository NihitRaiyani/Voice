# Data, transactions and concurrency

**Implemented foundation:** async PostgreSQL lifecycle, Alembic schema/repositories, slot locking/active-booking uniqueness, durable webhook/job transactions and Level 2 latest conversation checkpoint save/restore. **Pending:** live booking integration, redial/session recovery, cancellation and complete retention/API workflows.

## Schema ownership

| Records | Present model role / remaining integration |
|---|---|
| Callers, institute/branch/course/counsellor reference data | Durable identity/configuration; approval/seeding is separate from migrations |
| Calls, turns, events | Lifecycle/turn narrative; nullable turn language added; answer/post-call writes exist, not all live milestones |
| Conversation states | Latest per-call checkpoint schema plus Level 2 repository/store adapter with revision increments, schema/policy checks and expiry refusal; redial/L7 recovery pending |
| Model registry/benchmark runs/results | Candidate identity and reproducible measurement schema; runners/inference integration pending L2/L4/L5/L12 |
| Appointment slots/appointments | Offerable slot and active booking constraints; live controller not yet wired |
| Safety/usage/cost/recording models | Durable target entities; model existence does not prove live audit/usage collection |
| `followup_jobs` | Existing physical ledger for background work and inactive follow-up intents |
| Users/roles/audit | Data foundation, not implemented login/RBAC |
| `webhook_receipts` | Independent retained event identity for answer acceptance |

The initial migration is `20260920_0001`; `20260930_0002` adds webhook receipts; `20261004_0003` adds four checkpoint/model/benchmark tables and nullable `call_turns.language`. Existing tables/constraints and the first two migrations are preserved. [The schema catalog](20-relational-schema.md) explains all current keys, foreign keys, checks, indexes and retention choices. Preserve migration history; add reviewed migrations rather than rewriting applied ones. The [migration workflow](21-database-migrations.md) implements database-only Alembic commands and separate opt-in atomic/repeatable demo seeds; inactive synthetic offerings never authorize production advice. Schema upgrades insert no demo content. `roma/core/database.py` and `PostgresUnitOfWork` own session/transaction lifetimes.

## Actual appointment invariant

Slots are unique by branch/date/start time. Appointments have a partial unique index on that tuple for statuses `booked` and `confirmed`, plus a matching-slot foreign key. `book()` locks the existing slot with `FOR UPDATE`, checks availability, inserts the appointment and marks the slot booked inside one unit of work.

The schema has a positive `capacity` field, but the active-slot uniqueness currently enforces **one** active appointment. Do not claim capacity greater than one works. A cancelled appointment alone does not make a `booked` slot available; cancellation must atomically update both sides under policy.

The 100-attempt repository test is `tests/repositories/postgres/test_appointment_concurrency.py`. It expects one winner and 99 domain conflicts. Test presence is not fresh execution evidence; consult [baseline evidence](roadmaps/level-00-baseline.md).

## Level 3 integration

Expose availability/booking through application services and map domain conflicts to 409. Live offers/readback/confirmation must use committed truth. Advisory Redis holds cannot replace a database transaction. Existing-slot locking does not protect absent rows; rely on constraints when introducing slot creation.

Keep transactions short and release connections before inference/audio/Redis/external synchronization. Pool defaults remain 5 + 10 overflow per process with a 5-second wait. Settings now reject unbounded/invalid values and optionally enforce a declared process-wide allocation; the [capacity preflight](runbook.md#database-capacity-and-pool-pressure) checks actual server reservations/headroom. Peak includes overflow and all API/worker/dispatcher engines. `PostgresUnitOfWork` rejects nesting an active instance while supporting sequential use. Real PostgreSQL tests cover pool pressure, release before simulated audio, timeout recovery and cancellation; see [L1 evidence](levels/level-01.md). Request idempotency, reschedule/cancellation and business visiting-hours policy must be explicit.

## Durable conversation and recovery

Redis currently stores one-hour checkpoints. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore for persisted provider call IDs, with schema version and policy identity. L7 still needs observed checkpoint lag, redial/session identity, degradation drills and recovery limits. Never write every audio frame/token to PostgreSQL. Distinguish same-SID reconnect from new-SID redial and observe PostgreSQL appointment truth during restore.

Learning exercises: inspect the ER constraints, run empty-database migration/rollback, explain a query plan, demonstrate a race/rollback, cancel and reuse a slot, and recover a checkpoint without inventing a booking. See [L1](levels/level-01.md), [L3](levels/level-03.md) and [webhooks](18-webhook-idempotency.md).
