# 16 — Observability, Testing, and Delivery Tutorial

**Status:** Extensive offline tests and runtime timing logs are implemented. Structured telemetry,
PostgreSQL integration tests, load testing, containers, and CI/CD are planned.

## Observability begins with questions

Instrumentation should answer:

- Which call, turn, stage, request, or job failed?
- Where was latency spent?
- Which provider and operation caused the error?
- Did retries help or amplify the problem?
- What did the call cost?
- Can the answer be obtained without exposing PII?

## Structured event contract

```json
{
  "timestamp": "2026-09-18T12:00:00Z",
  "level": "INFO",
  "event": "llm.completed",
  "call_id": "redacted-correlation-id",
  "turn_id": 7,
  "stage": "pivot",
  "latency_ms": 734,
  "language": "gu"
}
```

Use consistent event names and correlation identifiers. Do not put transcript text, full phone
numbers, tokens, or secrets into general logs.

## Metrics and traces

Measure at least:

- `voice_endpoint_latency`;
- `stt_latency`;
- `brain_latency`;
- `llm_first_token_latency`;
- `tts_first_audio_latency`;
- `safety_filter_latency`;
- `total_turn_latency`;
- API request rate/error/latency;
- job queue depth/age/retries/failures;
- Redis and PostgreSQL latency/connection pressure;
- cost by call, turn, and provider.

OpenTelemetry can connect a request/call/turn/job trace; Prometheus can aggregate numeric series;
Grafana can visualize them. None replaces a clear event and metric contract.

## Test strategy

| Layer | Purpose | Paid providers? |
|---|---|---:|
| Unit | State transitions, safety, cost, validation, routing | No |
| Provider contract/fake | Adapter behavior and error mapping | No |
| API integration | FastAPI + database/cache boundaries | No |
| Worker integration | Enqueue, claim, retry, idempotency, acknowledgement | No |
| Concurrency | Booking races and shared-state isolation | No |
| Evaluation | Conversation fixtures and safety canaries | No by default |
| Load | Capacity, latency percentiles, resource pressure | No paid APIs by default |
| Manual live call | Carrier/audio/provider reality | Yes; explicit approval |

CI should run almost entirely without Twilio, Sarvam, or OpenAI charges.

## Load-test ladder

Run controlled stages at 1, 10, 25, 50, and 100 concurrent sessions. Record requests/turns per
second, P50/P95/P99 latency, error rate, CPU, memory, Redis latency, database pool usage, and queue
age. State whether the traffic is synthetic HTTP, simulated media, or real calls.

A load test is useful only if it produces a bottleneck hypothesis and a repeatable report. Do not
claim "supports 100 calls" from a test that bypasses the components used by real calls.

## Reproducible delivery

Target Docker Compose services:

```text
FastAPI | PostgreSQL | Redis | Worker | Prometheus | Grafana
```

Target CI sequence:

```text
lint -> type check -> unit tests -> integration tests -> security checks
     -> migration check -> container build
```

Production deployment details should remain provider-neutral until a host is chosen. A development
Cloudflare tunnel is not production architecture.

## Reliability exercises

- Inject STT/LLM/TTS/Twilio timeouts and verify safe domain behavior.
- Show which operations may retry and which are too time-sensitive or unsafe.
- Map provider exceptions to stable domain errors.
- Demonstrate graceful degradation when an optional dependency is unavailable.
- Explain when a circuit breaker would help and when it would only hide failures.

## Completion evidence

The production-engineering level is complete only when a clean environment is reproducible, CI
blocks a real regression, traces explain a slow turn, alerts identify actionable failures, and the
load report names the first capacity limit.
