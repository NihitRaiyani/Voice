# Level 11 — Observability and analytics

**v4 sections:** 45–48. **Status:** Basic logs/model foundations present; full telemetry/analytics planned.

## Entry gate

Integrated Level 10 path and Level 8 privacy/security.

## Scope when implementation is requested

Add OpenTelemetry tracing and Prometheus/Grafana metrics across endpointing, models, retrieval, queues, database, carrier and GPU. Keep correlation meaningful while excluding PII/high-cardinality secrets.

Normalize carrier/GPU/resource usage and costs with explicit units; local inference still has hardware/energy costs. Produce SQL-backed outcomes/booking/latency analytics and reconcile totals. Optional supervisor events may use Redis PubSub/WebSocket with explicit best-effort semantics.

## Existing reuse in Voice_Agent

Timing/cost logs, call status, durable lifecycle/job records and usage/cost model foundations.

## Acceptance gate

- [ ] Dashboards show real stage/resource/error data rather than placeholders.
- [ ] SQL outcomes/bookings/usage reconcile with source records and documented units.
- [ ] Tracing/log labels and optional supervisor access respect privacy/roles.

## Boundaries and advanced work

No mandatory live-supervisor dashboard or durable-delivery claim for PubSub. No invented cost rates.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
