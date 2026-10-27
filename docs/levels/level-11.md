# Level 11 — Observability and analytics

**Roadmap sections:** 45–48. **Status:** Sections 45–46 telemetry and normalized usage/cost implementation present; combined offline verification passed. Sections 47–48 SQL analytics and a bounded Admin supervisor channel are implemented; final combined evidence is recorded below.

## Entry gate and authorized scope

Level 10 native acceptance and Level 8 privacy gates remain open. The owner explicitly authorized sections 45–48, retaining earlier native/privacy entry gates as separate acceptance. Existing section 21 JSON logging/correlation remains; this increment adds reviewed bounded metrics/tracing and exact SQL cost projections. Sections 47–48 extend these projections with SQL outcomes and ephemeral supervision.

## Section 45 observation contract

`roma/core/telemetry.py` retains cumulative latency histogram buckets, counters and current gauges with finite stage/outcome/route/queue vocabularies. Optional OpenTelemetry spans use fixed reviewed attributes and suppress exception payload recording. `TRACING_ENABLED=false` is the default; explicit SDK-extra/setting opt-in creates a private 256-entry in-memory metadata history during app lifespan, cleared on shutdown, with no global provider or network exporter. No prompts, transcripts, phone numbers, provider bodies, arbitrary labels or call/turn IDs enter metric labels. Live Pipecat probes measure endpointing, ASR, controller, LLM first token, TTS first output and software turn completion; safety/retrieval/checkpoint/lock/queue/PLC/dead-letter operations add their defined signals. A process-lifecycle monitor observes event-loop stalls; CPU and peak RSS are measured, while unavailable GPU/current-RSS values remain unknown.

`GET /metrics` is private: actual socket loopback may scrape; other peers need a live active Admin JWT. Forwarded headers do not authorize a caller. A loopback reverse proxy must protect this route before forwarding. Native Prometheus/Grafana configuration is checked in under `configs/observability/`; any native startup proof belongs in the runbook/verification; configurations or synthetic samples do not establish real production dashboards, an acoustic latency metric or a native SLO.

## Section 46 usage and costs

Immutable `UsageEvent` records distinguish carrier/audio/GPU seconds, ordinary input/cache-read/cache-write/output tokens, characters and cache hits. SQL atomically stores an idempotent usage receipt plus exact Decimal cost lines; same-key changed content conflicts. No invoice, exchange rate, GPU time or local cost is fabricated. Unknown usage/rates/billing basis/cache-write TTL remain explicitly unpriced, with unknown-unit and unpriced-event counts. Historical costs are not repriced.

Protected cost routes report call/turn, UTC day/month and provider projections with per-currency totals and averages, decimal strings and completeness flags. They require `analytics.view`; the existing Admin/Counsellor/Viewer grants apply. Conservative INR spend reservations remain separate from measured usage/USD estimates. The reviewed direct standard Haiku 5.5 rate card and provenance are in [the observation contract](../16-observability-testing-and-delivery.md#section-46-normalized-usage-and-costs); account invoices remain authoritative.

## Sections 47–48 SQL analytics and supervisor

`GET /api/v1/analytics/overview`, `/funnel` and `/branches` use bounded half-open UTC date windows and `analytics.view` access. Calls use a call-start cohort; usage costs retain their own occurrence window and exact per-currency totals. Duration excludes missing/negative intervals; missing latencies remain unknown. Conversion requires an SQL appointment in booked/confirmed/completed state. Branch attribution uses one unambiguous SQL appointment branch, otherwise unknown; caller city/free text is never treated as branch authority. Ordered prospective `analytics.*` events support the funnel; historical final-stage values cannot fabricate missing milestones. Confirmation still requires SQL appointment evidence.

`GET /api/v1/supervisor/snapshot` and `WS /api/v1/supervisor/ws` are active-Admin-only. Authenticate the WebSocket in its first JSON frame (`token`), within five seconds; query credentials and cross-origin browser admissions are rejected. One owner-token Redis lease bounds each user's connections. Live SQL-backed role/token rechecks run every five seconds. Strict content-free messages contain internal UUID, stage, language, turn, duration, SQL booking truth, safety count, known latency and exact currency costs. Provider SID and caller content never leave the local resolver. A 64-item producer queue and 16-item per-subscriber queue bound delivery; slow clients/outages disconnect and require the protected SQL snapshot. Pub/Sub has no replay guarantee.

Durable voice checkpoints submit metadata to the owned background publisher after SQL succeeds. Completed software playback submits the caller turn identity frozen after controller admission and measured software duration; the opener is excluded from persisted caller-turn latency to avoid ordinal collisions; interrupted turns do not become successful latency samples. Event delivery and supervisor failures cannot block the call or create a booking. Queue loss/shutdown can leave prospective funnel history incomplete; no later stage backfills that gap. This is a backend channel; a visual supervisor dashboard and real-call reconciliation remain acceptance work.

## Evidence and acceptance

Combined checks passed; exact test counts and source fingerprints are recorded in [verification](../12-verification.md#sections-4146-telephony-serving-telemetry-and-costs--2026-10-26). Offline checks must cover label/payload privacy, cancellation/duplicate/unknown usage, currency separation, exact totals, role denial and empty/missing calls. Real dashboard signals, intended-environment reconciliation, native resource capacity and production access remain separate.

- [ ] Native dashboards display actual stage/resource/error data with documented measurement boundaries.
- [ ] Intended-environment SQL usage/cost records reconcile with source usage and provider billing basis.
- [ ] Tracing, metrics and dashboard access pass operational privacy/role review.
- [ ] Intended-environment analytics and supervisor displays reconcile with representative real calls.

No full Level 11 gate passes from offline/synthetic evidence. Sections 49–53 are separately owner-authorized.

Return to the [documentation index](../README.md).
