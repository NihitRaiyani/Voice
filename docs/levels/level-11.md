# Level 11 — Observability and analytics

**Roadmap sections:** 45–48. **Status:** Sections 45–46 telemetry and normalized usage/cost implementation present; combined offline verification passed. Sections 47–48 remain unstarted.

## Entry gate and authorized scope

Level 10 native acceptance and Level 8 privacy gates remain open. The owner explicitly authorized sections 45–46 only. Existing section 21 JSON logging/correlation remains; this increment adds reviewed bounded metrics/tracing and exact SQL cost projections. It does not implement full outcomes/booking analytics or supervisor events.

## Section 45 observation contract

`roma/core/telemetry.py` retains cumulative latency histogram buckets, counters and current gauges with finite stage/outcome/route/queue vocabularies. Optional OpenTelemetry spans use fixed reviewed attributes and suppress exception payload recording. `TRACING_ENABLED=false` is the default; explicit SDK-extra/setting opt-in creates a private 256-entry in-memory metadata history during app lifespan, cleared on shutdown, with no global provider or network exporter. No prompts, transcripts, phone numbers, provider bodies, arbitrary labels or call/turn IDs enter metric labels. Live Pipecat probes measure endpointing, ASR, controller, LLM first token, TTS first output and software turn completion; safety/retrieval/checkpoint/lock/queue/PLC/dead-letter operations add their defined signals. A process-lifecycle monitor observes event-loop stalls; CPU and peak RSS are measured, while unavailable GPU/current-RSS values remain unknown.

`GET /metrics` is private: actual socket loopback may scrape; other peers need a live active Admin JWT. Forwarded headers do not authorize a caller. A loopback reverse proxy must protect this route before forwarding. Native Prometheus/Grafana configuration is checked in under `configs/observability/`; any native startup proof belongs in the runbook/verification; configurations or synthetic samples do not establish real production dashboards, an acoustic latency metric or a native SLO.

## Section 46 usage and costs

Immutable `UsageEvent` records distinguish carrier/audio/GPU seconds, ordinary input/cache-read/cache-write/output tokens, characters and cache hits. SQL atomically stores an idempotent usage receipt plus exact Decimal cost lines; same-key changed content conflicts. No invoice, exchange rate, GPU time or local cost is fabricated. Unknown usage/rates/billing basis/cache-write TTL remain explicitly unpriced, with unknown-unit and unpriced-event counts. Historical costs are not repriced.

Protected cost routes report call/turn, UTC day/month and provider projections with per-currency totals and averages, decimal strings and completeness flags. They require `analytics.view`; the existing Admin/Counsellor/Viewer grants apply. Conservative INR spend reservations remain separate from measured usage/USD estimates. The reviewed direct standard Haiku 5.5 rate card and provenance are in [the observation contract](../16-observability-testing-and-delivery.md#section-46-normalized-usage-and-costs); account invoices remain authoritative.

## Evidence and acceptance

Combined checks passed; exact test counts and source fingerprints are recorded in [verification](../12-verification.md#sections-4146-telephony-serving-telemetry-and-costs--2026-10-26). Offline checks must cover label/payload privacy, cancellation/duplicate/unknown usage, currency separation, exact totals, role denial and empty/missing calls. Real dashboard signals, intended-environment reconciliation, native resource capacity and production access remain separate.

- [ ] Native dashboards display actual stage/resource/error data with documented measurement boundaries.
- [ ] Intended-environment SQL usage/cost records reconcile with source usage and provider billing basis.
- [ ] Tracing, metrics and dashboard access pass operational privacy/role review.
- [ ] Separately authorized sections 47–48 supply their own outcome/supervisor evidence.

No Level 11 gate passes; sections 47–48 and Level 12 are not implicitly started.

Return to the [documentation index](../README.md).
