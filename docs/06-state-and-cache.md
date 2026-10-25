# Durable state, Redis and cache

PostgreSQL is already the durable record/job foundation: relationships, constraints and atomic commits protect business facts. Redis serves short-lived operational context whose expiry/reconstruction is expected; even with Redis persistence enabled, this design keeps business authority in PostgreSQL. Do not mark PostgreSQL planned globally or assume every modeled table already receives live events.

| Data | Current / target owner |
|---|---|
| Call answer receipt/call/event, post-call/job intents | PostgreSQL; implemented paths |
| Appointments and exact slot availability | PostgreSQL; section 13 controller/API commit and atomic cancellation implemented |
| Offered-slot hold | Redis, default 90 seconds, owner-token compare-and-delete; never a reservation |
| Live conversation checkpoint | Redis for hot same-call state; PostgreSQL `conversation_states` now has a Level 2 latest-checkpoint writer/restore adapter keyed through `calls.provider_call_id` |
| Model and benchmark evidence | PostgreSQL registry/runs/results; sections 18–19 completed-report persistence with immutable run keys, model provenance and finite aggregate metrics |
| Lead token lookup, call status, rate counters, opener cache | Redis with explicit lifetime |
| Turns, safety events, provider usage/costs, audit | PostgreSQL models/repositories tested; complete live collection pending later levels |
| Claude testing spend ceiling | Protected append-only disk ledger today; preserve until durable metering integration |
| Recording capture/finalization | Protected local filesystem; PostgreSQL metadata integration scope reviewed separately |
| Governed knowledge/embeddings | PostgreSQL + pgvector at L9 |

## Lifetimes and recovery

Current conversation checkpoint TTL is one hour; older four-hour notes are obsolete. Lead records default to 30 minutes. Reconnect with the same call SID differs from redial with a new SID; stable session/lead mapping is required before claiming redial restore.

`conversation_states` provides one latest row per call, positive schema/revision, policy identity, a canonical stage, object state and explicit expiry after creation. Level 2 section 10 adds a PostgreSQL adapter that saves/restores compatible latest `CallState` snapshots with schema/policy checks, expiry refusal and revision increments through short unit-of-work sessions. It does not create redial session identity, cleanup jobs or L7 recovery guarantees. Section 13 separately reconciles same-SID appointments from SQL before production prompts; checkpoint-only locks cannot authorize a visit. Durable milestones must preserve stage/slots/confirmation and schema/policy identity. Never restore a reservation from Redis when PostgreSQL says absent/cancelled. Measure checkpoint lag, lock ownership/TTL and recovery point limits; advanced event logs/outbox/replay follow core recovery.

## Sessions and capacity

`roma/core/database.py` uses async SQLAlchemy with short sessions, pre-ping and hidden SQL parameters. Source pool defaults are size 5, overflow 10, timeout 5 seconds. Pool size must be positive, overflow nonnegative and checkout timeout positive/finite; zero-size or unlimited-overflow settings are rejected. `DATABASE_POOL_PROCESSES` counts all engine-owning API/recording/dispatcher/Dramatiq processes. `DATABASE_CONNECTION_BUDGET`, when set, rejects `(size + overflow) × processes` above the allocation. Defaults remain 5/10/5 and one declared process; the optional budget does not infer the deployment topology.

Count retained capacity as `size × processes`, worst-case peak as `(size + overflow) × processes`. Sum each engine group for mixed settings. Check actual PostgreSQL `max_connections`, both reserved categories and other-client/operational headroom with `scripts/check_database_pool.py` before scaling. Its opt-in load simulates 100 call tasks with short read-only DB units and audio waits outside sessions; it is not a full voice latency or deployment-capacity claim. PgBouncer is a measured future option when many processes share the database; validate pooling mode/asyncpg compatibility before introducing it. See the [runbook](runbook.md) and [section evidence](levels/level-01.md).

`PostgresUnitOfWork` owns one unit, requires explicit commit for writes and rolls back/closes uncommitted or failed/cancelled contexts. Nested active reuse is rejected; each independent task uses its own unit. Never share an active ORM session across calls. Release sessions before inference, media, Redis waits or external sync. Separate migrations from seed/demo data. Restore/backup must cover actual business invariants, not only tables.

## Cache contract

Current phrase/opener clips use content/manifest identity and optional Redis mirroring. A synthesis cache hit does not skip all upstream work. Dynamic caller readbacks cannot reuse another caller's clip. Changed fixed text invalidates audio; no stale wording may be spoken.

Section 19 adds a separate bounded process-local LRU (64 entries / 8 MiB by default), keyed by actual safe normalized text, language, voice, model/artifact version, normalization version and PCM format. Concurrent identical requests share synthesis; failed or cancelled synthesis never populates the cache. Every hit still crosses SafetyService, and artifact changes invalidate the version. Existing live Sarvam assets/cache remain intact. A process restart empties the new cache; it does not persist caller text in Redis. The [Level 5 brief](levels/level-05.md) owns inference and gate evidence. L9 evidence caching also includes scope/content/policy/eligibility versions and expiration. Inactive/stale documents must be excluded even on cache hits. Never store raw audio streams or secrets in Redis.

Delivery queues are not durable business authority. PostgreSQL retains job intent; Redis/Dramatiq sends IDs and the dispatcher may republish. See [jobs](19-background-task-framework.md), [webhooks](18-webhook-idempotency.md) and [data](14-data-and-concurrency.md).

Technical references: [SQLAlchemy pool limits](https://docs.sqlalchemy.org/en/20/core/pooling.html) and [async session concurrency](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks).

Section 17 adds `language_router` to the existing `CallState` JSON payload: an accepted enum plus at most five recent enum labels. It retains no transcript, initializes safely for older/malformed router fields and persists through the existing Redis and PostgreSQL checkpoint adapters without a migration. `advance_turn` is the one observation hook shared by live/text controllers; provisional ASR fragments and rejected input are not observations. Caller-language hints do not change the production reply policy. See [Level 4](levels/level-04.md#persisted-language-contract).

The 2026-10-20 real microphone readback exposed a nested-immutability defect in the PostgreSQL adapter: shallow copying a durable record lost the language route and left collections frozen. Restore now converts nested metadata to detached plain dict/list values before constructing CallState. Native PostgreSQL tests cover English/history plus mutation and re-save of attempts, objections, offers and facts. Reload of the actual English microphone call confirms the same language/stage/slots without a data migration. See [the physical follow-up](levels/level-06.md#real-claude-english-microphone-follow-up--2026-10-20).

## Section 24 supervised live state

The [Level 7 contract](levels/level-07.md#section-24-live-state-and-supervised-checkpoints) now connects Redis hot snapshots and the existing SQL latest-checkpoint adapter in production/local composition. Durable milestones commit before cache publication; the awaited writer serializes one frozen pending snapshot, bounded transient retries and observable lag/failure counts. Restore prefers compatible SQL over missing, expired, corrupt or stale Redis and repopulates the cache. Redis/SQL failures cannot silently create a fresh resumed call. The 360-second token lease covers the five-minute session with cleanup headroom; crashed owners expire and cannot delete successors.

Booking commits remain independent of Redis; advisory hold outages use fresh SQL availability without inventing a reservation. Phrase mirrors expire in one hour; state/lead/status/opener TTL inputs are bounded; the hourly counter atomically sets initial expiry. Core recovery permits loss of in-progress work after the last durable milestone, while acknowledged SQL milestones survive Redis loss. Advanced event replay is still gated. No running Redis persistence settings or working database were changed.

## Section 27 webhook key lifetimes

Temporary answer deduplication uses hashed event keys with explicit processing owner TTL60 s and completed TTL86,400 s. Completed coverage outlives the live-call lease; durable SQL uniqueness persists independently. Redis done/mismatch never bypasses SQL verification, and stale owners cannot delete successors. [Webhook idempotency](18-webhook-idempotency.md#section-27-advisory-processing-window) owns redelivery/retention semantics.


Phase 5A adds an optional bounded, expiring metadata-only Redis Stream for isolated audio labs. See the [replay contract](05-endpointing-vad.md#persistent-capture-and-advanced-audio-lab). It stores frame identities and controls, not PCM, appointment effects or durable conversation events. It does not change SQL milestones, production leases, Redis persistence configuration or recovery-point guarantees. Caller-audio replay needs separately authorized payloads.
