# 19 — Dramatiq and Redis Background Tasks

**Status: Implemented.** PostgreSQL remains the durable job ledger; Dramatiq/Redis delivers
work notifications to async consumers. Recording transport is unchanged.

## Why this framework

| Option | Project decision |
|---|---|
| Dramatiq + Redis | Selected: AsyncIO middleware supports our async database code; Redis broker and retry middleware supply explicit queue execution |
| Celery + Redis | Supported alternative when advanced workflow orchestration/monitoring is a requirement; not needed for these three job handlers |
| ARQ + Redis | Async-native and compact, but its repository currently states maintenance-only mode |

These are project-specific choices, not claims that one library is universally best.
Sources: [Dramatiq async actors](https://dramatiq.io/cookbook.html#asyncio-actors),
[Dramatiq Redis/retries](https://dramatiq.io/guide.html),
[ARQ maintenance status](https://github.com/python-arq/arq),
[Celery Redis behavior](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html).

## File flow

```text
call ends
  -> existing recording queue / local spool
  -> recording worker
  -> PostgreSQL transaction: call + followup_jobs intents
  -> job dispatcher scans ready IDs (100 every 5 seconds)
  -> Dramatiq Redis queue: only job UUID
  -> async actor claims specific row under a lock
  -> existing statistics / metadata summary / lead update
  -> effect + job status commit together
```

| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | Optional workers dependency and reproducible package version |
| `roma/services/job_dispatcher.py` | Publish ready IDs; never acknowledge the durable intent |
| `roma/providers/jobs/dramatiq.py` | Redis broker, isolated namespace, ID-only message serialization |
| `roma/workers/dramatiq_worker.py` | Async actor registration and database-pool lifecycle |
| `roma/workers/background.py` | Existing effects, execution timeout, and durable settlement |
| `roma/repositories/postgres/background_jobs.py` | Due IDs, targeted claims, lease recovery, atomic effects, bounded business retries |
| `scripts/dispatch_background_jobs.py` | Dispatcher process; `--once` publishes one batch |
| `scripts/run_background_worker.py` | Dramatiq CLI wrapper; one process and four threads by default |

## Retry ownership and failure behavior

PostgreSQL owns **business retries**: five total attempts, backoff capped at five minutes,
60-second execution timeout, and five-minute lease recovery. A settled business failure sets
`failed` and `available_at`; the dispatcher republishes it when due. `succeeded` and
`dead_letter` rows cannot be claimed again. An unlinked caller remains a visible dead letter.

Dramatiq owns **delivery retries** when the actor cannot safely claim/settle due to a database
outage: three retries with jittered backoff from one to thirty seconds. Exhausting broker
retries does not erase a PostgreSQL intent; the dispatcher can publish it again when eligible.
These broker retries do not independently increment the database business-attempt counter.

Publishing fails: the database row stays pending/failed with its previous attempts.
Publishing succeeds but its acknowledgement is lost: another message may be published;
only one consumer claims the row. A worker crashes mid-execution: the lease expires and
another actor can reclaim it. A commit succeeds but broker acknowledgement is lost:
redelivery finds `succeeded` and does nothing. Redis loses queued messages: PostgreSQL
still contains the intent and the dispatcher republishes it.

No database schema migration is needed for this framework integration. Existing job rows
remain compatible. Delayed follow-up delivery stays inactive: `send_followup` is excluded
from eligible jobs regardless of its due time. This phase sends no SMS or paid AI requests.

The dispatcher can send duplicate notifications while workers are stopped. Keep consumers
running alongside it; larger throughput would require a measured backpressure/publication
strategy. Redis is not the only copy of a business job.

## Run and inspect

```bash
uv sync --extra telephony --extra dev --extra workers

# Separate terminals:
uv run --extra telephony --extra workers python scripts/run_postcall_worker.py
uv run --extra telephony --extra workers python scripts/dispatch_background_jobs.py
uv run --extra telephony --extra workers python scripts/run_background_worker.py
```

Configure `DATABASE_URL` and `REDIS_URL` as before. Existing call handoff creates the jobs;
the dispatcher publishes IDs and the framework worker executes them. `--drain` is still
available on the recording worker. It is no longer an argument of the framework worker;
stop Dramatiq normally to let active tasks finish. Use `--once` on the dispatcher for one pass.

Read the authoritative progress in pgAdmin/psql:

```sql
SELECT id, job_type, status, attempts, available_at, last_error
FROM followup_jobs
ORDER BY created_at DESC
LIMIT 20;

SELECT call_id, event_type, idempotency_key, occurred_at
FROM call_events
WHERE event_type IN ('postcall_statistics', 'postcall_summary', 'lead_status_updated')
ORDER BY occurred_at DESC
LIMIT 20;
```

Focused verification uses disposable PostgreSQL/Redis and fake/no paid providers:

```bash
uv run --extra telephony --extra dev --extra workers pytest -q \
  tests/workers/test_dramatiq_worker.py \
  tests/repositories/postgres/test_job_dispatch.py \
  tests/repositories/postgres/test_background_jobs.py
```

The Redis integration test requires local `redis-server`; PostgreSQL uses the existing
disposable fixture. Tests verify actual Redis broker delivery, parallel duplicate claims,
Redis message loss, retry exhaustion, delayed jobs, and secret-safe framework failures.

## Interview practice

1. **Why introduce a framework when the custom worker worked?**
   To use an explicit broker/actor execution model, consumer concurrency, and standard
   delivery retry behavior while keeping the already-proven business transaction boundary.

2. **Why retain PostgreSQL instead of moving all job state into Redis?**
   A committed job intent must survive broker loss and remain queryable. The database owns
   business status and constraints; Redis delivers notifications.

3. **Why enqueue a UUID instead of the transcript or entire job?**
   The consumer reads authoritative input from PostgreSQL. Smaller messages reduce copied
   PII and prevent the queue payload from becoming a second mutable source of truth.

4. **Why can the dispatcher publish a duplicate?**
   PostgreSQL and Redis cannot commit atomically here. Republish after uncertainty; a
   targeted row lock and unique event key make the repeated business effect safe.

5. **Why not claim before publishing?**
   A Redis outage would then consume a business attempt or strand a leased job before any
   consumer received it. Publication is separate from execution ownership.

6. **Why are there two retry counters?**
   Broker delivery failure and business execution failure are different events. Database
   attempts advance only when an eligible actor acquires a job's execution lease.

7. **What bug did the concurrency tests guard against?**
   Two actors receiving one UUID must not both execute it. One locked claim succeeds; the
   other observes an owned/completed job and acknowledges without a second effect.

8. **What if a worker repeatedly crashes instead of raising a handled exception?**
   Expired leases allow recovery, but the database also caps attempts. After five claimed
   attempts, recovery marks a dead letter without starting a sixth execution.

9. **Why initialize the database inside the worker's event loop?**
   Async connections belong to their event loop and must not be inherited pre-fork.
   One pool per worker process is initialized lazily and closed before AsyncIO stops.

10. **Why does a scheduled follow-up still not send anything?**
    Scheduling an intent does not authorize messaging. Eligible actor types exclude
    `send_followup` until the separate contact/consent phase is implemented.
