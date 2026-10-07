# Backend roadmap and readiness

v4 is the active curriculum. The [level briefs](README.md) own acceptance gates; this chapter maps existing backend foundations to remaining work. Present source is not a completed v4 gate.

| Level | Existing reuse | Missing acceptance work |
|---|---|---|
| 0 | Current source, tests, evaluation and call trace | Clean-machine restore, representative timing and prerequisite review |
| 1 | Layered monolith, enforced domain dependency rule, STT/LLM/TTS/embedding/telephony provider contracts with mocks, PostgreSQL/Alembic, bounded pools/process allocation, capacity checker, checkpoint/model/benchmark relational foundations, database-only migration/rollback workflow, separate atomic demo seeds, pressure/release tests, FastAPI, versioned resource API, pytest/Ruff, mypy skeleton, pre-commit hooks, native Makefile startup and GitHub Actions workflow | Clean-host Level 0 evidence and full reviewer/demo evidence |
| 2 | Deterministic stages/checkpoint adapter, Transformers and Apple INT4/text/language labs, extraction/time resolver and prompt/safety | Selected 1.7B native review, suitable-host 8B comparison, structured safety audit and live integration |
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

The user explicitly requested Level 1 sections 3–9 while the remaining Level 0 evidence stays pending. These bounded persistence/schema/migration-workflow/architecture/provider/API/testing increments extend the existing foundation; they do not pass Level 0 or all of Level 1. Subsequent work should retain migrations, package layers, provider contracts, versioned API envelopes and carrier adapters. Each reused component must be assessed against the requested gate and current tests.

At Level 3, wire the already-built repository into the conversation safely. At Level 8, retain the transactional webhook and job designs and close their actual access/replay/privacy/backpressure gaps. Keep operational commands in the [runbook](runbook.md), not in competing plans.

See [decisions](decisions.md), [source mapping](roadmaps/README.md) and [verification](12-verification.md).
