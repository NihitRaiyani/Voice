# Observability, testing and delivery

**Present:** timing/cost logs, offline evaluations and database/worker integration test sources. **Pending:** production tracing/metrics dashboards, SQL analytics integration, real local-model capacity/chaos reports, CI hardening and full deployment packaging.

## Measurement design

Start with a question: which call/turn/stage failed, where time was spent, what retried, what effect committed and what resource saturated? Use PII-safe correlation and stable event names. Do not use phone numbers/transcripts/tokens as trace or metric labels.

Measure endpoint/ASR/extraction/LLM first token/TTS first audio, conditional retrieval/fallback, end-to-end response, queues, event-loop lag, database pools, Redis, GPU, safety, job attempts and normalized carrier/hardware costs. SQL outcomes must reconcile with durable records.

L11 adds OpenTelemetry/Prometheus/Grafana; tools consume defined signals rather than inventing them. Optional supervisor PubSub/WS is best effort and requires role/security controls. A dashboard with sample data is not production evidence.

`model_registry`, `benchmark_runs` and `benchmark_results` now provide the relational evidence foundation. Future runners must pin model/version and dataset identity, record hardware/configuration and meaningful units/sample counts, and keep PII/sample media out of these metadata rows. A schema row or `is_active` flag is not a license approval, completed benchmark or production selection. See the [schema catalog](20-relational-schema.md).

## Tests and capacity

Unit/provider-fake/API/repository/worker/concurrency/evaluation tests make no paid calls. PostgreSQL tests need a disposable database; report skips and environment limits. Manual local-model/GPU and authorized carrier checks are separate evidence.

L12 exercises slow providers, Redis loss, queue pressure, abrupt disconnect and RAG fallback. Separate mocked backend load from real GPU capacity. Grow 1/10/25/50/100 sessions only while agreed latency/error/resource gates hold; stop at measured saturation. Record P50/P95/P99, sample/configuration/hardware identity, queue wait, cold/warm state and failures.

## Delivery and SLOs

L1 introduces the expanded quality/CI skeleton; L12 adds security scans, migration/regression checks and zero-external-GenAI enforcement for the final local profile. L13 introduces full Docker/GPU Compose packaging, mounted weights/data, runtime secrets, readiness, clean-host startup, backup/restore and rollback.

Existing database/Redis Compose predates v4 and does not complete L13. Native-service startup remains the foundation-learning path. Roadmap latency/event-loop/packet-loss numbers are provisional and transport-specific. Agree SLO/error-budget policy after measurements; recurring misses prioritize reliability.

See [verification](12-verification.md), [L11](levels/level-11.md), [L12](levels/level-12.md), [L13](levels/level-13.md) and [runbook](runbook.md).
