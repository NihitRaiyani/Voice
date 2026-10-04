# Level 12 — Evaluation and measured capacity

**v4 sections:** 49–52. **Status:** Offline test foundation present; production benchmarks/chaos/capacity gate pending.

## Entry gate

Levels 10/11 integrated stack, telemetry and earlier acceptance suites.

## Scope when implementation is requested

Run reproducible language/model/RAG/latency benchmarks with dataset/configuration/hardware identity and cold/warm conditions. Separate mocked backend concurrency from real GPU inference capacity.

Exercise Redis loss, slow providers, pressure, abrupt disconnect and retrieval fallback. Increase 1/10/25/50/100 calls only while agreed resource/latency/error gates hold; stop at measured capacity. Set production SLOs from evidence.

Harden CI/CD with dependency/security scans, golden regressions and enforcement that the final local profile cannot call external GenAI APIs. Preserve rollback compatibility and failure runbooks.

## Existing reuse in Voice_Agent

Extensive offline tests/evals, real PostgreSQL/Redis test fixtures and each earlier level's lab/benchmark evidence, plus L1 model registry/run/result schema. Actual benchmark producers, data approval and report integration remain pending.

## Acceptance gate

- [ ] Benchmark/capacity report includes percentiles, failures/fallbacks, saturation and stop criteria.
- [ ] Chaos/degraded-state tests preserve safety, booking truth, cleanup and bounded resources.
- [ ] Golden RAG/voice regressions and security/local-production policy checks pass.
- [ ] Measured SLOs and operational responses are agreed; mock load is not presented as GPU capacity.

## Boundaries and advanced work

No promise of 100 real calls or sub-1.2-second SLO from a roadmap example.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
