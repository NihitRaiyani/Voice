# Observability, testing and delivery

**Present:** [Section 21 JSON logging](levels/level-06.md#section-21--structured-logging), sections 45–46 bounded telemetry/optional spans and normalized SQL usage/cost projections, offline evaluations and database/worker integration tests. **Pending:** final combined-source verification, actual production dashboards/reconciliation, sections 47–48, real local-model capacity/chaos reports and full deployment packaging.

## Measurement design

Start with a question: which call/turn/stage failed, where time was spent, what retried, what effect committed and what resource saturated? Use PII-safe correlation and stable event names. Do not use phone numbers/transcripts/tokens as trace or metric labels.

The shared logger serializes approved metadata only; arbitrary messages and tracebacks become `legacy_log` without free-form content. Prototype stdout reports remain CLI output, while operational events use JSON on stderr. The [runbook](runbook.md#structured-logging-section-21) explains how to follow one call.

Measure endpoint/ASR/extraction/LLM first token/TTS first audio, conditional retrieval/fallback, end-to-end response, queues, event-loop lag, database pools, Redis, GPU, safety, job attempts and normalized carrier/hardware costs. SQL outcomes must reconcile with durable records.

L11 adds OpenTelemetry/Prometheus/Grafana; tools consume defined signals rather than inventing them. Optional supervisor PubSub/WS is best effort and requires role/security controls. A dashboard with sample data is not production evidence.

`model_registry`, `benchmark_runs` and `benchmark_results` now provide the relational evidence foundation. Future runners must pin model/version and dataset identity, record hardware/configuration and meaningful units/sample counts, and keep PII/sample media out of these metadata rows. A schema row or `is_active` flag is not a license approval, completed benchmark or production selection. See the [schema catalog](20-relational-schema.md).

Sections 22–23 add PII-safe queue snapshots, producer/consumer wait, drop/discard counters and synthetic concurrent first-audio/heartbeat evidence. Streaming stage timings overlap; a fake software write is not acoustic playback, and the four-call report cannot establish native capacity or an SLO. [Level 7](levels/level-07.md#sections-2223-implementation) owns these limits and cancellation/pressure policy.

## Tests and capacity

Unit/provider-fake/API/repository/worker/concurrency/evaluation tests make no paid calls. PostgreSQL tests need a disposable database; report skips and environment limits. Manual local-model/GPU and authorized carrier checks are separate evidence.

L12 exercises slow providers, Redis loss, queue pressure, abrupt disconnect and RAG fallback. Separate mocked backend load from real GPU capacity. Grow 1/10/25/50/100 sessions only while agreed latency/error/resource gates hold; stop at measured saturation. Record P50/P95/P99, sample/configuration/hardware identity, queue wait, cold/warm state and failures.

## Delivery and SLOs

L1 introduces the expanded quality/CI skeleton; L12 adds security scans, migration/regression checks and zero-external-GenAI enforcement for the final local profile. L13 introduces full Docker/GPU Compose packaging, mounted weights/data, runtime secrets, readiness, clean-host startup, backup/restore and rollback.

Existing database/Redis Compose predates v4 and does not complete L13. Native-service startup remains the foundation-learning path. Roadmap latency/event-loop/packet-loss numbers are provisional and transport-specific. Agree SLO/error-budget policy after measurements; recurring misses prioritize reliability.

See [verification](12-verification.md), [L11](levels/level-11.md), [L12](levels/level-12.md), [L13](levels/level-13.md) and [runbook](runbook.md).

## Section 24 checkpoint signals

Reviewed JSON events add `checkpoint_completed`, `checkpoint_failed` and `state_cache_failed`. Failure events use ERROR. The finite nonnegative metric allowlist is checkpoints, failures, retries, redis_failures, recoveries, pending and lag_ms; transcripts, slot values, Redis keys/tokens, database URLs and arbitrary extras are omitted. Completed checkpoint latency includes its awaited milestone work; no numeric SLO is asserted. Existing queue and turn metrics remain separate. [Level 7](levels/level-07.md#section-24-live-state-and-supervised-checkpoints) defines the supervised retry and recovery contract.

## Section 45 bounded telemetry

`roma/core/telemetry.py` exposes cumulative Prometheus histogram/counter/gauge text with reviewed stage/outcome/route/queue labels and no retained samples or caller payload. Optional OpenTelemetry uses a private provider with fixed attributes and no automatic exception recording/exporter. Live Pipecat probes measure VAD-stop confirmation through software playback completion; endpointing uses the reported silence window. These are software boundaries, not first-audible/physical stop latency. Process CPU and peak RSS are available; unknown GPU/current RSS observations stay absent rather than zero. `/metrics` access and proxy caveats are in [security](07-security.md#sections-4346-service-and-observation-privacy).

## Section 46 normalized usage and costs

`UsageEvent`/`CostService`/`CostPostgresRepository` separate ordinary input, cache reads/writes, output tokens, carrier/audio/GPU seconds, characters and cache hits. One transaction writes an immutable idempotency receipt and exact Decimal cost lines. Changed same-key content conflicts; missing usage/rates/billing basis/cache-write TTL remains unpriced. Metadata retains raw units, exact amounts and price provenance alongside compatibility numeric columns. UTC day/month/provider/call/turn projections retain separate currencies, unknown-unit counts and completeness flags; no missing GPU time or local costs are invented. Historical rows are not repriced. The INR safety reservation ledger remains independent.

The [official Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview), verified 2026-10-26, supplies the versioned direct-standard USD rates per million tokens:

| Prompt total | Input | Output | 5-minute write | 1-hour write | Cache read |
|---|---:|---:|---:|---:|---:|
| Up to 100,000 | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |
| Above 100,000 | 0.50 | 2.50 | 0.625 | 1.00 | 0.05 |

The threshold includes ordinary input plus cache reads/writes. Only the exact reviewed model/direct billing basis is priced; discounts, other regions/providers and exchange rates are not assumed. A matching provider-observed amount/currency can be retained separately. These are estimates with provenance, not invoices or historical repricing. [Level 11](levels/level-11.md) retains the native dashboard/reconciliation gate and unstarted sections 47–48.

The [native observability lab](../configs/observability/README.md) configures a loopback Prometheus scrape and provisioned Grafana **Roma measured operations** dashboard. One API worker matches one process-local registry; multi-worker aggregation and separate worker-process scraping require explicit targets. Only observed samples appear. Grafana disables anonymous access and usage/update reporting, and its first Admin password must be provided privately. Existing native binaries and an authorized operator run are required; checked-in JSON/YAML alone is not readiness evidence. Tracing remains disabled by default. With the explicitly provisioned `observability` extra and `TRACING_ENABLED=true`, app lifespan uses a private SDK and a 256-entry in-memory reviewed metadata processor, cleared on shutdown; it restores the prior tracer and configures no global provider or network exporter.
