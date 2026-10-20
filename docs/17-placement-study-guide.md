# Placement and viva study guide

Use the Claude roadmap's level sequence as a learning progression. Trace behavior, identify the invariant, reproduce a failure, show evidence and defend a trade-off. Do not memorize technology names or claim unmeasured scale.

| Levels | Concepts to explain | Evidence |
|---|---|---|
| 0–1 | Async HTTP/WS/audio, modular monolith, contracts/mocks, ER/migrations/pools, versioned APIs | Call trace, clean setup, ER/contract and CI checks |
| 2–3 | Deterministic stages, Claude streaming/budgets, TTFT, structured extraction, booking transactions/locks/uniqueness | Claude/mock text demo, restore/safety tests, race/cancel/conflict report |
| 4–6 | PCM/μ-law/resampling, VAD versus endpointing, language tracking, WER/CER/RTF and first audio | Representative speech benchmarks and local voice demo |
| 7–8 | Backpressure, CPU offload, cancellation, recovery, idempotency, leases/retries, auth/RBAC/privacy | Failure trace, queue tests, duplicate/crash tests and permission/data-flow map |
| 9–10 | Embeddings, scoped retrieval, grounding/deferral, serving/GPU admission and carrier integration | Golden RAG report and authorized local speech/Claude live evidence |
| 11–13 | Traces/metrics, SQL analytics/cost units, capacity/chaos/SLOs and deployment recovery | Real dashboard, capacity report, clean-host startup/rollback and recovery drill |
| 14 | Quantization/fine-tuning/reranking/hybrid search trade-offs | Measured bottleneck and reproducible improvement |

## Questions to defend

1. Why do PostgreSQL and Redis have different authority/lifetimes?
2. Why is a repository-safe booking not yet a truthful live confirmation?
3. What prevents two active bookings and how does cancellation free both records?
4. Why can a callback handler run repeatedly but commit one business effect?
5. What happens after a commit succeeds but an HTTP/broker acknowledgement is lost?
6. Why do recording and Dramatiq consumers have different scaling constraints?
7. Why keep Claude behind a provider interface, with bounded retries and a spend cap?
8. How do Gujarati/code-mix WER/CER and TTS intelligibility differ from a few successful demos?
9. Which queues can drop data and which cannot?
10. What must be cancelled/cleared during barge-in?
11. Why can retrieved text neither select a stage nor override safety?
12. How do scope/version/status filters and no-evidence deferral prevent wrong answers?
13. What do mock load tests fail to prove about GPU/carrier capacity?
14. Why is Docker last and what makes a rollback safe for schema/jobs/model state?
15. What measured problem would justify optional infrastructure or model optimization?

Answer with context → invariant → design → failure → evidence → trade-off. Explain from actual code/tests, including gaps. For example, active-slot uniqueness and short controller commits exist today, while approved reference slots and the teacher/live demo remain pending; do not describe the full gate as complete.

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
one-command startup were completed in section 9. Provider contract mocks and
versioned public API schemas were completed in sections 7 and 8.

## Level 1 section 7 explanation

Show how `STTProvider`, `LLMProvider`, `TTSProvider`, `EmbeddingProvider` and
`TelephonyProvider` hide SDK/runtime details behind small request/result types.
Explain why tests and synthetic CLI runs select mock AI providers, why future Indic speech/embedding
classes fail fast until their model levels are complete, and why switching a
provider should mean one adapter plus one config value rather than rewriting
callers.

Defend the current limit: the Pipecat media pipeline still uses the existing
Claude/Sarvam comparison path. Replacing Sarvam speech belongs with local ASR/TTS inference,
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

Defend `route_intent()` as a seam for later work: deterministic replies stay code-owned, booking paths stay under slot/confirmation rules, and knowledge goes to the current model prompt today. Level 9 can attach RAG to the knowledge route without allowing retrieved text to select a stage or weaken safety. State clearly what remains pending: native/paid Claude quality/latency, redial/session identity and L7 recovery drills.

## Level 2 section 11 explanation

Trace one text turn through locally parsed name or validated JSON extraction, `advance_turn()`, the selected state/task prompt, `ClaudeLLM.generate()`, safety and checkpoint. The model never chooses the stage or commits a booking. A schema-valid answer can still be semantically wrong; show the conflicting-day/nonexistent-offer/out-of-stage readback checks and the deterministic date resolver.

Explain why Claude is an adapter, not business logic. The SDK streams content blocks and message usage events; TTFT ends at first text rather than a role-only chunk. Disable SDK retries, retry only transient failures before text within the total deadline, and never retry quota/billing/auth errors or replay partial speech. On uncertainty retain the per-attempt budget reservation instead of claiming zero cost. Distinguish exact reported usage from conservative byte-based admission and estimated INR.

Names stay local; prompts omit full call records and redact recognized identifiers. Raw console text is synthetic diagnostic data, not speech or ordinary logs. Mocks/HTTP transports prove mechanics without credit; they do not prove Claude quality, provider latency or a complete voice experience. Native scoring and voice approval remain deferred. Read [the Claude contract](25-claude-llm.md) and [the prerequisite audit](roadmaps/sections-01-10-review.md).


## Level 2 section 12 explanation

Trace one unsafe sentence through deterministic matching, its approved replacement and a structured event. Explain why another LLM is not the safety authority. A negated fee clause cannot authorize an affirmative salary claim; an unfamiliar certificate issuer is unverified too. Preserve legitimate course facts, approved no-cost EMI wording and the Weltec certificate policy.

Show rule/policy identity, call/turn foreign keys, detection time and measured check latency. No raw transcript is needed to explain which rule fired. The existing schema maps these fields through columns and allowlisted metadata; event-ID replay is idempotent, and the turn row may contain no transcript. Short database work finishes before speech resumes. If audit storage fails, use a safe fallback and report the failure honestly; never claim an outage event was durably stored.

Defend bounded caller/slot validation, Unicode combining marks, code-owned enums and injection canaries as layers rather than a proof against every prompt attack. Demonstrate the three authenticated safety read endpoints and explain that statistics count rule activations. Mocks and disposable PostgreSQL prove software behavior; they cannot pass native-speaker quality or live voice gates.

## Level 3 section 13 explanation

**Why does checking availability before insert fail?** Both callers can read “free.” Recheck under a short slot lock, and retain the partial unique index for active `booked`/`confirmed` appointments as the final authority. The 100-request HTTP test must show one 201 and 99 conflicts.

**Why not lock while the caller thinks?** That consumes a connection and blocks other work. Redis owns a 90-second advisory hold with a token and TTL; PostgreSQL only locks during confirmation. Expired holds never override SQL truth.

**When is “confirmed” truthful?** After explicit caller affirmation and a successful commit. The controller clears tentative lock/win while awaiting SQL; interruption/failure cannot fabricate success. Same-SID restore reads appointments before trusting Redis.

**Why test cancellation and retries?** A stale cancel must not free a successor's slot. Cancellation changes both records atomically; stable booking IDs make retries return the same active appointment. Terminal records require a new identity. Compare pessimistic locking with optimistic insert/conflict handling and explain why the working slot-lock/index combination was retained. See [policy and tests](levels/level-03.md).

## Level 4 section 14 explanation

**Why convert 8 kHz mu-law to 16 kHz PCM?** Mu-law is compressed 8-bit sample coding; a model's PCM/rate contract is different. Upsampling changes the sample clock, not telephone bandwidth. Twenty milliseconds means 160 carrier bytes or 640 bytes of mono PCM16 at 16 kHz.

**Why not drop every second sample or restart the resampler per chunk?** Unfiltered decimation aliases high frequencies into speech; restarting loses filter history/phase and introduces seams. Show the anti-alias test, arbitrary-chunk equivalence and delayed lossy round trip.

**What does the jitter buffer prove?** A fixed, bounded queue can order normalized frames before decode, reject duplicates/late frames and expose gaps. A timer must release the last frame even when no later packet arrives. A gap is explicit; fabricated PLC and adaptive delay are still gated.

**Why are TCP and UDP tests different?** TCP preserves application order through retransmission, so delay/backpressure is the normal issue. UDP-style missing/reordered/duplicate media needs explicit simulation and later real VAD/ASR measurement. Twilio global sequence includes controls; count media chunks separately. Ordering complete mu-law bytes does not itself break PCM byte alignment. See [the lab and evidence](levels/level-04.md).

## Level 4 section 15 explanation

**Why is VAD not endpointing?** Silero estimates speech probability per 32 ms frame; our code decides onset, continuation and when enough silence ends the turn. Hysteresis protects quiet tails, pre-roll protects onset, and hangover retains final sounds. A 64 ms “haan” must not be rejected by an arbitrary minimum sentence length.

**Why not use network time as silence?** Missing audio is unknown speech, not evidence that the caller stopped. Split/reset on a discontinuity, retain valid speech and never manufacture a silence timer from a stalled socket. The bounded service reframes 20 ms transport blocks into Silero's 512-sample input.

## Level 4 section 16 explanation

**Why isn't an HF token a cloud model?** It authenticates a running endpoint or model access. A Hub repository may have no hosted provider; downloading weights runs inference on our machine. We supply cache-only local adapters and an explicit custom-endpoint lab option without claiming a free service or zero remote RAM.

**What do CTC and RNNT decode?** Acoustic features encode the waveform. CTC collapses repeated/blank token alignments; RNNT also conditions emission on previous tokens. The model owns features/decoding; `STTProvider` returns normalized text. IndicConformer supports Gujarati/Hindi, while Whisper is an explicit English/comparison baseline. Neither a protocol test nor transcript normalization proves recognition accuracy.

**Why hold the worker permit after timeout?** Cancelling an await cannot stop native inference. Releasing admission early lets a second request overlap the same model. Retain the slot until actual completion; later serving/process isolation is a separate level.

## Level 4 section 17 explanation

**Why not switch on one English word?** SEO/Google Ads are normal borrowed terms. Combine script ratios, Latin vocabulary and recent informative turns; use hysteresis and abstain on ambiguity. Persist only the route and five labels with the conversation checkpoint so reconnects do not reset language context. This remains a tested heuristic with native/ASR accuracy pending. Caller language is metadata; live replies stay Hindi-base Hinglish. See [the implementation and evidence](levels/level-04.md#sections-1517-design-and-acceptance).

## Level 4 section 18 explanation

**Why can WER exceed 100%?** Insertions count too. Align reference and hypothesis deterministically, sum substitutions/deletions/insertions, then divide by reference words. Aggregate corpus edits and reference lengths; averaging percentages lets a two-word clip outweigh a long one. Empty references have no defined nonempty-hypothesis rate.

**Why keep CER as well?** Indic spelling/spacing errors can make word scores harsh. NFC preserves combining marks; this lab counts Unicode code points without whitespace, not grapheme clusters. Save the normalization version, reference, hypothesis and audio hash so a score can be reproduced.

**Why isn't request RTF model speed?** Hosted upload, queueing and transfer contribute to elapsed time. Record request time/audio duration explicitly; isolated inference RTF stays unknown when the service does not expose it. Error rows have no fabricated recognition score.

**Why save failures and provenance?** Averages hide missing English spans and language-specific errors. Read/replay saved public failures, group clean/noise/telephone separately and label stitched code-mix as synthetic. Only after inference finishes, save aggregate model/run/results in one short transaction; an immutable run key makes retries safe. See [Level 4](levels/level-04.md).

## Level 5 section 19 explanation

**Why two TTS models?** FastPitch predicts acoustic features; HiFi-GAN generates waveform samples. Both checkpoints, their configs and speaker identity belong to the artifact manifest. Resample waveform before mu-law encoding; splitting compressed bytes is not resampling.

**Why isn't a text-only cache key enough?** The same words in another language, voice or model version produce different audio. Include safe normalized text and those identities, plus normalization/PCM format. Bound memory and combine simultaneous misses; retain no failed output.

**Why check safety on cache hits?** Cached bytes cannot grant permission to speak retired or unsafe wording. Check raw text before number spelling can hide amount signals, then check the actual normalized line, audit and only then resolve its audio identity. Cache latency and synthesis RTF are different measurements. Native pronunciation and complete voice approval remain separate from waveform/cache tests. See [Level 5](levels/level-05.md).


## Level 6 section 20 explanation

The prototype is half duplex: close the microphone before controller/Claude/TTS/playback, so the speaker cannot become the next caller turn. Silero and endpointing finalize bounded audio; the saved language state selects an explicit local ASR route, then the existing state machine/booking and final safety remain authoritative. `VoicePrototype` saves a receipt and ready turn in short database operations, then plays validated PCM and records completion/timings. No transaction stays open during inference or audio.

An HF token solves identity, not unlimited inference capacity. Hosted ASR is a useful separate lab, but it would add a second cloud call and cannot prove the all-local Level 6 gate. Four synthetic language paths and five actual local question renders prove integration mechanics. The later physical English/mock-LLM turn was heard clearly, and the approved local Whisper base run completed all 15 cases but failed multilingual quality. Claude, representative multilingual ASR and native/full booking review remain required. Discovery/booking pronunciation uses exact current controller wording and validated slots, never a general translation that could invent a booking.
