# Durable state, Redis and cache

PostgreSQL is already the durable record/job foundation: relationships, constraints and atomic commits protect business facts. Redis serves short-lived operational context whose expiry/reconstruction is expected; even with Redis persistence enabled, this design keeps business authority in PostgreSQL. Do not mark PostgreSQL planned globally or assume every modeled table already receives live events.

| Data | Current / target owner |
|---|---|
| Call answer receipt/call/event, post-call/job intents | PostgreSQL; implemented paths |
| Appointment repository and constraints | PostgreSQL; live controller integration pending |
| Live conversation checkpoint | Redis for hot same-call state; PostgreSQL `conversation_states` now has a Level 2 latest-checkpoint writer/restore adapter keyed through `calls.provider_call_id` |
| Model and benchmark evidence | PostgreSQL registry/runs/results schema present; benchmark runners pending |
| Lead token lookup, call status, rate counters, opener cache | Redis with explicit lifetime |
| Turns, safety events, provider usage/costs, audit | PostgreSQL models/repositories tested; complete live collection pending later levels |
| OpenAI testing spend ceiling | Protected append-only disk ledger today; preserve until durable metering integration |
| Recording capture/finalization | Protected local filesystem; PostgreSQL metadata integration scope reviewed separately |
| Governed knowledge/embeddings | PostgreSQL + pgvector at L9 |

## Lifetimes and recovery

Current conversation checkpoint TTL is one hour; older four-hour notes are obsolete. Lead records default to 30 minutes. Reconnect with the same call SID differs from redial with a new SID; stable session/lead mapping is required before claiming redial restore.

`conversation_states` provides one latest row per call, positive schema/revision, policy identity, a canonical stage, object state and explicit expiry after creation. Level 2 section 10 adds a PostgreSQL adapter that saves/restores compatible latest `CallState` snapshots with schema/policy checks, expiry refusal and revision increments through short unit-of-work sessions. It does not create redial session identity, appointment-reservation restore, cleanup jobs or L7 recovery guarantees. Durable milestones must preserve stage/slots/confirmation and schema/policy identity. Never restore a reservation from Redis when PostgreSQL says absent/cancelled. Measure checkpoint lag, lock ownership/TTL and recovery point limits; advanced event logs/outbox/replay follow core recovery.

## Sessions and capacity

`roma/core/database.py` uses async SQLAlchemy with short sessions, pre-ping and hidden SQL parameters. Source pool defaults are size 5, overflow 10, timeout 5 seconds. Pool size must be positive, overflow nonnegative and checkout timeout positive/finite; zero-size or unlimited-overflow settings are rejected. `DATABASE_POOL_PROCESSES` counts all engine-owning API/recording/dispatcher/Dramatiq processes. `DATABASE_CONNECTION_BUDGET`, when set, rejects `(size + overflow) × processes` above the allocation. Defaults remain 5/10/5 and one declared process; the optional budget does not infer the deployment topology.

Count retained capacity as `size × processes`, worst-case peak as `(size + overflow) × processes`. Sum each engine group for mixed settings. Check actual PostgreSQL `max_connections`, both reserved categories and other-client/operational headroom with `scripts/check_database_pool.py` before scaling. Its opt-in load simulates 100 call tasks with short read-only DB units and audio waits outside sessions; it is not a full voice latency or deployment-capacity claim. PgBouncer is a measured future option when many processes share the database; validate pooling mode/asyncpg compatibility before introducing it. See the [runbook](runbook.md) and [section evidence](levels/level-01.md).

`PostgresUnitOfWork` owns one unit, requires explicit commit for writes and rolls back/closes uncommitted or failed/cancelled contexts. Nested active reuse is rejected; each independent task uses its own unit. Never share an active ORM session across calls. Release sessions before inference, media, Redis waits or external sync. Separate migrations from seed/demo data. Restore/backup must cover actual business invariants, not only tables.

## Cache contract

Current phrase/opener clips use content/manifest identity and optional Redis mirroring. A synthesis cache hit does not skip all upstream work. Dynamic caller readbacks cannot reuse another caller's clip. Changed fixed text invalidates audio; no stale wording may be spoken.

L5 expands keys to text/language/voice/model version. L9 evidence caching also includes scope/content/policy/eligibility versions and expiration. Inactive/stale documents must be excluded even on cache hits. Never store raw audio streams or secrets in Redis.

Delivery queues are not durable business authority. PostgreSQL retains job intent; Redis/Dramatiq sends IDs and the dispatcher may republish. See [jobs](19-background-task-framework.md), [webhooks](18-webhook-idempotency.md) and [data](14-data-and-concurrency.md).

Technical references: [SQLAlchemy pool limits](https://docs.sqlalchemy.org/en/20/core/pooling.html) and [async session concurrency](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks).
