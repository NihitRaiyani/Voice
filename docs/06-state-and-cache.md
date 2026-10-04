# Durable state, Redis and cache

PostgreSQL is already the durable record/job foundation. Redis remains transient operational storage. Do not mark PostgreSQL planned globally or assume every modeled table already receives live events.

| Data | Current / target owner |
|---|---|
| Call answer receipt/call/event, post-call/job intents | PostgreSQL; implemented paths |
| Appointment repository and constraints | PostgreSQL; live controller integration pending |
| Live conversation checkpoint | Redis; durable milestone recovery pending L2/L7 |
| Lead token lookup, call status, rate counters, opener cache | Redis with explicit lifetime |
| Recording capture/finalization | Protected local filesystem; PostgreSQL metadata integration scope reviewed separately |
| Governed knowledge/embeddings | PostgreSQL + pgvector at L9 |

## Lifetimes and recovery

Current conversation checkpoint TTL is one hour; older four-hour notes are obsolete. Lead records default to 30 minutes. Reconnect with the same call SID differs from redial with a new SID; stable session/lead mapping is required before claiming redial restore.

Durable milestones must preserve stage/slots/confirmation and schema/policy identity. Never restore a reservation from Redis when PostgreSQL says absent/cancelled. Measure checkpoint lag, lock ownership/TTL and recovery point limits; advanced event logs/outbox/replay follow core recovery.

## Sessions and capacity

`roma/core/database.py` uses async SQLAlchemy with short sessions, pre-ping and hidden SQL parameters. Source pool defaults are size 5, overflow 10, timeout 5 seconds. Budget their totals across API, recording and Dramatiq processes; extra workers multiply connection demand.

Release sessions before inference, media, Redis waits or external sync. Separate migrations from seed/demo data. Restore/backup must cover actual business invariants, not only tables.

## Cache contract

Current phrase/opener clips use content/manifest identity and optional Redis mirroring. A synthesis cache hit does not skip all upstream work. Dynamic caller readbacks cannot reuse another caller's clip. Changed fixed text invalidates audio; no stale wording may be spoken.

L5 expands keys to text/language/voice/model version. L9 evidence caching also includes scope/content/policy/eligibility versions and expiration. Inactive/stale documents must be excluded even on cache hits. Never store raw audio streams or secrets in Redis.

Delivery queues are not durable business authority. PostgreSQL retains job intent; Redis/Dramatiq sends IDs and the dispatcher may republish. See [jobs](19-background-task-framework.md), [webhooks](18-webhook-idempotency.md) and [data](14-data-and-concurrency.md).
