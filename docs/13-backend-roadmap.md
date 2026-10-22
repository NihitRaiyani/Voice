# Backend roadmap and readiness

The Claude roadmap is the active curriculum. The [level briefs](README.md) own acceptance gates; this chapter maps existing backend foundations to remaining work. Present source is not a completed roadmap gate.

| Level | Existing reuse | Missing acceptance work |
|---|---|---|
| 0 | Current source, tests, evaluation and call trace | Clean-machine restore, representative timing and prerequisite review |
| 1 | Layered monolith, enforced domain dependency rule, STT/LLM/TTS/embedding/telephony provider contracts with mocks, PostgreSQL/Alembic, bounded pools/process allocation, capacity checker, checkpoint/model/benchmark relational foundations, database-only migration/rollback workflow, separate atomic demo seeds, pressure/release tests, FastAPI, versioned resource API, pytest/Ruff, mypy skeleton, pre-commit hooks, native Makefile startup and GitHub Actions workflow | Clean-host Level 0 evidence and full reviewer/demo evidence |
| 2 | Deterministic stages, state-machine facade/checkpoint adapter, extraction/time resolver, prompt/safety logic, Claude provider/text console, separate language lab, deterministic safety audit/input guards and safety queries | Native/paid timing and later local-stack/live integration |
| 3 | Row-locked repository/index, controller/API commits, advisory TTL holds, cancellation/reuse, retries and same-SID appointment reconciliation; HTTP and repository race tests | Approved real reference slots, teacher demo and later complete voice approval |
| 4–5 | Carrier codecs, section 14 strict PCM/stateful conversion/fixed-buffer lab and live pre-decode sequence validation, Silero/contextual endpointing, sections 15–17 code-owned endpoint lab, cache-only/hosted-lab STT adapters, saved language routing, hardened audio/STT boundaries, measured public ASR/failure/SQL evidence and actual three-language local Indic-TTS with nine cache hits | Representative/native language and code-mix review, local model readiness recorded in Level 5, full Level 4 gate before adaptive jitter/PLC |
| 6–7 | Section 20 isolated microphone/speaker integration, durable turn/timing adapter and offline language paths; Pipecat cancellation/isolation seams | Representative ASR quality, Claude/headphone/native demonstration, full interruption/pressure recovery |
| 8 | Twilio signatures, atomic answer receipt, PostgreSQL job ledger, Dramatiq and keyed caller linkage | User/RBAC/token lifecycle, replay policy, complete PII/access/deletion, job publication bounds and audit |
| 9 | Static approved facts, PostgreSQL/provider seams | Governed documents, local embeddings, pgvector, grounding/fallback and multilingual golden set |
| 10 | Twilio media, native serializer and local recording handoff | Local speech model serving/GPU admission, retrieval timing/cancellation and authorized live evidence |
| 11–12 | Timing/cost logs and extensive offline tests | Real telemetry/SQL analytics, representative chaos/load/security/quality/SLO reports |
| 13 | Pre-v4 local database/Redis Compose | Full clean-host speech-model/Claude deployment, health/readiness, backup/restore and rollback |
| 14 | No chosen extension | Optional measured experiment only |

## Next implementation boundary

The current request aligns existing implementation through section 20 with the Claude roadmap. Claude Haiku 5.5 is the main LLM, with fresh paid Claude/microphone checks deferred and no OpenAI calls/fallback. Existing migrations, provider contracts, APIs, carrier/speech and deterministic policies remain reusable. Section 21 and later work are outside this increment. [The source audit](roadmaps/sections-01-10-review.md#claude-source-review-through-section-20--2026-10-20) distinguishes implemented foundations from unfinished acceptance.

The owner-approved local development ASR is `whisper-turbo-batched-v2`, using the same pinned 464 MB MLX 4-bit Turbo weights. Pure Gujarati/Hindi/English decoding is unchanged. Explicit mixed turns may be partitioned at the strongest detected speech pause into at most two contiguous chunks; every PCM sample is retained and one model encodes/decodes the batch. Completed rows leave the active decoder batch. Context preserves Gujarati/Hindi script unless a chunk has strong English language evidence. No second ASR model, cascade, retry or paid provider is used. All 15 public cases execute; English output is retained in all six mixed cases and five suffixes are exact, but noisy Gujarati–English, native accuracy and voice latency still fail acceptance. Paid Claude, microphone and native review remain deferred. Existing section 20 controller, booking, durable turn/timing and English hardware evidence remain separate from complete acceptance.

Level 3 now reuses the existing repository/index behind a short confirmation commit, with offline concurrency/failure evidence in its brief. At Level 8, retain the transactional webhook and job designs and close their actual access/replay/privacy/backpressure gaps. Keep operational commands in the [runbook](runbook.md), not in competing plans.

See [decisions](decisions.md), [source mapping](roadmaps/README.md) and [verification](12-verification.md).
