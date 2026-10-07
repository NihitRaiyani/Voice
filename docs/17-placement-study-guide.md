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

Describe migration compatibility: old rows/keys survive; optional turn language remains unknown for old rows; deleting the new tables during rollback loses their new data. Explain required checkpoint expiry, benchmark metadata without raw PII/audio, Level 2 latest-checkpoint restore, pending cleanup/redial/runner integration, and Level 9 knowledge-table deferral. Schema foundation and operational feature completion are different evidence.

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

Explain what this does not finish: type checking, CI/pre-commit and native
one-command startup are still later Level 1 work. Provider contract mocks and
versioned public API schemas were completed in sections 7 and 8.

## Level 1 section 7 explanation

Show how `STTProvider`, `LLMProvider`, `TTSProvider`, `EmbeddingProvider` and
`TelephonyProvider` hide SDK/runtime details behind small request/result types.
Explain why mocks are the default AI providers for tests, why remaining future Indic/vLLM
classes fail fast until their model levels are complete (Qwen Transformers is now implemented in section 11), and why switching a
provider should mean one adapter plus one config value rather than rewriting
callers.

Defend the current limit: the Pipecat media pipeline still uses the existing
OpenAI/Sarvam comparison path. Replacing it belongs with local inference,
speech quality, latency and cancellation evidence, not with the Level 1
contract foundation.

## Level 1 section 8 explanation

Show the `/api/v1` resource contract as the public business API: every success
returns `{success, data, meta}` and every error returns `{success: false,
error}` with a stable code and message. Explain how list endpoints expose
pagination, filters and sort metadata so callers can replay exactly what the
server applied.

Trace route handlers into `RestApiService`. Defend why each request opens short
units of work through the configured session factory instead of holding a
database transaction across audio, inference or carrier operations. Map the
important failures: missing/invalid bearer token is 401, appointment slot
conflict is 409, Pydantic request/query validation is 422 and unavailable
configured database access is 503.

Explain why `/api/call` stays separate from `POST /api/v1/calls`: the legacy
route performs the explicit carrier dial side effect, while the v1 call resource
creates durable business records. Joining those flows needs a later tested
client migration. Also state the current security limit clearly: Level 8 RBAC,
sessions, revocation and callback replay policy are still pending.

## Level 1 section 9 explanation

Show the first-week engineering loop: `make services-up` starts native local
PostgreSQL/Redis, `make db-upgrade` applies Alembic, `make app-start` boots the
backend, `make ci` runs the bounded hosted-check equivalent and `make test` runs
the full offline suite separately. Explain why Docker remains Level 13 and why
local service state belongs under ignored `var/`, not in the repository.

Defend the split between the CI skeleton and the full disposable integration
suite. CI proves cheap day-one invariants: Ruff, mypy over stable contracts,
empty-database migrations and mock-provider/interface tests. The full suite can
still use heavier disposable PostgreSQL/Redis tests, but it should not fight the
native migration smoke for local database resources.

Explain the security rule: secrets stay in environment variables or `.env`, both
outside Git. The defaults used by the Makefile are local mock/development values
for tests and startup smoke only; they do not authorize paid provider calls,
carrier dials or production credentials.
## Level 2 section 10 explanation

Show the seven-stage machine as business state, not prompt wording: `OPEN -> DISCOVER -> VALUE -> STRUCTURE -> PIVOT -> OBJECTION -> CLOSE`. Explain that `ConversationStage`, `transition()` and `can_transition()` decide phase changes from explicit `TurnSignals`; the LLM only receives the task for how to say the next line. If `city` is missing in discover, the controller asks the short city question instead of letting the model pick a new stage.

Trace checkpoint persistence through `save_state()`/`restore_state()`: Redis remains the hot same-call cache, while `PostgresConversationStateStore` writes the latest compatible `CallState` to `conversation_states` through short unit-of-work sessions. The row carries schema version, policy version, revision, canonical stage, JSON state and expiry. Restore refuses missing calls, expired rows and schema/policy mismatches rather than replaying unsafe state.

Defend `route_intent()` as a seam for later work: deterministic replies stay code-owned, booking paths stay under slot/confirmation rules, and knowledge goes to the current model prompt today. Level 9 can attach RAG to the knowledge route without allowing retrieved text to select a stage or weaken safety. State clearly what remains pending: Qwen3-8B suitable-host inference/native review, structured safety-event audit, redial/session identity and L7 recovery drills. Section 11 now wires the text console and direct Transformers adapter.

## Level 2 section 11 explanation

Trace text → chat template/tokenizer → token IDs → device → model/logits → greedy or sampled tokens → decoded chunks. Show `Qwen3TransformersLLM` behind `LLMProvider` and explain lazy loading, `eval()` versus `inference_mode()`, BF16/FP16/FP32 versus INT4 storage/compute, and why Qwen thinking is disabled for narrow replies. Explain why an 8 GiB Mac cannot run full-precision Qwen3-8B and why an explicit smaller-model experiment is different evidence.

Run the text console to show current state, schema-validated extracted slots, one narrow task/prompt, raw model output, deterministic safety and final reply. Demonstrate that malformed JSON cannot select a stage, code resolves dates, model failure uses safe fallback and a conversational lock is not a committed booking. Explain request serialization and stopping at token boundaries; Level 7 still owns production streaming/queue guarantees.

Defend the metrics: TTFT is first generated token after loading/tokenization, not first printable word; tokens/second uses generated token count and elapsed generation time. Report cold load separately and CUDA peak memory only when available. Explain why a tiny random-weight smoke proves decoding mechanics but no language quality. The 40-case lab covers every stage in Gujarati/Hindi/English/code-mix; native correctness/naturalness must average ≥4/5 per language, every score ≥3/5, with zero unsafe final replies. Missing reviews and mock outputs cannot pass. Read [the local LLM guide](25-local-transformers-llm.md); suitable-host 8B/native evidence and section 12 safety audit remain pending.
