# Backend roadmap and readiness

v4 is the active curriculum. The [level briefs](README.md) own acceptance gates; this chapter maps existing backend foundations to remaining work. Present source is not a completed v4 gate.

| Level | Existing reuse | Missing acceptance work |
|---|---|---|
| 0 | Current source, tests, evaluation and call trace | Clean-machine restore, representative timing and prerequisite review |
| 1 | Layered monolith, PostgreSQL/Alembic, pools, FastAPI and pytest/Ruff | Full ASR/LLM/extraction/TTS contracts/mocks, versioned public API, type-check/pre-commit/CI and native one-command startup |
| 2 | Deterministic stages, extraction/time resolver, prompt/safety logic | Direct local Qwen candidate, durable milestone restore and safety audit |
| 3 | Row-locked appointment repository, partial active-slot uniqueness and race test | Live/API integration, cancellation/reuse, holds policy and truthful commit confirmation |
| 4–5 | Carrier codecs, Silero/contextual endpointing, cloud speech/cache tests | Actual local ASR/TTS and language/noise/RTF benchmarks |
| 6–7 | Pipecat turn pipeline, cancellation/isolation/Redis degradation seams | Local microphone loop, bounded local inference queues and tested checkpoint/pressure recovery |
| 8 | Twilio signatures, atomic answer receipt, PostgreSQL job ledger, Dramatiq and keyed caller linkage | User/RBAC/token lifecycle, replay policy, complete PII/access/deletion, job publication bounds and audit |
| 9 | Static approved facts, PostgreSQL/provider seams | Governed documents, local embeddings, pgvector, grounding/fallback and multilingual golden set |
| 10 | Twilio media, native serializer and local recording handoff | Local model serving/GPU admission, retrieval timing/cancellation and authorized live evidence |
| 11–12 | Timing/cost logs and extensive offline tests | Real telemetry/SQL analytics, representative chaos/load/security/quality/SLO reports |
| 13 | Pre-v4 local database/Redis Compose | Full clean-host local-model deployment, health/readiness, backup/restore and rollback |
| 14 | No chosen extension | Optional measured experiment only |

## Next implementation boundary

Complete missing Level 0 evidence first. When implementation is requested, Level 1 should extend the existing foundation rather than recreate migrations, package layers or carrier adapters. Each reused component must be assessed against the requested gate and current tests.

At Level 3, wire the already-built repository into the conversation safely. At Level 8, retain the transactional webhook and job designs and close their actual access/replay/privacy/backpressure gaps. Keep operational commands in the [runbook](runbook.md), not in competing plans.

See [decisions](decisions.md), [source mapping](roadmaps/README.md) and [verification](12-verification.md).
