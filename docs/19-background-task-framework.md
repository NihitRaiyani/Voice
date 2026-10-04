# Durable jobs with Dramatiq and Redis

**Present implementation; reused at v4 Level 8.** PostgreSQL owns business job intent/status; Dramatiq/Redis delivers UUID notifications. Keep this implemented framework rather than introducing a second one from a roadmap example.

## Flow and ownership

Call end → Redis recording queue/local spool → one recording consumer handles permitted audio → PostgreSQL call + job intents commit → recording-message ack → dispatcher publishes ready IDs → async actor claims a specific row → effect + settlement commit together.

| Source | Responsibility |
|---|---|
| `roma/services/postcall_service.py` | Durable handoff/use-case boundary |
| `roma/services/job_dispatcher.py` | Publish ready IDs without claiming execution |
| `roma/providers/jobs/dramatiq.py` | Broker namespace/ID-only serialization and delivery retries |
| `roma/workers/dramatiq_worker.py` | Async actors and per-process/event-loop database lifecycle |
| `roma/workers/background.py` | Bounded execution/effects/settlement |
| `roma/repositories/postgres/background_jobs.py` | Claims, leases, idempotency, effect/status transactions |
| `scripts/dispatch_background_jobs.py`, `scripts/run_background_worker.py` | Process entry points |

`followup_jobs` is the physical ledger. Active handlers are statistics, deterministic metadata summary and linked-lead update. Unlinked callers remain visible for review. Follow-up intents may be scheduled but `send_followup` is excluded from execution; no SMS or paid AI is sent by these handlers.

## Retry and failure boundaries

Source business policy: five attempts, 60-second execution timeout, five-minute lease, capped five-minute backoff. PostgreSQL records attempts/due time/reason/terminal state. Broker delivery retry policy is three retries with one-to-thirty-second jittered backoff; it does not independently increment business attempts.

Failed publication leaves the database intent eligible. Ambiguous publish can produce duplicates; targeted row locks fence execution. Crash recovery reclaims expired leases within the attempt cap. Lost broker ack after committed success becomes a no-op redelivery. Redis message loss is repaired by republishing eligible IDs.

Dispatcher currently scans up to 100 IDs every five seconds and can accumulate duplicate notifications while consumers are offline. This is a current-volume trade-off, not a bounded high-throughput publication guarantee. L8 must specify measured backpressure, retention and audited safe replay.

One recording consumer is required by its startup recovery. Dramatiq workers have separate row-claim concurrency. Initialize pools inside the worker event loop/process; extra worker processes multiply pool demand.

## Operation and learning

The [runbook](runbook.md) owns startup/shutdown/inspection commands. Use `--once` on the dispatcher; stop Dramatiq normally. Recording `--drain` is a different worker capability.

Tests are `tests/workers/test_dramatiq_worker.py`, `tests/repositories/postgres/test_job_dispatch.py` and `tests/repositories/postgres/test_background_jobs.py`, using disposable PostgreSQL/Redis and fake paid providers. Fresh evidence/limits are in [baseline](roadmaps/level-00-baseline.md).

Explain why intent is not stored only in Redis, why messages contain UUIDs, why publish and claim are separate, how ambiguous acknowledgements are recovered, why broker and business retries differ, and why scheduling does not authorize contact. Compare a more complex outbox/publication strategy only after measurement shows need.

See [concurrency](08-concurrency.md), [recordings](09-recording-storage.md), [API/jobs](15-api-security-and-jobs.md) and [L8](levels/level-08.md).
