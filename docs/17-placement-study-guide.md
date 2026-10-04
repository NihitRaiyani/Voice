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

## Level 1 section 3 explanation

PostgreSQL keeps durable business facts with relationships, constraints and atomic commits. Redis serves fast operational context whose expiry/reconstruction is expected; enabling Redis disk persistence does not make it the business authority. If Redis loses an active checkpoint, consult durable records rather than inventing an appointment or erasing a completed call.

Trace one unit of work from session creation through query/write, explicit commit or rollback and closure. Then show that inference/audio waits occur after exit. Explain why 100 active calls need short shared connection checkouts rather than 100 minute-long transactions, and why reusing one session across concurrent tasks is unsafe.

With size 5, overflow 10 and four engine-owning processes, retained capacity is 20 but peak is 60. Compare peak with PostgreSQL capacity after reserved slots, other clients and operational headroom. Demonstrate bounded timeout, recovery after release, cancellation cleanup and the difference between synthetic pool evidence and real voice capacity. See [L1 results](levels/level-01.md) and the [operator checker](runbook.md#database-capacity-and-pool-pressure).

## Level 1 section 4 explanation

Use the [relational catalog](20-relational-schema.md) to justify each key, check and index. Show a duplicate checkpoint/result rejection, an orphan-reference rejection, call-to-checkpoint ownership cascade, run-to-result cascade and model deletion blocked by measured results. Explain why relational booking truth stays outside flexible checkpoint JSON, why duplicate metric rows cannot silently replace a measurement, and why a signed finite metric is valid while NaN is not.

Describe migration compatibility: old rows/keys survive; optional turn language remains unknown for old rows; deleting the new tables during rollback loses their new data. Explain required checkpoint expiry, benchmark metadata without raw PII/audio, pending cleanup/restore/runner integration, and Level 9 knowledge-table deferral. Schema foundation and operational feature completion are different evidence.

## Level 1 section 5 explanation

Trace the preserved Alembic revision chain from initial schema to webhook receipts to checkpoint/benchmark extensions. Explain why an applied revision is immutable and why a reviewed future migration owns its indexes. Demonstrate offline SQL review, empty/legacy-populated upgrade, metadata parity and disposable downgrade/re-upgrade using the [migration guide](21-database-migrations.md).

Run the opt-in demo command twice and explain stable identities, database uniqueness, the zero-row second insert and atomic rollback on conflict. Distinguish inactive synthetic offerings/role names from approved business data or user permissions. Explain why database-only operations need no AI/carrier keys, and why successful schema rollback does not recover dropped records.

## Level 1 section 6 explanation

Trace the modular monolith from `roma/main.py` to API adapters, services,
domain rules, repositories, providers, realtime voice processors and workers.
Explain why the v4 `backend/app` tree maps to existing packages rather than a
rename, and why a package name is less important than dependency direction.

Show the architecture test that keeps `roma/domain/**` free of FastAPI,
provider SDKs, concrete repositories, realtime processors and workers. Defend
the two cleanup changes: Redis checkpoint storage is a repository adapter used
by realtime composition, while the domain exposes only the checkpoint protocol
and in-memory adapter; private file permission helpers live in `roma.core`
instead of making the spend ledger depend on post-call workers.

Explain what this does not finish: versioned public API schemas, provider
contract mocks, type checking, CI/pre-commit and native one-command startup are
still later Level 1 work.

## Level 1 section 7 explanation

Show how `STTProvider`, `LLMProvider`, `TTSProvider`, `EmbeddingProvider` and
`TelephonyProvider` hide SDK/runtime details behind small request/result types.
Explain why mocks are the default AI providers for tests, why future Qwen/Indic
classes fail fast until their model levels are complete, and why switching a
provider should mean one adapter plus one config value rather than rewriting
callers.

Defend the current limit: the Pipecat media pipeline still uses the existing
OpenAI/Sarvam comparison path. Replacing it belongs with local inference,
speech quality, latency and cancellation evidence, not with the Level 1
contract foundation.
