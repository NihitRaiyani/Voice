# Placement and viva study guide

Use v4's level sequence as a learning progression. Trace behavior, identify the invariant, reproduce a failure, show evidence and defend a trade-off. Do not memorize technology names or claim unmeasured scale.

| Levels | Concepts to explain | Evidence |
|---|---|---|
| 0–1 | Async HTTP/WS/audio, modular monolith, contracts/mocks, ER/migrations/pools, versioned APIs | Call trace, clean setup, ER/contract and CI checks |
| 2–3 | Deterministic stages, tokenization/decoding, TTFT, structured extraction, booking transactions/locks/uniqueness | Text/local model demo, restore/safety tests, race/cancel/conflict report |
| 4–6 | PCM/μ-law/resampling, VAD versus endpointing, language tracking, WER/CER/RTF and first audio | Representative speech benchmarks and local voice demo |
| 7–8 | Backpressure, CPU offload, cancellation, recovery, idempotency, leases/retries, auth/RBAC/privacy | Failure trace, queue tests, duplicate/crash tests and permission/data-flow map |
| 9–10 | Embeddings, scoped retrieval, grounding/deferral, serving/GPU admission and carrier integration | Golden RAG report and authorized local-model live evidence |
| 11–13 | Traces/metrics, SQL analytics/cost units, capacity/chaos/SLOs and deployment recovery | Real dashboard, capacity report, clean-host startup/rollback and recovery drill |
| 14 | Quantization/fine-tuning/reranking/hybrid search trade-offs | Measured bottleneck and reproducible improvement |

## Questions to defend

1. Why do PostgreSQL and Redis have different authority/lifetimes?
2. Why is a repository-safe booking not yet a truthful live confirmation?
3. What prevents two active bookings and how does cancellation free both records?
4. Why can a callback handler run repeatedly but commit one business effect?
5. What happens after a commit succeeds but an HTTP/broker acknowledgement is lost?
6. Why do recording and Dramatiq consumers have different scaling constraints?
7. What does direct local inference teach before vLLM?
8. How do Gujarati/code-mix WER/CER and TTS intelligibility differ from a few successful demos?
9. Which queues can drop data and which cannot?
10. What must be cancelled/cleared during barge-in?
11. Why can retrieved text neither select a stage nor override safety?
12. How do scope/version/status filters and no-evidence deferral prevent wrong answers?
13. What do mock load tests fail to prove about GPU/carrier capacity?
14. Why is Docker last and what makes a rollback safe for schema/jobs/model state?
15. What measured problem would justify optional infrastructure or model optimization?

Answer with context → invariant → design → failure → evidence → trade-off. Explain from actual code/tests, including gaps. For example, active-slot uniqueness exists today, but the controller still needs database integration; do not describe the full gate as complete.

Each student should explain the full request flow even when ownership is divided across backend, database, models, retrieval, real-time and QA. Use [completion criteria](completion-contract.md) for final demonstrations/deliverables and [level briefs](README.md) for the current scope.
