# Cross-level completion contract

This maps v4 sections 56–68 to project evidence. These requirements complement the individual level gates; they do not start implementation or authorize production traffic.

## Nine mandatory areas

| Area | Acceptance evidence owner |
|---|---|
| Appointment concurrency | L3 constraints/transactions/conflict race |
| Persistent conversation state | L2 restore; L7 checkpoint/recovery guarantees |
| Async jobs | L8 retry/idempotency/dead-letter; L10 recording flow |
| Security/auditability | L2 safety events; L8 auth/callback/PII/access approval |
| Observability/automated testing | L1 CI skeleton; L11 metrics/traces; L12 regression/chaos/load |
| Deterministic control | Conversation/safety contracts and regressions in every level |
| Measured local ASR/TTS | L4/L5 language/noise WER/CER/RTF and first-audio |
| Direct local LLM understanding | L2 tokenization/decoding, TTFT, tokens/s and VRAM before serving |
| Governed RAG | L9 approved scoped evidence, grounding/deferral and golden evaluation |

## Final demonstration

Demonstrate a supported-language input counselling call with approved Hindi-base Hinglish output and committed appointment confirmation. A second caller attempting the same slot receives a safe conflict. Show barge-in/disconnect cleanup, Redis/provider degradation, visible timings/queue/lock/safety/cost signals, an evidenced knowledge answer and a no-evidence deferral.

The student must explain deterministic versus generated decisions across the whole flow. At least one disaster-recovery drill must include impact, root cause and follow-up evidence. Put this evidence with the relevant level/report; do not create recurring session journals.

## Deliverables

Maintain the current/target architecture and failure flow, ER/schema/migrations/constraints/seeds, local-model benchmark report, RAG golden/benchmark report, automated testing evidence, real observability dashboards and a tested operating/recovery/rollback guide. Create implementation artifacts only when their level is requested.

Suggested ownership is backend/API, database/state, local models, RAG/search, real-time integration and QA/observability. Owners may divide work, but each student must explain the full request flow and interfaces. Team allocation is a suggestion, not automatic delegation or hiring.

## SLOs and learning review

Final numerical SLOs come from Level 12 measurements. Track user-stop-to-first-audio, event-loop stall, model/retrieval/queue delay, booking conflicts, exhausted jobs, safety escapes, no-evidence fallback and recording-access audit coverage. Treat roadmap latency/packet-loss examples as provisional and transport-specific. Repeated misses trigger reliability work before feature expansion.

The review should cover transactions versus LLM authority, PostgreSQL versus Redis, provider dependency inversion, local tokenization/decoding, WER/CER/RTF, TTFT/first audio, conditional retrieval, metadata/grounding/fallback, cancellation scope and measured concurrency. Optional rerankers/vector databases/quantization require a demonstrated bottleneck.

See [verification](12-verification.md), [operating guide](runbook.md) and [decision register](decisions.md).
