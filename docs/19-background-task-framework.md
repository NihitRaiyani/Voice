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

`followup_jobs` is the physical ledger. Statistics, deterministic metadata summary and linked-lead update remain active. Section 26 adds explicit consent/sender-gated followup execution, lower-priority summary routing and terminal recording reason/attempt evidence. Unlinked callers and unconfigured/unconsented followups stay visible as dead letters. No SMS/email/CRM sender is configured; default summary makes no paid call.

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

## Section 26 extension

Reuse the existing claims/lease/settlement boundary through `BackgroundHandlers`. Future schedules remain SQL rows; dispatcher routing carries ID/type only. Summary actor priority100 uses `roma_background_summary`; ordinary work priority0 stays preferred. Normal startup remains deterministic; `scripts/run_background_worker.py --allow-paid-summary` is explicit paid opt-in for a separate metadata-only capped Claude instance. External followups require persisted consent and an injected sender honoring stable provider idempotency; no automatic messages are sent. Recording terminal intents retain reason/attempts, and completed conversion is never duplicated. [Level 8](levels/level-08.md#section-26-background-processing) owns bounds and limits. Optional Message Batches is documented, not implemented or billed.

## Document-owned knowledge jobs

Sections 34–40 add `knowledge_ingestion_jobs`, separate from call-owned post-call intent. Unique document/hash/pipeline identity, a 300-second SQL lease, attempt/owner fencing, at most five attempts, safe delayed retries and explicit dead letter/quarantine protect canonical publication. Inference runs outside transactions; final publication reloads source identity and verifies canonical chunks/vectors. The supported operator composition is an explicit bounded SQL drain; the injectable Dramatiq actor carries IDs but is not an automatically installed consumer. Repeated drain calls recover available/expired work; neither a job nor successful embedding confers human approval. See [Level 9](levels/level-09.md) and [runbook](runbook.md#governed-ingestion-retrieval-and-golden-evaluation-sections-3440).
