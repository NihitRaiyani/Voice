# Verification and acceptance evidence

Current sections 47–53 evidence is recorded in [analytics, evaluation and packaging](#sections-4753-analytics-evaluation-security-and-packaging--2026-10-27). Earlier governed-retrieval and voice increments remain dated evidence; native/representative/production gates stay open.

Historical section 24 verification: **3,078 passed / 22 existing warnings** in each final offline suite. [Redis state/recovery evidence](#section-24-redis-state-and-milestone-recovery) records exact checks and limits.

Historical sections 22–23 verification (`f9d1785`): **2,994 passed / 22 existing warnings** in each final offline suite. [Streaming/interruption evidence](#sections-2223-streaming-and-interruption) records the current isolated checks and remaining gates.

Historical section 21 verification: **2,953 passed / 22 existing warnings** in each final offline suite. [Structured logging evidence](#section-21-structured-logging) records the current checks. Historical ASR/consent repair verification passed **2,935 tests** per suite. [ASR/consent/live delivery evidence](#asr-padding-and-deterministic-consent-repair--2026-10-22) records the exact isolated commands and distinguishes real microphone/SQL proof from remaining native/latency approval.

Current source/provider policy changed on 2026-10-20: [Claude roadmap](roadmaps/README.md) and Claude Haiku 5.5 replace OpenAI. Dated checks, model names, counts and former source hashes below remain historical evidence; they do not prove Claude quality/cost/latency. No OpenAI requests or fallback are permitted. Native/representative and complete voice gates remain pending.


Historical local Phase 2–4 verification: **2,920 tests pass in both `make ci` and `make test`, with 22 existing warnings**. These offline checks do not imply hosted CI, native review or any passed gate. See the [combined delivery report](#combined-phase-24-delivery) for per-item evidence and limitations.

Historical complete-suite verification after the [Level 2 section 10 increment](levels/level-02.md): **1,674 tests passed, 23 warnings, no failures or skips**. The new `make ci` skeleton starts native local services, runs Ruff, runs mypy on the Level 1 contract surface, applies Alembic to the local database, passes **38 provider/config tests** and passes **11 mocked API/architecture/migration-settings tests**. Pre-commit hooks pass across all files.

The earlier [Level 0 baseline report](roadmaps/level-00-baseline.md) remains historical evidence of 1,571 passing tests and five lint findings before this increment. Its test/identity record is not rewritten as current implementation evidence. L1 records capacity/pressure/timeout/cancellation, relational constraint/migration/legacy-compatibility, separate-seed workflow results and the section 9 workflow skeleton. Level 2 section 10 records the software-owned state-machine facade and durable latest-checkpoint adapter. Hosted GitHub Actions passed on 2026-10-05 for commit `ac99270`; Level 0 clean-machine evidence remains separate.

Tests needed explicit tokenizer data and permission for disposable local database shared memory. Passing after those setup corrections does not prove clean-machine reproduction, local-model quality, live latency or production readiness. Level 0 remains pending prerequisite review, reproducible setup and representative P50/P95 timing.

## Commands and environment

```bash
make ci
make test
uv run --no-sync python scripts/run_eval.py
```

Use the locked telephony/dev/workers environment, NLTK sentence data and disposable PostgreSQL/Redis prerequisites in the [runbook](runbook.md). Report skips/errors explicitly. Existing async tests use `asyncio.run`; dial tests pin the clock/window. No paid AI or carrier calls occur by default.

Section 4 metadata contracts passed 116 tests; focused relational/migration/repository/booking checks passed 46 tests. The complete suite includes these and Alembic schema parity; working-database migration and later restore/benchmark/cleanup features are not implied by those results. See the [relational catalog](20-relational-schema.md).

Section 5 adds 18 tests for database-only migration settings, ordered/offline schema/index review, database-only upgrade/check/rollback/re-upgrade and separate atomic/repeatable/concurrent seed behavior, preservation, head/environment guards and CLI privacy/opt-in. The final full suite passed in 62.67 seconds after new module-owned PostgreSQL fixtures were shortened to module scope; session-long extra servers had exhausted macOS shared-memory IDs in the initial run (1,647 passed, ten setup errors). No application or PostgreSQL server setting was changed to resolve that test-resource issue. All three revision files and retained v4 source bytes remain unchanged. An end-to-end demo CLI run on fresh disposable PostgreSQL inserted seven rows, then zero on repeat; reference rows survived downgrade/re-upgrade and a third invocation inserted zero. Documentation verification covered 60 non-archive Markdown files and 262 local links with zero issues; all 25 changed Python files pass lint. See the [migration guide](21-database-migrations.md).

Section 6 adds an architecture dependency test that prevents `roma/domain/**`
from importing API, provider, repository, realtime, worker or SDK modules. It
also verifies the domain conversation state facade and spend ledger privacy
behavior after moving shared private-file helpers into `roma.core`. The
configured `roma` database was upgraded to `20261004_0003 (head)` and `alembic
check` reports no metadata drift; no seed data was inserted.

Section 7 adds provider contracts and mocks for STT, LLM, TTS, embeddings and
telephony. Tests verify config defaults, provider factories, async streaming
chunks, deterministic mock audio/embeddings, mock telephony and fast failure for
future local adapters. No paid provider call is made by the provider-contract
tests.

Section 8 adds bearer-protected `/api/v1` JSON resources for durable calls,
leads, appointments, analytics and safety events. Tests verify the shared
success/error envelope, pagination/filter/sort metadata, Pydantic validation,
OpenAPI path exposure, appointment conflict code mapping and production database
session-factory wiring while preserving the legacy `/api/call` behavior.

Section 9 adds the Level 1 workflow skeleton: `Makefile`, native
`./scripts/dev_services.sh`, mypy config, local pre-commit hooks and GitHub
Actions. `make app-start` starts native PostgreSQL/Redis, upgrades the schema
and boots the FastAPI app; a smoke run returned `/health` successfully. The CI
slice is intentionally bounded to mocked/unit checks plus empty-database
migration because the full disposable-integration suite creates its own database
clusters. The full suite remains `make test` and passed separately after local
services were stopped.

Section 10 adds the Level 2 state-machine facade and durable latest-checkpoint adapter. Focused verification covers the public stage API, transition/objection/interruption/missing-information helpers, `route_intent()` classification, PostgreSQL latest-checkpoint revision increments and compatible `PostgresConversationStateStore` save/restore by provider call ID. This does not pass OpenAI provider integration, safety-audit, redial/session or L7 recovery gates.

## Evidence rules

Each level records revision, environment/data/model/prompt/document identity, reproducible command, actual outcome, demo/review and limitations. Distinguish source presence, offline verification, real local inference, authorized live verification and full gate pass. Reused foundations close individual checks, not whole levels automatically.

| Gate | Required evidence |
|---|---|
| L1 | Empty DB migrations, native startup, provider contracts/mocks, versioned API, reviewed pool budget and green CI |
| L2/L3 | Durable text restore, Claude streaming/cache-accounting evidence and safety audit, committed live booking, 100-attempt race and cancellation/idempotency |
| L4–6 | Approved language/noise WER/CER/RTF, TTS intelligibility/first audio and complete local voice lab |
| L7 | Barge-in/double cancellation, late results/disconnect, Redis/provider loss, bounded pressure and event-loop lag |
| L8 | Roles/token lifecycle, forged/stale/duplicate callback policy, job crash/replay/retention and PII/access approvals |
| L9 | Eligible scoped/versioned evidence, local embeddings, golden Recall@K/MRR, grounding/deferral and final safety |
| L10 | Authorized local-model carrier call, serving/readiness/GPU admission, retrieval cancellation and Claude-only external GenAI traffic |
| L11/L12 | Real telemetry/reconciled analytics, mock versus GPU capacity, chaos/security/golden regressions and measured SLOs |
| L13 | Clean-host startup/migrations/readiness, backup/restore and compatible rollback |

## Benchmark and documentation checks

Record sample count, hardware/concurrency, codec/noise/language mix, approved dataset, policy/model/document versions and warm/cold state. Measure user-stop-to-first-audio directly and report relevant P50/P95/P99, failures/fallbacks/queue wait/resource saturation. Do not add stage percentiles or promise roadmap example SLOs.

Documentation maintenance checks links/fences, stale carrier/package/roadmap references, level/status consistency, scoped runtime/config changes, preserved prompt/audio assets and original Word-file identity. Inspect the diff and preserve pre-existing user edits. Offline evidence distinguishes the historical baseline from each requested implementation increment; documentation edits do not require paid/live checks.

## OpenAI roadmap review and section 11

The owner selected the OpenAI source on 2026-10-12. [The prerequisite audit](roadmaps/sections-01-10-review.md) records fresh sections 1–10 checks before new code, including read-only configured-DB schema parity. [Level 2](levels/level-02.md#section-11-implementation-and-evidence) records the final 1,716-test offline result and OpenAI SDK HTTP mock transport/console/lab evidence. Model TTFT/cost/native quality are unmeasured with exhausted quota; mock metrics are mechanics evidence only. Full voice and native approval remain deferred. No working DB migration/seed, local LLM installation or paid call ran.


## Section 12 deterministic safety

[Level 2 section 12 evidence](levels/level-02.md#section-12-implementation-and-evidence) records the final 1,790-test offline run, 31-file type check, lint/hooks and the expanded bounded CI slice. Safety tests cover English/Hindi/Gujarati claims and caller questions, direct deflections, injection/poisoned inputs, storage failure/timeout/cancellation, per-turn pre-TTS auditing and native disposable PostgreSQL round-trips/query semantics. Runtime audit omits raw text and never claims a failed database write committed. Native scoring, paid measurements and complete voice approval remain deferred. No working DB mutation, paid call or model download was needed.

## Section 13 transactional booking

[Level 3 evidence](levels/level-03.md#acceptance-gate-and-evidence) retains the original **1,825-test** section verification and adds a 2026-10-21 complete offline conversation proof. Native PostgreSQL tests exercise a 100-request HTTP race (one 201, 99 conflicts), concurrent different-slot attempts for one call, cancellation/reuse, stable retries, commit rollback and index enforcement. Native Redis verifies atomic ownership/TTL. The added `TextConversationService` path selects and reads back a disposable demo slot, commits exactly one appointment after explicit affirmation, restores SQL truth and prevents a second caller from taking it while re-offering the remaining slot. No paid calls, microphone, schema mutation or configured-database seed is part of verification. Real approved slots, teacher demonstration and complete voice approval remain separate.

## Section 14 digital audio

[Level 4 section 14 evidence](levels/level-04.md#section-14-acceptance-and-reference-evidence) records **1,859 offline tests**, the **56 + 287** CI selection, Ruff and 44-file mypy checks. Codec/reference vectors, anti-alias/image rejection, streaming frame/phase/tail behavior, fixed 40/50/60 ms jitter cases, live pre-decode ordering and clean energy-based VAD consumption are covered. The reproducible virtual-clock impairment report is not neural ASR quality, actual carrier loss or end-to-end voice latency. The complete Level 4 and advanced adaptive/PLC/real UDP-ASR gates stay pending. Production retains native serialization and its current sample rates; no working-DB/secret/fixed-asset change is part of this increment.

## Sections 15–17 speech input

The [Level 4 speech evidence](levels/level-04.md#verification-boundary) separates deterministic endpointing/router/SDK-contract tests from real ASR quality. Existing bundled Silero silence/reset runs locally; real IndicConformer/Whisper inference and hosted endpoint access do not run in CI. `make speech-lab` uses synthetic data without `.env`/downloads. Tests cover worker busy admission through cancellation, HTTP error/privacy limits, normalized text, 20→32 ms framing and accepted-turn/checkpoint integration. The later [implemented-boundary audit and hosted smoke](levels/level-04.md#implemented-boundary-audit-and-hosted-smoke) records actual public FLEURS Gujarati/Hindi IndicConformer and English Whisper inference without local ASR downloads. Its three clean read clips and reported timings do not establish representative accuracy/RTF, noisy endpoint or complete voice approval. Audit regressions cover specific safety/input/auth/storage/audio/STT failures and do not imply exhaustive bug absence.

Implemented-boundary audit evidence before sections 18–19, on 2026-10-19: **2,412 offline tests**, **56 + 840** bounded CI tests, Ruff and **54-file mypy** pass; **65 Markdown files / 355 local links** pass. The explicit public smoke also passes the actual configured adapters in all three languages and the bundled-Silero-to-hosted-ASR Gujarati path. [Level 4](levels/level-04.md#integrated-audit-verification--2026-10-19) retains exact timings, scope and remaining quality gates.

## Sections 18–19 verification — 2026-10-19

Fresh final checks: **2,574 offline tests passed** (23 existing dependency warnings, 84.59 seconds); `make test-ci` passed **56 provider/config + 1,002 bounded tests**. Ruff and **62-file mypy** pass; all pre-commit hooks pass. Local Markdown links/anchors pass. Native PostgreSQL test fixtures release the new benchmark cluster at module end; the suite passes without changing application or working-server configuration. Model/runtime installation and real inference are separate explicitly authorized checks, never default CI work.

[Level 4](levels/level-04.md#section-18-asr-benchmark) owns the small public ASR corpus, scores, missing English spans, saved/read failure WAVs and 12 explicit Whisper service errors. Indic/Whisper evidence was persisted to existing `roma` benchmark tables: two runs and 102 metric rows. No schema/migration changes occurred. At that hosted checkpoint, native/representative quality and a matched comparison were pending; the later local comparison below closes the supported-model execution comparison while preserving the native/code-mix gate.

[Level 5](levels/level-05.md#actual-evidence-and-acceptance-gate) owns the approved local runtime/weights, real 18/18 case report, nine misses/nine hits, waveform identity and timing table. Its benchmark adds 18 samples / 81 metric rows; repeat import returns the same run ID without duplicates. Actual defaults and lab overrides are explicit. Unsupported lexical input and invisible formatting controls cannot produce cached corrupted speech; partial failures retain attempted model provenance. Native pronunciation/approved fallback rendering and later complete voice approval remain separate gates.

The selected Word roadmap hash remains `89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`. Runtime models/WAVs, secrets and private caller data are excluded from Git. Production carrier/speech, OpenAI and Hindi-base Hinglish policy remain intact.


## Scoped Whisper and safety pronunciation follow-up — 2026-10-19

Fresh verification: **2,635 offline tests passed** (23 existing dependency warnings, 85.17 seconds), **56 provider/config + 1,063 CI-selected tests**, Ruff, **63-file mypy**, and all pre-commit hooks pass. Markdown validation checks **66 non-archive files / 390 local links and anchors**, with zero issues. The old native-alphabet rejection regression now verifies supported approved fallback speech and continued rejection of ordinary unsupported Roman text. No working-database/schema changes or paid API calls were made.

[Level 5](levels/level-05.md#approved-safety-pronunciation-follow-up--2026-10-19) records actual local **24/24** approved-line cases, six verified PCM16 WAVs, six misses/stores and 18 audited cache hits. The installed runtime/weights were reused offline. Hash-bound canonical/rendered identities, exact certificate-only adjudication, audit outage/cancellation and cache invalidation are covered. The lab explicitly uses synthetic in-memory audit; native listening and live voice approval remain pending.

[Level 4](levels/level-04.md#hosted-whisper-failure-diagnosis--2026-10-19) preserves the unsuccessful alternate endpoint comparison (**2/15**) and original historical failures. Detailed official service queues reported exhausted ZeroGPU quota on both endpoints. The corrected actual adapter check returns fixed `asr-hosted-quota-exhausted` for one public Gujarati clip; it does not resubmit or disclose remote bodies/tokens. Successful transcript handling is covered offline, but a complete real comparison is still blocked by hosted capacity. No quality scores are invented for failed requests.

The selected Word roadmap retains SHA-256 `89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`. Production carrier/speech, OpenAI policy, canonical lexicon, fixed assets and configured database remain unchanged.


## Official Whisper authentication and section 20 — 2026-10-19

Fresh final verification: **2,673 offline tests passed** (23 existing dependency warnings, 84.88 seconds), **56 provider/config + 1,101 CI-selected tests**, Ruff and **69-file mypy**. All commit hooks pass. Documentation links/anchors pass; the selected Word roadmap retains SHA-256 `89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`. No schema migration, working-database seed, paid OpenAI call or new ASR model download ran.

The user-authorized official Whisper edge authentication succeeded with the existing `.env` key. [Actual same-corpus evidence](../benchmarks/asr/section18/whisper-authenticated-report.json) records **7 successful / 8 quota-blocked cases**. The account's authenticated ZeroGPU allowance exhausted, and a separate official router probe returned unavailable credits. No capacity was purchased or deployed. Future runs stop at the first quota rejection and retain a resumable checkpoint; token presence is not unlimited inference capacity. Only the explicit official-host option receives the edge header, never community/arbitrary endpoints. Secret values remain excluded from tracked files/reports.

[Level 6](levels/level-06.md) records the default four-path synthetic input/controller/safety/PCM/fake-speaker lab, five actual local discovery-question renders and one actual synthetic booking pronunciation. Disposable PostgreSQL tests verify short receipt/turn/event writes, privacy opt-in, idempotency and safety-anchor correlation; hardware fakes verify overflow/underflow and cancellation cleanup. LLM streams close promptly on fallback/cancellation. Startup validates local-only ASR, cached model readiness, TTS and bundled Silero before capture; an actual missing-prerequisite run refuses before microphone, paid API or lab-call writes.

At that initial section 20 checkpoint, the physical microphone/headphone conversation and local ASR prerequisites were unavailable. The dated Whisper, Claude and IndicConformer follow-ups below supersede those availability facts while preserving the still-pending native/representative gate. Hosted ASR cannot satisfy the current Claude-only-cloud prototype target. Production provider selections, canonical safety wording, fixed assets and migrations remain unchanged. Section 20 integration code is implemented; its complete Level 6 gate is **not passed**.


## Local Whisper base and quota-free microphone verification — 2026-10-19

The 2026-10-19 decision approved pinned multilingual Whisper base: four artifacts total **147,882,941 bytes**. Its separate **15/15 completed** benchmark failed Gujarati/Hindi/stitched quality. On 2026-10-21 the owner additionally approved pinned IndicConformer revision `2a77d305038dfc502fd9464e7d028b90381976cb`: **404 files / 2,556,502,929 bytes** (2.557 GB / 2.381 GiB), plus 645,087,232 bytes (645 MB / 615 MiB) of optional runtime package directories on this Mac. Exact offline Gujarati and Hindi smoke inference passed. [Level 4](levels/level-04.md#local-indicconformer-readiness--2026-10-21) owns the installation evidence. The later same-corpus local comparison below completes the 15-case accounting; native/representative approval and a production switch remain pending. No paid HF compute or migration is involved.

The combined 2026-10-21 booking/Indic follow-up passed **114 focused tests** and **2,757 full offline tests** with 22 dependency deprecation warnings in 92.10 seconds. The dependency lock resolves **155 packages**; all pre-commit hooks pass. Markdown validation checked **69 files / 462 local links and anchors**, with zero issues. Gujarati and Hindi cache-only smokes each returned zero provider errors. No Claude, OpenAI, hosted ASR or microphone request ran.

The owner then deferred all OpenAI requests because quota is exhausted. Explicit local `--llm-provider mock` bypasses OpenAI settings/client construction and paid opt-in while keeping real microphone, ASR, TTS and short durable lab writes. The first physical capture exposed a finite-pronunciation gap; the two existing approved re-offer variants now render with normal safety checks. Actual local synthesis passed both variants. [Level 6](levels/level-06.md#local-base-microphone-follow-up--2026-10-19) records the failed attempt, silent attempt and repaired successful physical turn. The owner confirmed clear playback. SQL readback confirms completed/played status with transcript retention disabled; no microphone recording/HF upload or OpenAI generation occurred. Real OpenAI conversation, multilingual ASR quality, booking/reference-data setup and native gates remain pending.

Fresh verification for this follow-up: **2,686 offline tests passed** (22 dependency deprecation warnings, 87.35 seconds), Ruff and **70-file mypy** pass. The locked optional runtime resolves successfully; new artifact/routing tests are included in the bounded CI target. **1,114 CI-selected tests** passed (four dependency warnings, 23.97 seconds); all pre-commit hooks passed. Markdown validation checked **67 non-archive files / 416 local links and anchors**, with zero issues.

## Claude alignment verification — 2026-10-20

The unchanged selected Word hash is now `67a79ba87fbec07051d460fd4ec9d5520f7047c0e242da72a65bfdd09ede5f48`; former hashes above identify historical checkpoints. Fresh adapter/config/stream/cache/retry/budget/privacy and CLI checks must use mocks by default. Separately authorized synthetic Claude checks measure provider access/usage/TTFT only; retain native scores as pending. Existing ASR/TTS reports, physical mock-LLM evidence, migrations and schema are not rewritten by the provider change. [The sections 1–20 audit](roadmaps/sections-01-10-review.md#claude-source-review-through-section-20--2026-10-20) owns compliance qualifications; final executed check counts are recorded only after actual runs.


## Claude migration through section 20 — 2026-10-20

The unchanged selected source has SHA-256 `67a79ba87fbec07051d460fd4ec9d5520f7047c0e242da72a65bfdd09ede5f48`. The [sections 1–20 audit](roadmaps/sections-01-10-review.md#claude-source-review-through-section-20--2026-10-20) distinguishes existing foundations, this provider replacement and pending human/ASR/reference-data gates. There are no schema migrations, working-DB seeds, new speech weights, real telephone calls or OpenAI requests in this increment.

Fresh full verification: **2,752 offline tests passed**, 22 existing dependency warnings, 89.71 seconds. Ruff and **74-file mypy** pass; the lock resolves 124 packages, including Anthropic 0.125.0. All paid checks are separate synthetic runs. [Recorded evidence](../benchmarks/llm/claude/README.md) covers model access, final 40/40 nonempty text replies/known usage, median TTFT 1.239 s, schema transformation, actual 807-token cache creation/read, failed adaptive empty-output cases and safe local TTS rendering. Native scores and full multilingual microphone/booking approval remain pending.

The current bounded CI target passed **1,147 tests** (four dependency warnings, 26.22 seconds), including the new provider bridge and replay checks.

All pre-commit hooks passed after the final evidence update. Markdown validation checked **69 non-archive files / 452 local links and anchors**, with zero issues. The final default-configuration structured extraction also succeeded in one attempt (1.654 s) with thinking disabled and an actual 806-token cache creation; the separate readiness artifact preserves the cache-read demonstration.


## Real English microphone and scoped repairs — 2026-10-20

[Level 6 evidence](levels/level-06.md#real-claude-english-microphone-follow-up--2026-10-20) records a real three-turn `roma` hardware/Claude-extraction check and a repaired four-turn prepared-state demo with actual Claude wording, local Whisper base, installed TTS/cache and headphones. Both received explicit clear-playback confirmation. Demo references existed only in a disposable PostgreSQL instance; it was removed after readback. No microphone demo appointment was committed. The later 2026-10-21 offline conversation test proves the booking controller/commit/restore/conflict path separately, without microphone or paid inference. Full transcripts/audio were not exported, and no new models, OpenAI requests or working-DB migrations/reference seeds occurred.

The native checkpoint regression failed before the nested mutable-copy fix and passed afterward, including language/history and continued slot/objection/offer/fact mutation with a second save/restore. The actual working call restores English/DISCOVER with expected filled slots. Mock regressions first reproduced unsupported-script and truncated-word failures, then passed with voice-only audited fallback and token-limit rejection. A bounded synthetic Claude/local-TTS check verified complete native-script speech before the repaired physical demo. Historical failed/limited attempts are retained separately rather than counted as gate passes.

Final full offline verification: **2,755 passed**, **22 existing dependency warnings**, **88.06 seconds**. Ruff, **74-file mypy** and all pre-commit hooks pass. Documentation links/anchors check **69 non-archive Markdown files / 460 local links**, zero issues. An earlier full run during concurrent physical-lab work had two disposable-PostgreSQL setup errors (2,750 passed); both affected tests passed separately, and the final sequential full run passed all tests. No application workaround or migration was added for that setup failure.

The owner deferred further live testing due to limits after confirming repaired speech was clear and understandable. Hindi/Gujarati/code-mix hardware paths, formal native scores, representative ASR quality and complete booking acceptance remain pending. No further paid/microphone calls ran after the deferral. Complete acceptance through section 20 is not claimed.

## Local IndicConformer matched comparison — 2026-10-21

The [Level 4 comparison](levels/level-04.md#local-indicconformer-versus-whisper-base--2026-10-21) records the exact same 15 public cases with pinned local IndicConformer and a fresh local base run. Indic accounts for 12 supported successes and three unsupported English cases with null WER/CER; base completes 15/15. The 12 shared cases score **33.33% WER / 23.94% CER** for Indic and **100% / 96.91%** for base. Shared-warm median latency is **1.020 s / 0.961 s** on 11 paired cases; process-first load/inference is **16.618 s / 1.579 s**. Results exclude Claude/microphone/TTS latency and are not representative p95 or native voice acceptance.

Nine verified public failure WAV/review pairs are saved under ignored runtime storage; English spans remain absent from stitched Indic hypotheses. The new comparison validates matching audio/reference/normalization and recomputed scores, retains failures separately, and excludes both process-first cases from both warm samples. The SQL importer now handles pinned local Indic provenance and correctly marks local metric revisions as known. Two new immutable benchmark runs store **30 attempted samples / 165 metric rows**; business data, schema and historical reports/rows remain untouched. Paid Claude, microphone and hosted inference remain deferred.

Final checks for this comparison increment: **138 focused tests** and **2,768 full offline tests passed** (22 existing dependency warnings; full suite 89.58 seconds). Ruff, **76-file mypy**, mock-provider pre-commit hooks and the unchanged 155-package dependency lock pass. Markdown validation checks **69 files / 474 local links and anchors**, with zero issues. The comparison was independently recomputed from the two retained reports and all nine exported public failure WAV hashes were rechecked. No model downloads, paid requests or microphone tests ran.

## IndicConformer systematic diagnosis — 2026-10-21

The [Level 4 diagnosis](levels/level-04.md#indicconformer-systematic-diagnosis--2026-10-21) records four clean raw-model CTC/RNNT/SOS A/B probes using only the pinned cache and public audio. All four CTC outputs match the adapter, 12/12 prior local transcripts match their hosted counterparts, and an independent edit-distance implementation matches all 12 scores. Gujarati/Hindi-only pooled quality is **15.43% WER / 3.98% CER**; the higher headline includes deliberately stitched English loss. The selected per-language output vocabulary has no English alphabet letters, and a single base-language hint covers each stitched clip. Full encoded lengths rule out truncating the appended audio in this probe. RNNT/SOS changes do not recover English; no production setting was changed. Known-source-boundary recombination is labeled diagnostic-only. Paid Claude and microphone tests remain deferred.

## Adaptive local code-mix routing — 2026-10-21

Historical experiment, superseded by the owner-selected single-pass policy below. Its report and immutable SQL evidence are preserved; the phrase-splitting implementation and executable CLI selection have been removed.

The explicit mixed-language path now preserves `gu-en`/`hi-en`, detects one bounded phrase gap before ASR and routes Gujarati/Hindi to pinned IndicConformer. Whisper inspects the suffix without a forced language; only a detected, credible English result is accepted, otherwise the suffix remains with Indic. No-gap Indic output receives only exact reviewed domain-loanword normalization. Provider failures still propagate. Focused tests cover direct routes, gap bounds, noisy false positives, Whisper language arbitration, provider failure and exact-token normalization beside Indic punctuation.

The unchanged 15-case public report completed offline with zero provider errors. Gujarati/Hindi-only remains **15.43% WER / 3.98% CER**. Clean/telephone stitched code-mix is **12.50% / 3.50%**, while noisy stitched code-mix is **40.48% / 22.64%**. Mixed median wall time is **2.031 s**, so noisy quality and the proposed 1.2-second latency target fail. The final immutable SQL run `section18-adaptive-local-20261021-v3` stores 15 samples / 90 aggregate rows under three model identities; earlier v1/v2 runs remain historical intermediate measurements. No transcript/reference text, schema or business data changed.

Paid Claude and microphone testing stayed deferred. Production speech, weights and reply policy stayed unchanged. Native intra-sentential/domain audio and human review remain required; see [Level 4](levels/level-04.md#adaptive-local-code-mix-routing--2026-10-21).

## Single-pass routing and section 20 review — 2026-10-21

The requested single-pass local composition replaces the phrase-span cascade without changing model caches, `.env`, production speech or Claude policy. Real public ASR inference completes 15/15 with no network/download/paid/microphone call; the report uses unchanged manifest/audio/reference identities. Gujarati/Hindi is 15.43% WER / 3.98% CER. Stitched mixed is 44.84% / 37.02% and median 1.148 s, compared with the historical cascade median 2.031 s. The English suffixes are omitted; matching emitted loanwords cannot restore them. [Level 4](levels/level-04.md#single-pass-local-routing--2026-10-21) owns the full table, cold loads, failure samples and immutable SQL run/checksum.

The current mock prototype exercises the same router through four played language paths with one selected ASR call each. Five route-specific regressions additionally exercise endpointing, exact loanwords, controller/safety, timing, synthetic synthesis/playback and state roundtrip. These are software integration tests, not native audio evidence. [Level 6](levels/level-06.md#single-pass-integration-follow-up--2026-10-21) and the [source audit](roadmaps/sections-01-10-review.md#single-pass-review-through-section-20--2026-10-21) retain the deferred native/microphone/paid gates.

Fresh verification for the selected single-pass increment: **2,800 full offline tests passed**, with 22 existing dependency deprecation warnings, in 89.54 seconds. Repository Ruff and **76-file mypy** pass. The full suite includes disposable PostgreSQL migrations/constraints/booking/turn-state checks; no paid provider or microphone requests run. Markdown validation checks **86 files / 502 local links and anchors** with zero issues. The six public failure WAV hashes match their report cases. The separate mock report records four played/restored paths with exactly one selected ASR call each.

A separate [local synthetic loanword audio probe](../benchmarks/asr/section18/loanword-synthetic-report.json) completes four Gujarati/Hindi clips with one real Indic decode each, no Whisper and all 7 expected keyword occurrences canonicalized. Raw output and normalized output are retained; the Hindi non-keyword error is preserved. Installed TTS generates the audio; this does not provide native-speaker/slot-accuracy acceptance or recover omitted full English suffixes.

## No-cascade deletion investigation — 2026-10-21

[The fresh root-cause report](../benchmarks/asr/section18/code-mix-root-cause-report.json) records 22 vocabulary scans, two clean raw encoder/CTC probes and 12 existing-base decoding trials. English-tail frames select blank 100% even before the Indic language mask, with mean selected-head blank posterior above 99.94%. Prompt/beam/automatic-language alternatives for cached Whisper base fail both mixed clips (pooled WER 89.29–92.86%) and are not adopted. [Level 4](levels/level-04.md#english-sentence-deletion-root-cause--2026-10-21) owns detailed interpretation and the proposed additional checkpoint evaluation.

No production/app code, `.env`, database, runtime dependency, model weight, paid Claude or microphone changed. Report scores/audio identities and diagnostic vocabulary evidence are rechecked locally. This is diagnosis, not a completed code-mix fix or another full-suite claim. Additional model weights have not been downloaded; the proposed single-multilingual-decoder evaluation awaits owner authorization.

## Approved Turbo replacement verification — 2026-10-21

[Level 4](levels/level-04.md#whisper-turbo-replacement--2026-10-21) records the approved model download, verified 464 MB MLX 4-bit runtime layout, 2.557 GB Indic cache removal and removal of the redundant CPU Turbo trial. The [unchanged 15-case report](../benchmarks/asr/section18/whisper-turbo-local-report.json) completes offline with English 0% WER but Gujarati 52.78%, Hindi 37.78%, Gujarati–English 75.21% and Hindi–English 59.26%; this fails recognition approval. A completed SQL run is not a quality pass. Decoding probes show a fixed token-budget truncation and persistent full-English-suffix loss; unhelpful prompt/timestamp/language settings are rejected.

The [fresh mock composition](../benchmarks/voice/section20-turbo-mock-report.json) records four played/restored paths and one scripted decoder call each. Regression tests cover five route states, immutable full audio/metadata, exact loanwords, missing/corrupt cache, lazy loading, cross-adapter busy admission and timeout/cancellation behavior. Existing database/booking/safety/TTS checks remain applicable. No new paid Claude or microphone test is performed. Native/full microphone gates remain pending.

Fresh replacement verification: **2,816 full offline tests pass** in 88.42 seconds, including disposable PostgreSQL migration/booking/restore/conflict and Redis coordination checks. The same 22 existing Pipecat/dependency deprecation warnings remain. Repository Ruff, **77-file mypy** and offline lockfile consistency pass. Markdown validation checks **86 files / 534 local links and anchors** with zero issues. The four synthetic Turbo loanword cases recover **7/7 expected keyword occurrences** after exact normalization, with no retained audio or native-quality claim. The bundled MLX multilingual tokenizer hash matches the separately pinned tokenizer artifact. Native, paid Claude and microphone acceptance remain deferred.

All repository pre-commit hooks pass (Ruff, mypy and provider contract tests). All 12 exported public failure PCM hashes match their saved report cases. No credentials/runtime weights are tracked.

## Single-model batch verification

[Controlled diagnosis](../benchmarks/asr/section18/turbo-omission-diagnosis.json) rules out a material feature mismatch, simple gain correction, FP32 activations and 4-bit compression as sufficient causes of the suffix loss. A full-precision format of the same approved Turbo model was tested, then removed; the active checkpoint remains unchanged. Cropped VAD and unrestricted language detection are rejected after losing quiet words and selecting Hindi/Urdu scripts incorrectly.

The [versioned batch report](../benchmarks/asr/section18/whisper-turbo-batch-report.json) uses identical references/audio hashes and completes 15/15 offline. All nine pure-language hypotheses/metrics exactly match the prior whole-turn report. Six mixed cases retain English, five suffixes are exact; Gujarati–English WER falls to 40.17%, Hindi–English to 25.19%. Pure Gujarati/Hindi remains 52.78%/37.78%. Mixed median increases to 3.564 seconds; no real-time/native pass is claimed. [Level 4](levels/level-04.md#single-model-speech-batching--2026-10-21) owns detailed scope and SQL identities.

[The four-path prototype mock](../benchmarks/voice/section20-turbo-batch-mock-report.json) preserves safety, timing and state restoration without model/hardware/DB calls. Tests cover exact PCM reconstruction, strongest-pause/batch bounds, scorer reset, context script/English evidence, one batch dispatch and completed-row cache/order handling. The latter two native-array checks run on this Mac with tiny synthetic tensors and no weights; Linux CI skips those optional MLX-only checks. No paid Claude or microphone test runs.

Fresh full offline verification: **2,829 tests pass** in 91.99 seconds, with the same 22 dependency deprecation warnings. Ruff and **79-file mypy** pass. A focused 15-test follow-up confirms the final tested language guard is wired into the decoder. Native Gujarati/Hindi/noisy quality, no-pause switching, p95/first-audible latency and microphone acceptance remain pending.

A subsequent 28-test benchmark-evidence check passes, including rejection of altered batch bounds and preservation of the current report identity. Final adapter smoke checks recover the exact English suffix in both clean mixed clips; peak Metal allocation is 1.512 GB and process RSS is 0.552 GB (these measurements may overlap). Cold startup is excluded from the steady-state benchmark comparison.


## Phase 1 setup, test catalog and safe diagnostics — 2026-10-22

The missing-data subprocess regression fails before the setup guard, then stops once with `TOKENIZER-DATA-MISSING` and the setup command after the guard is added. The repo-local data directory was removed explicitly; the sentence suite refused to run before `make nltk-data` installed only `punkt_tab`. A separate direct-startup regression exposed the installed NLTK path-security requirement: register the explicit data root before opening its parameter files. Startup validation now covers app, prototype and pytest session setup without automatic installation.

CI delegation/catalog regressions fail on duplicated workflow commands and an absent catalog, then pass with one explicit complete offline catalog and reasoned-exclusion validation. Eight fake-seam failures and the CLI privacy check fail on the former generic/string codes, then pass with the shared enum/mapping. A further checkpoint regression reproduces DB-as-LLM misclassification before the narrow storage-boundary fix. Disposable PostgreSQL readback covers every enum code and rejects arbitrary private error strings; ordinary events omit transcript data by default. Readiness-document tests reject stale current availability and missing current-status blocks.

Required `make ci` and `make test` checks use isolated native PostgreSQL/Redis, never the working `roma` database. The scratch verification environment uses dev mode because an existing config test asserts that default; its isolated migration server is closed before the full test fixtures allocate additional clusters on this Mac. Dated benchmark evidence is unchanged. No paid provider, microphone, speech model download, ASR strategy, Claude setting or production-media change is part of this phase. No gate is marked passed. Phases 2–4 require the owner's next “continue”; the Phase 1 commit stays local without a push.


### Phase 1 report

| Item | Failing-before regression | Passing-after evidence |
|---|---|---|
| Tokenizer | `test_missing_tokenizer_fails_fast_before_sentence_tests`; `test_direct_startup_registers_repo_default_without_make_environment` | Four setup regressions and the existing six sentence tests pass; missing-data subprocess exits once with the setup instruction. |
| CI definition | `test_workflow_delegates_to_the_same_ci_test_command`; `test_ci_catalog_accounts_for_every_test_module` | Three catalog/delegation tests pass, including missing-module and empty-exclusion-reason rejection. No modules are excluded. |
| Error categories | All eight `test_each_fake_failure_has_its_own_safe_durable_code` cases; `test_cli_prints_only_a_safe_stage_code`; `test_checkpoint_failure_is_db_not_llm` | Eighteen error/CLI tests pass, plus disposable-DB enum readback and private-error rejection. |
| Readiness docs | Two `test_level_brief_starts_with_current_readiness_without_claiming_a_gate` cases; `test_indic_cache_availability_checklist_is_explicitly_historical`; `test_speech_contract_marks_previous_indic_routing_as_historical` | Four readiness tests pass. All dated benchmark artifacts remain unchanged. |

| Required item checks | `make ci` | `make test` |
|---|---|---|
| 1: setup | 80 provider tests + 1,210 CI tests | 2,833 passed |
| 2: unified catalog | 2,837 passed | 2,837 passed |
| 3: safe errors | 2,856 passed | 2,856 passed |
| 4: final documentation/regression set | 2,860 passed | 2,860 passed |

The final full runs retain 22 existing dependency deprecation warnings; no gate is marked passed. Ruff, configured mypy and all commit hooks pass. Markdown checking covers 86 files and 550 local links/anchors without errors.

Repository findings beyond the brief: a completely missing tokenizer directory could previously fall back silently to home-installed data; the new explicit-root validation prevents that. The installed NLTK protects reads using registered data roots, so direct startup must register the selected root before loading it. Existing config tests assume dev mode, and this Mac cannot retain the extra migration server alongside all disposable test clusters; the verification environment accounts for both without changing application settings. The shared speech contract also contained an undated stale Indic route, now labelled historical. The only production application change is the explicitly requested startup tokenizer guard; production media/provider behavior is unchanged.

The three OpenAI-named modules and their guarded behavior are documented in the runbook; none was deleted or enabled for paid runtime. No Phase 1 owner decision remains outstanding. The request to resume Claude/microphone/native acceptance remains deferred under the four-phase global rules and the stop/continue boundary.

Changed files (24):

- .github/workflows/ci.yml
- Makefile
- README.md
- configs/ci-tests.json
- docs/05-endpointing-vad.md
- docs/12-verification.md
- docs/levels/level-04.md
- docs/levels/level-06.md
- docs/runbook.md
- roma/core/tokenizer.py
- roma/core/voice_errors.py
- roma/main.py
- roma/repositories/postgres/voice_lab.py
- roma/services/text_conversation_service.py
- roma/services/voice_prototype.py
- scripts/run_ci_tests.py
- scripts/voice_prototype.py
- tests/architecture/test_ci_coverage.py
- tests/architecture/test_readiness_docs.py
- tests/conftest.py
- tests/core/test_tokenizer_setup.py
- tests/repositories/postgres/test_benchmarks.py
- tests/services/test_voice_errors.py
- tests/services/test_voice_prototype.py

## Phase 2 turn boundaries — offline verification

Both `make ci` and `make test` passed **2,881 tests, 22 warnings** using the Phase 1 isolated dev-mode service setup, with the migration server stopped before full integration fixtures. Ruff and mypy passed; 86 Markdown files / 554 local links and anchors passed. No model inference, paid call, microphone, working `roma` DB change, push or gate approval occurred.

Failing-before/passing-after regressions in `tests/services/test_turn_boundaries.py`: `test_continuation_grace_merges_800ms_pause_before_any_asr_or_playback`, `test_grace_wait_is_bounded_and_respects_the_session_ceiling`, `test_capture_deadline_flushes_partial_source_audio_before_reset`, `test_empty_asr_at_capture_deadline_has_a_distinct_safe_code`, `test_deadline_preserves_the_hard_30_second_input_cap`, and `test_audio_gap_can_recover_unexpected_english_from_every_saved_route` (the three pure routes failed before structural eligibility). The harness/policy tests first failed for missing scaffolding; all pause cases now retain the second sentence. Low-English evidence, exact no-gap bytes, configurable minimum length and saved-route recovery also pass. Current router-version expectations were updated; historical v2 benchmark fixtures/reports remain immutable.

[Level 6](levels/level-06.md#offline-turn-boundary-repair--phase-2) owns the pause matrix, quantized 320 ms grace cost, simulated 5 ms batch overhead and provisional defaults. No design-changing contradiction was found: an established route uses two informative observations; a fresh router can select its first informative route. Real pure-language batch overhead and quality remain unmeasured. The owner still needs to approve the provisional grace/minimum length after representative evaluation.

## Phase 3 output, timing and disposable booking readiness

Per-item isolated `make ci` / `make test` totals were respectively **2,890 / 2,890** (batch rejection), **2,892 / 2,892** (turn timing), **2,899 / 2,899** (demo lifecycle) and **2,901 / 2,901** (readiness), with 22 existing warnings on every run. Ruff/mypy passed; the new demo module also passed a direct mypy check. Markdown verification passed 86 files / 559 local links before this report update.

Failing-before/passing-after tests: `test_batch_rows_are_validated_before_text_is_joined` (clean/silence/low probability/repetition/partial rejection/all rejection/non-finite fixtures), `test_rejection_policy_is_configurable_and_validated`, `test_all_rejected_batch_rows_leave_controller_unchanged_with_distinct_asr_code`, `test_speech_to_first_write_budget_excludes_full_playback_duration`, `test_turn_budget_has_no_threshold_until_explicitly_configured`, `test_demo_target_refuses_remote_working_or_non_development_databases`, `test_demo_database_native_lifecycle_migrations_and_future_test_only_data`, `test_demo_stop_refuses_unowned_or_symlinked_directory`, `test_booking_readiness_rejects_empty_and_wrong_branch_and_accepts_seeded_db`, and `test_empty_booking_db_stops_cli_before_model_readiness_mic_or_lab_call`. New surfaces first failed for missing implementation; the no-advance and CLI-order tests also reproduced the previous behavior.

The [Level 6 table](levels/level-06.md#offline-output-and-demo-readiness--phase-3) records the fake-clock 849 ms speech-end-to-write run and its limits. Rejection thresholds remain the owner's provisional numeric defaults; budgets remain unset. No design-changing contradiction: the general seed intentionally creates an inactive demo campus, so this phase adds an explicit separate voice demo, preserving the controller's canonical branch name and TEST ONLY codes. No working `roma`, paid API, microphone, model artifact, Claude configuration, production-media path or gate was changed. Owner decisions remaining: validate rejection thresholds on representative recordings and set budgets only after real timing review.


## Phase 4 corpus evaluation scaffolding — offline verification

Per-item isolated `make ci` / `make test` totals: **2,910 / 2,910** (schema), **2,912 / 2,912** (scoring), **2,913 / 2,913** (recording protocol), **2,920 / 2,920** (report plumbing), each with 22 existing warnings. Ruff and the configured mypy surface passed. Markdown verification before this inventory passed 87 files / 572 links and anchors. No model inference, new WER result, recording, fetch, download, paid call or microphone test ran. The section18 manifest and historical reports are unchanged.

Failing-before/passing-after tests in `tests/eval/test_corpus_v2.py`:

- `test_v2_accepts_explicit_metadata_and_exposes_a_schema`
- `test_v2_rejects_duplicate_or_incomplete_recording_metadata` (duplicate ID/audio, missing entity reference/licence/consent, switch and channel cases)
- `test_v2_minimums_are_required_config_and_no_default_counts_are_invented`
- `test_entity_recall_uses_unicode_normalization_and_whole_phrases`
- `test_per_speaker_edit_totals_cannot_hide_one_speakers_failures`
- `test_recording_protocol_covers_annotation_and_owner_approval_before_collection`
- `test_report_adds_v2_breakdowns_and_leaves_v1_format_unchanged`
- `test_report_rejects_incomplete_or_mismatched_recording_identity`
- `test_report_counts_failed_recordings_as_empty_without_hiding_them`
- `test_offline_report_cli_never_reads_audio_or_loads_models`

The new module contains 19 parameterized test cases. Metadata/scoring first failed for missing functions; the protocol failed for its missing page; the CLI rejected the missing `annotate` command. After implementation all pass, including the existing benchmark compatibility tests. The CLI test supplies only fake metadata/text, forbids audio/model/provider seams, preserves input bytes and rejects output-over-input writes. Error rows retain their original error status and count as empty transcripts, so speaker/entity denominators cannot hide failures. This is scoring infrastructure, not a corpus/model acceptance run.

No design-changing repository contradiction was found. Metadata consistency checks cannot establish authentic consent or verify real audio; those remain human prerequisites. The owner's configuration keys are `min_distinct_speakers` and `min_distinct_recordings` in `configs/asr-corpus-v2.json`, both null. Before collection the owner must set those minimums and approve language/channel coverage, independent-source granularity, annotation rules, licence/consent, storage/retention and reviewers. The protocol contains no collection authorization.

## Combined Phase 2–4 delivery

The original Phase 1 baseline is `64ecbd6`. Phase 2 is local commit `2792130`; Phase 3 is local commit `6bfdd46`; the Phase 4 commit hash is supplied in the delivery response. Each phase has one commit. Nothing is pushed, no gate is marked passed, and paid Claude/microphone/native acceptance remain deferred. The working `roma` database, model weights, Claude configuration and production Twilio/Sarvam behavior are unchanged. Temporary test/demo databases were isolated native clusters and removed; the migration-check server was stopped before full suite fixtures.

| Item | `make ci` tests passed | `make test` tests passed |
| --- | ---: | ---: |
| P2 harness | 2861 | 2861 |
| P2 shared policy | 2868 | 2868 |
| P2 continuation grace | 2870 | 2870 |
| P2 capture deadline | 2873 | 2873 |
| P2 routing recovery | 2881 | 2881 |
| P3 batch rejection | 2890 | 2890 |
| P3 turn timing | 2892 | 2892 |
| P3 disposable demo DB | 2899 | 2899 |
| P3 booking readiness | 2901 | 2901 |
| P4 manifest v2 | 2910 | 2910 |
| P4 entity/speaker scoring | 2912 | 2912 |
| P4 recording protocol | 2913 | 2913 |
| P4 report plumbing | 2920 | 2920 |

The earlier phase reports name behavioral regressions and setup corrections. Clarifications: the fresh router accepts its first informative observation; the established route uses two. The general demo seed intentionally creates an inactive campus; the voice demo is separate. Neither clarification required a design change. Native/representative quality, acoustic playback latency and real-model batch overhead remain unmeasured in this work.

The Level 6 pause matrix retains both sentences at 256/384/448/512/640/800 ms, merged with the provisional default grace. Configured 300 ms grace adds 320 ms after frame rounding, plus up to a device-block alignment interval. A qualifying paused pure-language turn adds **5 ms in the fake decoder** (10 ms batch versus 5 ms single); real model overhead is not measured. The fake speech-to-first-write table is endpoint 844 ms, ASR 5 ms, LLM/safety/TTS/playback-start 0 ms, total 849 ms. Zeroes denote instantaneous fake-clock work, not production performance.

Provisional defaults: 512 ms base endpoint, **300 ms continuation grace**, 256 ms batch gap, **2,000 ms minimum batch turn**, 30,000 ms capture cap; batch rejection **0.6 / -1.0 / 2.4**; **TurnBudget unset**; **corpus minimums unset**. Existing English evidence threshold stays 0.8. Owner review remains needed for grace/minimum length, rejection thresholds, real timing budgets and corpus/consent/reviewer decisions before representative collection and later paid/microphone/native acceptance.

### Files changed per phase

Phase 2 (25 files):

- `.env.example`
- `CLAUDE.md`
- `configs/ci-tests.json`
- `docs/12-verification.md`
- `docs/decisions.md`
- `docs/levels/level-04.md`
- `docs/levels/level-06.md`
- `roma/core/speech_settings.py`
- `roma/core/voice_errors.py`
- `roma/domain/audio/endpointing.py`
- `roma/domain/audio/speech_chunks.py`
- `roma/domain/audio/turn_policy.py`
- `roma/eval/benchmark_evidence.py`
- `roma/eval/local_whisper.py`
- `roma/providers/ai/local_routing.py`
- `roma/providers/ai/mlx_batch.py`
- `roma/providers/ai/registry.py`
- `roma/providers/ai/whisper_mlx.py`
- `roma/providers/audio/local_device.py`
- `roma/services/speech_input.py`
- `scripts/voice_prototype.py`
- `tests/providers/ai/test_whisper_mlx.py`
- `tests/services/test_turn_boundaries.py`
- `tests/services/test_voice_prototype.py`
- `tests/support/voice_turns.py`

Phase 3 (32 files):

- `.env.example`
- `Makefile`
- `configs/ci-tests.json`
- `docs/05-endpointing-vad.md`
- `docs/12-verification.md`
- `docs/decisions.md`
- `docs/levels/level-04.md`
- `docs/levels/level-06.md`
- `docs/runbook.md`
- `roma/core/ai_contracts.py`
- `roma/core/speech_settings.py`
- `roma/core/voice_errors.py`
- `roma/domain/audio/endpointing.py`
- `roma/domain/audio/turn_budget.py`
- `roma/providers/ai/local_routing.py`
- `roma/providers/ai/mlx_batch.py`
- `roma/providers/ai/registry.py`
- `roma/providers/ai/stt.py`
- `roma/providers/ai/whisper_mlx.py`
- `roma/providers/audio/local_device.py`
- `roma/repositories/postgres/demo_database.py`
- `roma/repositories/postgres/voice_lab.py`
- `roma/services/speech_input.py`
- `roma/services/text_conversation_service.py`
- `roma/services/voice_prototype.py`
- `scripts/demo_database.py`
- `scripts/voice_prototype.py`
- `tests/providers/ai/test_batch_rejection.py`
- `tests/providers/ai/test_whisper_mlx.py`
- `tests/services/test_demo_database.py`
- `tests/services/test_turn_boundaries.py`
- `tests/support/voice_turns.py`

Phase 4 (16 files):

- `CLAUDE.md`
- `configs/asr-corpus-v2.json`
- `configs/asr-corpus-v2.schema.json`
- `configs/ci-tests.json`
- `docs/12-verification.md`
- `docs/26-asr-recording-protocol.md`
- `docs/README.md`
- `docs/decisions.md`
- `docs/levels/level-04.md`
- `docs/levels/level-06.md`
- `docs/runbook.md`
- `pyproject.toml`
- `roma/eval/asr_benchmark.py`
- `roma/eval/corpus_v2.py`
- `scripts/asr_benchmark.py`
- `tests/eval/test_corpus_v2.py`


## Post-fix 15-case ASR rerun — 2026-10-22

The [Level 4 comparison](levels/level-04.md#structural-routing-and-rejection-rerun--2026-10-22) records 15/15 offline executions on the existing pinned Turbo model and unchanged public manifest. Independent scoring verifies all 30 current/baseline cases and matched audio/reference identities. No rows were rejected; Gujarati-only structural batches regress pooled quality and latency, while the other 12 hypotheses are unchanged. This benchmark result supersedes no historical report and passes no gate. It excludes capture/LLM/TTS/playback, and no paid call, microphone, download, model/config change, SQL import or working `roma` write ran. The focused existing MLX/provider/rejection/evaluation checks pass **65 tests**; Markdown verification passes **87 files / 577 links and anchors**. The full-suite counts above remain the earlier phase evidence; no application code was changed by this evidence-only increment.


## Full large-v3 8-bit versus Turbo paired trial — 2026-10-22

The [Level 4 paired trial](levels/level-04.md#full-large-v3-8-bit-mlx-versus-turbo--2026-10-22) records the owner-approved **1.708 GB** pinned full large-v3 8-bit MLX download and separate offline 15-case runs for it and the existing Turbo 4-bit model. Both finish **15/15 requests, zero execution errors**, with no inference-time network attempts. Independent scoring verifies all **30 cases** and matching PCM/reference/manifest identities; all 15 fresh Turbo hypotheses match the prior structural-v3 run.

Pooled WER/CER improve **35.95% / 16.76% → 27.89% / 11.70%**, while median ASR request increases **3.185 → 6.756 s**. Hindi/Hindi–English regress and full large-v3 rejects one repetitive noisy mixed row, losing its English suffix; rejected content remains represented as deletions in the score. Peak Metal allocations are **1.408 → 2.573 GiB**, process peak RSS **0.466 → 0.688 GiB**, measured separately and never summed. System swap was already high, includes other apps and cannot be attributed solely to a model; no full voice RAM or latency claim is made.

The focused existing MLX/provider/rejection/evaluation checks pass **65 tests**; Markdown verification passes **87 files / 583 local links and anchors**, and `git diff --check` is clean. This evidence-only increment changes no application, dependency, config, test, migration or production path. Turbo remains the configured model and both caches remain installed. No working `roma` access, paid Claude or microphone test ran; no gate passes. Historical evidence remains intact. See the raw reports and hashed comparison linked from Level 4 for every case, runtime/weight pin, timing, row decision and memory observation.


## ASR padding and deterministic consent repair — 2026-10-22

[Level 4](levels/level-04.md#waveform-padding-repair-and-model-retirement--2026-10-22) owns the confirmed bad log-mel padding, unchanged single-call path, 30 independently checked case scores, actual decoder profiling, corrected 15/15 runs per model and authorized full-v3 cache retirement. [Level 6](levels/level-06.md#supervised-claude-and-booking-follow-up--2026-10-22) owns the paid/supervised attempts, owner clarity/relevance observation, real TEST ONLY appointment and SQL restore. All failed attempts remain in transcript-free evidence.

Failing-before/passing-after: `test_batch_features_match_whisper_waveform_padding`; all six parameters of `test_natural_explicit_consent_commits_after_the_readback`; and `test_explicit_readback_confirmation_does_not_depend_on_llm_extraction`. Safeguards additionally cover questions, negations, revisions and missing selected readbacks. The affected controller/text subset passes **162 tests** before final full verification.

| Final check | Result |
| --- | --- |
| `make -o services-up -o db-upgrade ci` | Ruff/mypy clean; full explicit CI catalog **2,935 passed**, 22 existing warnings, 95.49 s |
| `make test` | **2,935 passed**, 22 existing warnings, 93.39 s |
| Independent ASR score checks | **30/30**, identical case/audio/reference identities |
| Paid Claude smoke | Three real successful requests; adapter-reported ₹0.06; model/settings unchanged |
| Supervised microphone | 15 attempts / 13 played; one prior LLM-safe fallback and two no-speech captures retained |
| Actual demo SQL | One confirmed 2026-10-23 15:00 appointment, booked slot and successful authoritative checkpoint restore |

The CI invocation reuses the explicit service/schema preparation: a fresh disposable `roma_demo` was already migrated through all three revisions and seeded TEST ONLY. The native demo PostgreSQL was stopped before full tests to prevent macOS shared-memory exhaustion; the working `roma` URL was never used. The integration catalog still exercises its own empty-database migrations. An initial run used `APP_ENV=test` against an existing test expecting dev and retained one extra native cluster; restoring the documented dev default and stopping only the owned demo cluster resolved the setup failures, without changing tests, system limits or working services. This is local CI evidence; no GitHub Actions run or push is claimed.

The root ASR repair passed both suites at **2,921** before the live follow-up exposed natural-consent handling; final **2,935** includes the additional safety/commit cases. Existing provider timeout, fallback and spend caps remain unchanged. New explicit readback consent avoids a paid extraction call. The voice adapter reports ₹0.31 across the supervised attempts, separate from the ₹0.06 smoke; these are adapter usage records rather than an invoice. No model downloads or cascades ran. Turbo artifacts reverified unchanged; only the approved full-v3 cache was removed. Disposable demo PostgreSQL/Redis were removed after evidence collection. No audio/transcripts, working-DB mutation, `.env` edit, migration change, production-provider change or section 21 implementation occurred. Formal native scoring, representative ASR quality and acceptable end-to-end voice latency remain pending; no gate passes.

Markdown verification after synchronization: **87 files / 605 local links and anchors passed**; `git diff --check` is clean.


## Section 21 structured logging

Test-first coverage in `tests/core/test_structured_logging.py` and `tests/services/test_voice_prototype.py` checks JSON shape/privacy, untrusted metadata rejection, concurrency and nested-context cleanup, HTTP/WebSocket IDs, request exceptions/cancellation, independently handled Uvicorn privacy, two-turn tracing, stage failures and current LLM stage/language. The initial focused run failed to import the absent formatter/context API. Follow-up tests caught missing cancellation metadata and stale pre-controller LLM stages before those were fixed. A Uvicorn fixture initially omitted required synthetic settings; it was corrected without changing application settings.

The focused logging/prototype/error-compatibility run passed **87 tests**. The first full CI run exposed three test-order failures because in-process Alembic disables existing loggers; event-log tests now restore their own logger state, and startup re-enables the owned event logger. A separate failing-before/passing-after subprocess test proves the mock CLI works without a `.env` file or provider credentials: logging now has its own optional settings profile instead of requiring the full provider settings. Final **2026-10-23** verification passed:

- `make -o services-up -o db-upgrade ci`: Ruff clean, mypy clean on 82 source files, **2,953 tests passed / 22 existing dependency deprecation warnings** (97.50 s).
- `make test`: **2,953 passed / the same 22 warnings** (97.36 s); no failures or skips.
- Before CI, `make db-upgrade` applied all existing migrations to a fresh native disposable PostgreSQL database. That server was stopped before the suites; the two Make prerequisites were deliberately bypassed to avoid starting/upgrading the working `roma` services. Integration fixtures created their own disposable databases. No migration file changed.
- `make --silent voice-prototype`: four synthetic paths, **38 valid JSON log records**, four correlated completed turns, no microphone/network/download activity. A subprocess test separately verifies startup from a clean directory without `.env` or provider credentials.
- Markdown check: **87 files / 615 local links and anchors**, zero issues. `git diff --check` is clean.

These are local checks; no GitHub Actions run or paid/live acceptance is claimed. All providers/devices are fake; no paid API, microphone, download, model changes or working `roma` access is involved. This verifies structured operational logging, not native voice readiness or Level 11 telemetry.


## Sections 22–23 streaming and interruption

Test-first queue/streaming coverage initially failed because the new modules did not exist. Focused checks then caught cancellation being mistaken for queue overflow, the response cancelling its long-lived final-transcript consumer, and clear failure skipping resource release. Regression tests now exercise those exact seams, interruption during generation/TTS/playback, delayed cancellation-resistant TTS, next-turn checkpoint/resumption, real VAD frames, `WebSocketDisconnect`, fallback/close pressure, audited cache-only filler, private metric rejection and uncommitted booking-claim suppression. Process-offloaded PCM framing is byte-identical to the retained path; process jobs retain bounded leases after cancellation. New modules are in the single CI catalog and mypy surface.

The initial focused suite passed **160 tests**. Final subagent review reproduced four more cancellation races: late fallback and post-receipt playback, device-close failure bypassing disconnect cleanup, and next-turn admission before old output clear. Four failing-before regression tests now pass; the related final suite passed **92 tests**. Additional failing-before/passing-after cases cover capture overflow closing/clearing the transport and the existing five-minute session cap. The four-call [offline report](../benchmarks/voice/section22-23-offline-report.json) is reproducible via `make --silent streaming-lab` and records first software audio, queue bounds and fake WebSocket heartbeat timing under deliberately slow pure-Python CPU/TTS work. It includes cold process startup. It contains no real-model quality/capacity or native/acoustic acceptance claim. Final verification on **2026-10-23**:

- `make -o services-up -o db-upgrade ci`: Ruff clean, mypy clean on **86 source files**, **2,994 passed / 22 existing warnings**, no failures/skips (103.96 s).
- `make test`: **2,994 passed / the same 22 dependency deprecation warnings**, no failures/skips (99.13 s).
- All existing migrations applied to fresh native disposable PostgreSQL before CI; it was stopped before the suites to avoid macOS shared-memory exhaustion. Integration fixtures used their own disposable databases. The working-service/DB Make prerequisites were bypassed deliberately; no configured `roma` connection or migration occurred.
- Disposable SQL tests verified interrupted turn receipts, an idempotent disconnect event and the existing terminal `failed` call status without a schema change.
- The final four-call synthetic run has **zero queue drops/fallbacks**; queue peaks stay within configured capacities. It includes a 30 ms pure-Python delay per process job, 20 ms fake TTS and cold process startup. Measurements below are first software writes and fake WebSocket heartbeat delay, not acoustic/native production acceptance.
- Markdown: **87 files / 624 local links and anchors**, zero issues; `git diff --check` is clean.

| Synthetic call | Speech-end to first software audio (ms) |
|---|---:|
| 0 | 530.56 |
| 1 | 529.75 |
| 2 | 560.36 |
| 3 | 560.34 |

Fake WebSocket heartbeat: **124 samples**, maximum observed event-loop delay **2.13 ms** beyond a 5 ms target interval. No SLO/pass threshold is claimed. Local checks do not imply GitHub Actions or live/native acceptance. No new model/weight, paid API, microphone, working DB, schema migration or production-carrier change is involved. Advanced epochs remain gated; no Level 7 gate is marked passed.


## Section 24 Redis state and milestone recovery

Test-first additions reproduced the missing coordinator/lease APIs, immortal counter after a lost reply, missing phrase expiry, invalid TTL acceptance, missing turn/safety milestones and non-error checkpoint failure logs. Follow-up red/green cases repaired initial lease acquisition recovery and transient lease-check poisoning while preserving stale-owner rejection. Production composition, startup cleanup, turn/end/safety hooks and duplicate-context checkpoint/prompt recovery are covered with fake seams; FSM transitions must never execute twice for the same recovered frame.

Disposable PostgreSQL/Redis tests use existing migrations and real repository adapters. They restore exact stage, accepted slot, discovery slots and language metadata after missing/expired hot keys; successor ownership prevents old writes/releases. Booking tests cover advisory hold outages, a lost acquire reply, recovery and foreign ownership without bypassing SQL conflicts. The source/tests never contact working `roma`, paid providers, microphone or models.

The combined checkpoint/Redis/booking/streaming/architecture run passed **223 tests** before final production wiring; production focused checks passed **64**, and the final duplicate-frame checks are included in the full suites below. Final **2026-10-23** verification passed `make -o services-up -o db-upgrade ci`: Ruff clean, mypy clean on **88 source files**, **3,078 tests / 22 existing warnings** (121.20 s); `make test`: **3,078 passed / the same 22 warnings** (100.94 s), no failures/skips. The final controller-focused run passed **59 tests**, including failed and recovered duplicate frames; final native SQL/Redis milestone regressions passed **13 tests**. Local checks do not claim a GitHub Actions result. All existing migrations are verified on a fresh native disposable database; working-service/DB Make prerequisites are deliberately bypassed, and integration tests create their own disposable services. Ruff/mypy and Markdown checks are part of final verification.

The acknowledged-milestone recovery point comes from awaited SQL commit, not Redis persistence or replay. In-progress work since the last SQL milestone may be lost; a crashed worker's lease can delay admission until expiry. The core gate, native/reliability acceptance, deployment RPO/RTO and advanced Streams/replay remain pending. No schema/provider/model/settings change or live call was used. The sections 22–23 synthetic timing report remains historical to its source fingerprint; no fresh native capacity/latency claim is inferred from it.

Final Markdown verification: **87 files / 630 local links and anchors**, zero issues; scoped diff whitespace check is clean. No core or advanced gate is marked passed.

## Section 25 provider reliability

Initial test-first coverage exercised missing shared reliability APIs, inherited admission/turn deadlines, transient SSE retry, circuit trip/probe/cancellation and immediate SDK stream closure. Cross-review then reproduced timers cancelling downstream consumers between yielded chunks and manifest/cache-identity waits escaping the inherited deadline. Four failing regressions now pass with per-await timers and retained native hash admission. Extraction and queued production wording share one configured turn budget; duplicate contexts do not reset it.

Independent final provider/text/voice/streaming checks: **682 passed / one existing dependency warning (12.47 s)**. The adapter-focused review run passed 287 tests before four final timing cases; their targeted follow-up passed 207. Ruff and scoped mypy passed. These are offline/fake-provider checks, not paid reliability/native acceptance. Native inference/Twilio call creation are not retried. Full integration verification follows sections 26–28; no gate is passed and no provider/model/.env/working-DB change was used.

## Section 26 background processing

Test-first slices failed on absent handler/summary queue interfaces, missing persisted consent, incorrect terminal recording status/reason and missing exhausted-recording SQL handoff. Offline fake SDK tests exercise explicit paid opt-in and metadata-only caps without paid calls. Disposable SQL/Redis proves due scheduling, claims/retries, idempotent effects/receipt settlement, visible dead letters and original recording-queue acknowledgement identity. The implementation agent's final worker/SQL/Redis run passed **94 tests**; independent integration checks use the same suite before the section commit. Ruff, formatting and scoped mypy passed. No actual followup, paid summary, microphone, model download or working-DB mutation occurred. Full integration checks follow sections 27–28; no Level 8 gate is passed.

## Section 27 webhook idempotency

Initial tests failed on the absent window API and missing service integration. Real Redis proves processing/done TTLs, expiry and stale-owner rejection; fake seams prove SQL-before-done, busy responses, cache-error SQL fallback and cancellation release. Cross-review found an advisory cache digest incorrectly overriding SQL conflict decisions; a failing real SQL/Redis stale-digest case now passes. Disposable tests simulate a crash after committed receipt/call/event but before done and verify one effect on retry. Independent SQL/Redis/service suite: **17 passed (6.25 s)**; API/Redis/service integration separately passed **20 tests / four existing warnings**. No working DB, schema, paid provider, carrier settings, microphone or model changes. No Level 8 gate or exactly-once delivery claim.

## Section 28 authentication and final integration

Test-first additions failed on missing authentication primitives, sessions/refresh tables, permissions, live revocation, refresh-race handling, Viewer recording redaction and shared admission. Migration metadata/head tests exposed a stale hardcoded prototype prerequisite; the prototype now reads the single Alembic head from files without modifying a database. New revision 0004 adds no seed identities and preserves all earlier revisions.

Focused auth/API/migration checks passed **145 tests**; logging/secret handling checks passed **37 tests**. The first combined CI run exposed six order-dependent logging failures after in-process Alembic disabled application loggers. The exact sequence reproduced **one pass / six failures** before preserving pytest logging in the shared migration fixture, then **seven passes** after; broader affected auth/logging/migration coverage passed **212 tests**. Production logging/filter behavior is unchanged by this isolation repair.

Final offline integration on 2026-10-24: a fresh native disposable PostgreSQL applied base-to-head `20261023_0004`, then stopped before the serial suites. `make -o services-up -o db-upgrade ci` passed Ruff, mypy (**99 source files**) and **3,158 tests / 22 existing dependency warnings (112.12 s)**. Its service/migration prerequisites were deliberately skipped in that invocation after separate disposable migration verification, avoiding working-service changes. `make test` independently passed **3,158 tests / the same 22 warnings (112.18 s)**. Mock provider settings and offline model/tooling flags were enforced; SQL/Redis integration fixtures created their own disposable services.

Environment-template integration also reproduced two failures before repair: the exact-key check knew only the original Settings profile, and a blank auth secret prevented normal unconfigured startup. The key contract now covers both profiles without ignoring unknown keys; blank secret disables human auth, while nonblank short secrets still fail. The related configuration/security slice passed **37 tests** before the final aggregate rerun. Markdown verification passed **87 files / 653 local links and anchors**; cached whitespace and actual private-credential diff checks passed. All provider tests use fakes; PostgreSQL/Redis fixtures are disposable. No private .env, working roma data/schema, paid Claude, microphone, model weights or production speech path was changed. Operational signing-secret setup, intended-environment migration, Admin bootstrap and institute permission approval remain explicit; advanced tracks/native acceptance and all gates remain pending.


## Sections 29–33 security, privacy and governed knowledge

The owner authorized these five sections with parallel implementation and independent review. Callback tests prove strict bounded forms, configured-account/signature validation, outbound intent expiry, call-bound short-lived stream capabilities and shared replay admission before provider construction. Rate-limit regressions cover atomic shared Redis expiry, verified-user budgets, untrusted forwarded identity, safe Redis failure and pre-accept WebSocket rejection.

Privacy tests cover authenticated selected-field ciphertext/tampering, legacy key-free behavior and explicit backfill; call-bound consent, real SQL recording production, human-only access and durable audits; preview-first disposal, bounded Redis cleanup and replay-resistant tombstones. Independent review reproduced two privacy races before repair: a role downgrade during playback still returned audio, and a stale backfill restored erased plaintext. Final permission rechecks and locked/conditional backfill updates now pass their regressions, including actual concurrent disposable PostgreSQL. Equivalent hexadecimal key encodings are compared as decoded bytes; invalid/equal keys fail with sanitized errors.

Knowledge tests cover deterministic ownership, no-evidence/static replies, no discovery extraction or booking intent from informational questions, global pacing/deadline/terminal priority, scoped approved versions and immutable content provenance. A later independent review exposed booking-noun questions such as branch visit policy entering profile extraction; classifier and controller-only signal/counter regressions now prevent that. Governance adds actual SQL lifecycle/effective/scope/version guards, Admin-only operations and metadata-only retrieval provenance; no vector search or embedding inference is claimed.

Final integration initially exposed stale head/table expectations, the missing RecordingRecord disclosure mapping and legacy mocked-generation fixtures that used factual questions. The record mapping now round-trips explicit and legacy values, schema tests retain the exact preserved six-revision chain, and streaming/fault tests use stage acknowledgements while keeping their original assertions. The intentionally interrupted first CI run is not passing evidence.

Final verification on **2026-10-24**:

- A fresh native disposable PostgreSQL applied base through **20261024_0006**, exposing **31 application tables**, then stopped. No configured working-database connection or upgrade was used.
- `make -o services-up -o db-upgrade ci`: Ruff passed, mypy passed on **119 source files**, **3,354 tests passed / 22 existing dependency deprecation warnings** (124.83 s). Working service/migration prerequisites were bypassed after the separate disposable migration check; test fixtures use their own isolated services.
- `make test`: independently **3,354 passed / the same 22 warnings** (126.42 s), no failures or skips.
- Callback/media/API review covered 678 tests; routing/conversation/realtime verification covered 1,137; the final independent security review passed 63 focused tests. Privacy SQL/backfill concurrency and knowledge migration/approval tests used disposable PostgreSQL. The final aggregate suites above supersede earlier partial and sandbox-limited checks.
- Synthetic media probe used in-memory replay admission and fake providers: five inbound/five outbound events passed without provider, Redis or device access. No paid endpoint/model download or real phone/microphone test ran.
- Tracked Markdown and local-link checks, staged whitespace/syntax checks and configured-private-credential diff scans passed. No private credential values were printed.

These are local offline verification results, not a hosted Actions result or production/privacy/native gate approval. Private .env, working roma, speech weights/providers/settings and the Hindi-base Hinglish policy were preserved. The cryptography library was added under the lockfile; it is not a speech model download. Intended-environment migrations, private key/account activation, consent/retention/legal and Anthropic account-specific terms, backup/export erasure, representative ASR/native scores/latency and live reliability remain pending. Twilio supplies no signature timestamp; inbound freshness is not fabricated. Sections 34 onward and advanced tracks remain separately requested work.

## Operational and supervised acceptance follow-up — 2026-10-25

Base revision: `c6a9487616857867d397e03e381699420a385bff`; the follow-up contains local CLI/device controls, the streaming coverage repair, new catalogued tests, canonical documentation and sanitized evidence. It introduces no migration, model/provider switch, weight download or fixed spoken-asset change.

| Verification | Fresh outcome |
|---|---|
| `make -o services-up -o db-upgrade ci` | **3,378 passed**, 22 existing dependency warnings, **127.64 s**; Ruff passed; mypy **119 files** passed |
| CI catalog | All **191 test modules**, no exclusions; includes new CLI/disposable-SQL resume tests |
| Focused CLI/device/voice/stream/corpus/state checks | 122 passed; separate disposable SQL/Redis resume/booking/recovery slice6 passed |
| `uv run --no-sync python scripts/streaming_lab.py` | Four fake requests/eight chunks; four played calls; all five bounded queues; no fallback |
| Operational rollout | Isolated-first encrypted disk restore/migration/auth/backfill rehearsal, then authorized local0006/Admin/key activation; independent read-only recheck |
| Synthetic backup restore/disposal | Restored private data deliberately proved present, latest deletion ledger reapplied before traffic, SQL/files/Redis scrub and repeated/replay protection passed |
| Supervised voice | Two discovery replies played; 11.555/10.324 s first software audio; one unexplained next-capture failure; later diagnostic had two no-speech attempts |
| Owner listening | Naturalness **2.5/5**; correctness unscored; same-waveform8k/16k **no improvement** |
| Cold TTS profile | 5.183 s imports,1.942 s model load,273 ms combined inference/vocoder; one synthetic phrase, not a percentile result |

The first integrated run had three failures/two errors before native PostgreSQL setup completed. An isolated diagnostic captured `shmget(size=56)` / shared-memory-ID exhaustion (kernel limit32), not disk exhaustion. A temporary diagnostic wrapper also caused a child-process re-entry failure; it was discarded and is not part of the source/verification path. Thirteen exact owned, unused, approximately16-hour-old native test clusters were revalidated and gracefully stopped; their data directories were preserved. Shared-memory segments fell18→5, working PostgreSQL and unrelated clusters were left running, no host settings changed, and the **normal** CI command above then passed. No failing tests were excluded or weakened.

Evidence: [local operational summary](../benchmarks/security/local-operations-20261024.json), [synthetic restore erasure](../benchmarks/security/restore-erasure-20261025.json), [voice observations](../benchmarks/voice/acceptance-followup-20261025.json), [TTS profile](../benchmarks/tts/local-profile-20261025.json), [synthetic streaming](../benchmarks/voice/reliability-followup-20261025.json). Private credentials, configuration, encrypted backups/keys and runtime audio remain ignored and owner-only. Owned supervised SQL/Redis/runtime were removed after exporting metadata; the INR6 conservative reservation ledger survives outside that fixture against the aggregate INR50 ceiling.

No full acceptance gate changed. Real multilingual discovery-to-booking, representative ASR, formal native correctness/naturalness, acoustic onset/p95 and agreed budgets, actual carrier/native interruption/concurrency/recovery, institute disclosure/retention/legal and Anthropic organization-specific retention evidence, external/whole-person erasure and advanced prerequisites remain pending. Successful deterministic discovery replies did not exercise a paid Claude conversation.


## Phase 5A persistent capture and deterministic replay

Final `make ci` and `make test` each passed **3,440 tests / 22 existing warnings**, after isolated native PostgreSQL/Redis startup and migration through `20261024_0006`; the owned services were stopped afterward. Ruff passed, mypy checked 125 source files, and the complete explicit catalog contains 200 modules with no exclusions. Markdown checks covered 687 local links/anchors before this evidence link was added. Working `roma`, private configuration and models were not changed; no paid inference, microphone or production transport ran.

[Verification and red/green test names](../benchmarks/voice/phase5a-verification.json) and [synthetic jitter/replay metrics](../benchmarks/voice/phase5a-adaptive-replay.json) distinguish the proof from remaining gates. Initial full CI exposed outdated pull-stream harnesses; they now verify persistent callbacks and software muting with exact PCM coverage. Review additionally reproduced a committed-but-invalidated booking result and dropped capture-deadline tails; regressions now prove next-turn SQL reconciliation and preservation of all eligible bounded input. The fake clear path executes before cancellation-resistant provider cleanup within its 60 ms test target. Actual driver/callback reliability and acoustic interruption latency remain unmeasured. Adaptive concealment adds labelled waveform but does not establish recognition quality or latency superiority. No gate is marked passed. The owner explicitly requires one local commit for this phase and no push.


## Phase 5B retained TTS and speech rendering

Both `make ci` and `make test` completed with exit code 0 against fresh disposable native PostgreSQL/Redis; the successful runner recorded 140.16 s and 148.78 s respectively, including CI startup/checks. The exact catalog collects 3,572 tests in 203 modules. Ruff passed and mypy checked 127 source files. Owned services were stopped. [Verification metadata](../benchmarks/tts/phase5b-verification.json) records this scope; [native synthetic timing](../benchmarks/tts/phase5b-native-20261025.json) records the separate cached-model measurement.

Red/green coverage includes worker reuse, bounded IPC/admission, identities/epochs, cancellation and mandatory kill/drain/reap, startup retained warmup, eight-word comma chunks, concurrent synthesis/playback and pre/post spoken safety. Full CI exposed calendar years spoken with monetary-scale words being replaced by financial safety; grouped calendar-year speech now preserves the date without weakening the filter. Midnight and early morning are explicit. The native profile preceded typing-only declarations/comments and the calendar-year repair, which does not affect its clean/time pairs. No microphone, paid call, model download/change or working-database operation occurred. No naturalness/acoustic/native gate is marked passed. This phase is one local commit with no push; Phase 5C implements the later explicit full-duplex request under its separate evidence limits.

## Phase 5C duplex verification scope

The explicit CI catalog registers `test_echo.py`, `test_duplex_device.py` and `test_duplex_voice.py`; existing speech-input and CLI modules cover the integration. The mypy file list adds the duplex session and echo evaluator, with audio adapters already included by directory. Fake tests cover bounded reference/capture queues, sample-clock scheduling, independent capture during decode/playback, onset ownership, stale epochs, cancellation/clear and failure paths. The optional native-DSP cases require the exact pinned runtime and skip when it is unavailable; a skipped case is not native evidence.

`uv run --no-sync python scripts/echo_lab.py` runs real WebRTC DSP on seeded generated harmonic/noise signals with known delayed echo and independent near-end content. Attenuation and energy/correlation metrics can establish numerical behavior on those probes only. They do not establish intelligibility, representative Gujarati/Hindi/English/code-mix preservation, physical double-talk, callback reliability, acoustic onset/clear latency or native acceptance. No microphone, paid provider, model download or working-database operation is required for these checks. No gate is marked passed.

Final Phase 5C verification on 2026-10-26: actual `make ci` passed **3,630 tests / 22 existing warnings**, followed by `make test` with **3,633 / 22**. CI collection preceded the final three device regressions; the separate final full suite includes them. Fresh final Ruff passed and mypy checked **130 source files**. Both commands used fresh disposable native PostgreSQL/Redis, upgraded through current head and stopped their owned services. [Verification metadata](../benchmarks/voice/phase5c-verification.json) records red/green cases and source hashes; [real-DSP synthetic metrics](../benchmarks/voice/phase5c-synthetic-echo.json) record the separate generated-signal probes.

Independent review reproduced, then rechecked fixes for terminal-call admission and unplayed echo references after output-generation or epoch-only invalidation. Goodbyes complete before graceful shutdown; native output abort/close still runs if reference cleanup fails. The composed fake-device/session test preserves the complete interruption onset while playback blocks. Native DSP probes use no microphone: default noise-suppression-off late echo attenuation was 8.75 dB on voiced harmonics and 23.79 dB on noise, with double-talk energy ratios 0.74 and 0.45. These imperfect synthetic preservation results require real speech/device calibration and do not approve intelligibility, acoustic interruption latency or a full gate.

## Booking vocabulary prompt integration — 2026-10-26

The fixed reviewed 98-byte/24-token noun list is wired to both single-call `initial_prompt` and batch decoder prompts behind `WHISPER_BOOKING_VOCABULARY`; it defaults off until a paired representative comparison. Prompted runs have distinct provenance and a bounded 410-token output allowance; historical reports/default decoding remain intact. No caller text, names, phone numbers or arbitrary operator prompts can enter this list. Independent review and the integrated focused run passed **224 offline tests**, with **3 native MLX tests deliberately deselected** in this slice. [Verification metadata](../benchmarks/asr/booking-vocabulary-verification.json) records the source hashes. No new ASR inference, model swap/download, WER improvement or native-quality acceptance is claimed.

## Final integrated offline verification — 2026-10-26

Actual `make ci` and separate `make test` each passed **3,718 tests / 22 existing dependency warnings** on the combined full-duplex, vocabulary and provider-erasure implementation. Pytest times were 137.72 s and 135.66 s. Ruff and default mypy passed across **136 source files**; the explicit CI catalog contains **210 modules**. Each final run used a fresh disposable native PostgreSQL/Redis environment, applied the existing migration chain through `20261024_0006`, and stopped its owned services. [Integrated metadata](../benchmarks/security/phase5-integrated-verification-20261026.json) records results and source fingerprints.

The aggregate run first exposed a real public-template omission: `test_env_example_matches_settings_fields` failed because the new vocabulary setting was missing. Adding `WHISPER_BOOKING_VOCABULARY=false` and correcting two documentation references passed the 50-test focused configuration/vocabulary selection before both full reruns. An earlier temporary PostgreSQL startup failed only because its macOS Unix socket path exceeded the OS length limit; the runner moved to a short private temporary path. No working database/private `.env`, models or provider defaults changed.

Independent review covered the erasure operator's explicit apply/account ownership/relationship checks, retry suppression, sanitized outputs and durable retry ledger. The known-resource operator remains unused against real providers; its 51 new mocked tests are included in the full results. Default primary-checkout typing now passes, superseding isolated-worktree import limitations. No paid provider, microphone, playback, remote deletion or new model inference was used for the final verification. Native voice quality remains unaccepted: the prior 2.5/5 rating has no new listener replacement, and actual duplex acoustic preservation/latency, representative ASR, full multilingual booking, institute/legal/provider-account terms and broader erasure remain pending. No full gate was marked passed; delivery remains local with no push.

## Phase 6 guided acceptance — 2026-10-26

The first Phase 6 `make ci` and separate `make test` each passed **3,734 tests**. These aggregate results precede the latest stale-rating and capture repairs. The stale-rating defect was reproduced red, then the focused evidence suite passed **9 tests**: signed ratings from another run/source remain private, current readiness reports no new score, and tampered current or stale receipts still fail HMAC verification. Final combined-source verification is recorded separately after the latest repairs.

The initial supervised launch reached model warmup, device open and `CAPTURE_STARTED`, then ended with a CAPTURE failure. Verified local receipts written at `2026-10-26T03:55:17.012072Z` record microphone use, no completed turn or playback request and no retained recording; no SQL booking commit/restore was verified. The source was base commit `c338a0ea3e9be90eb0b24e89fd44b6e2e0c40467` plus uncommitted Python fingerprint `0290b433c333bd16cee1d15457c1641ef611e8647bfe40f3b8e5f6dce95070f6`; the base commit alone does not identify the tested source. This launch establishes no successful playback, backchannel handling, interruption or booking.

The later supervised run, recorded at `2026-10-26T04:34:04.216967Z`, used the same base commit plus dirty Python fingerprint `f8282d52510efc62f69614456c95ab0377cdf378f4be77d0fa68405c11468a5b`. It produced **two played turns/two playback requests**, recovered from three capture losses, then failed on the fourth loss. Software first-audio timings were **7.95/7.07 s**; reported TTS timings were **600/13 ms**, including one cache hit. The owner supplied an actual **4/5 naturalness** rating and heard that the requested time was not finalized. Correctness, backchannel preservation and interruption success were not supplied. Booking commit/restore values are null with `booking_verified: false`, because the failed run skipped SQL verification; no absence or success is inferred. The signed rating and bounded current-readiness blocks preserve the earlier **2.5/5** as historical evidence.

Recovery discards the incomplete utterance and pending stale work, invalidates the epoch, clears output and requests repetition. Input received during cleanup is discarded; the same native input stream resumes afterward. Three bounded recoveries share the original deadline, and the fourth loss or failed cleanup is terminal. This is not lossless capture or replay. A separate supervised probe temporarily raised the weak system input level from **15% to 80%**, produced a **26-character ASR result**, and restored the original **15%**, which was verified. No transcript is retained here. Conservative aggregate reservations reached **INR18/50**; reservations are not invoice costs.

HMAC verifies local receipt integrity, not acoustic performance, listener identity or policy authority. Acoustic first-audible/stop metrics remain null. The new owner rating does not pass formal native/representative quality. Representative ASR, native multilingual quality, physical double-talk and acoustic interruption latency, full discovery/booking, reliability/concurrency, institute disclosure/retention/legal and account-specific provider terms, and whole-person/backup erasure remain pending. No full gate is passed.

Final combined-source `make ci` and separate `make test` each passed **3,750 tests / 22 existing warnings** (pytest 134.20 s / 128.25 s). Ruff passed; default mypy checked **139 source files** and the CI catalog contains **212 modules**. Both commands used fresh disposable PostgreSQL/Redis through `20261024_0006`, then stopped their owned services. [Phase 6 metadata](../benchmarks/security/phase6-verification-20261026.json) records source hashes, failing-before/passing-after regression names and the separate failed live outcome. These offline runs made no paid requests or microphone use. The tested Python fingerprint matches the final supervised run; private `.env`, working `roma` and speech weights were unchanged. Delivery is one local commit without push; no full acceptance gate passed.

## Sections 34–40 governed retrieval — 2026-10-26

The new implementation extends sections 32–33 with idempotent SQL-leased ingestion, canonical Markdown/table chunks, pinned E5 embeddings, exact scoped pgvector and bounded extractive answers. [Level 9](levels/level-09.md) owns scope and acceptance; [runbook](runbook.md#governed-ingestion-retrieval-and-golden-evaluation-sections-3440) owns reproducible commands. Final combined-source `make ci` and separate `make test` each passed **3,933 tests / 22 existing warnings** (pytest **150.25 s / 143.86 s**). Ruff passed; default mypy checked **155 source files**, all **222 test modules** are in the explicit CI catalog, the offline lockfile check passed and **706 local Markdown links/anchors** resolved. Both commands used fresh disposable PostgreSQL/Redis through `0007` and stopped their owned services. Earlier Phase 6 totals are historical.

The checked-in [local report](../benchmarks/rag/local-v1-report.json) records actual cache-only `intfloat/multilingual-e5-small` revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384 dimensions, preprocessing `e5-prefix-mean-l2-v1`, artifact digest `142babea870580b1913d63db19990a1808407b69be7f1e570d246a8dfb09c0f6` and exact pgvector execution. Corpus SHA-256 is `6fc2c2acd36978f2d94708ee496d297e393fbbf67ff0415eaaacb5bca2a30f96`; report material fingerprints identify the evaluated source. All 62 synthetic cases execute: 24 calibration select threshold 0.80 without held-out use, and the reported summary covers 38 held-out cases (16 answerable, 22 expected deferrals, five safety cases).

Held-out Recall@5/MRR are **93.75% / 93.75%**; Hindi is **75% / 75%**, while English, Gujarati and Hinglish each reach **100% / 100%** on four answerable cases per language. Preserve the calibration and held-out Hindi batch-schedule failures. Warm retrieval P50/P95/P99 are **13.09 / 14.03 / 14.09 ms**, including query normalization, query embedding and SQL search, excluding model/artifact loading during ingestion, Claude generation, revalidation and audit. Isolated embedding RAM was about **807 MB on the 8 GiB host**; combined voice-stack headroom is unmeasured.

Span support, expected deferral, routing and safety mechanics are 100% in the held-out report. The generation evaluator is `deterministic_fixture_selector`: an oracle selector proves mechanical exact-span release and fallback behavior only. `real_claude_groundedness` is null, `paid_claude_called` and `native_review_passed` are false, and **`quality_gate_passed` remains false**. Mechanical checks meet provisional aggregate Recall80/MRR70/span90/deferral95/safety95/P95450ms targets; span support is not a substitute for the owner's real-groundedness90 requirement.

No paid provider request, microphone, private `.env` change or working-`roma` migration/write belongs to this increment. Native `0007` migration/vector/job verification uses disposable PostgreSQL only; working `roma` remains at the separately accepted `0006`. Approved representative corpus/documents, Hindi retrieval improvement, actual Claude semantic groundedness/relevance and answer latency, native review, combined resource/concurrency/cancellation and production enablement remain open. Documentation synchronization does not pass Level 9 or start the next level.

[Verification metadata](../benchmarks/rag/verification-20261026.json) records tested Python fingerprints, check results, actual regression names and native SQL readback. The final native run `c05be042-1afb-4063-90fa-d8b2d7b3cc80` persisted **one benchmark run / 45 metric rows**; readback also found nine succeeded ingestions, eight active documents and nine canonical chunks with pgvector 0.8.5. A mode-0600 synthetic-only custom dump is retained under ignored `var/roma/rag-benchmarks/`; the disposable cluster was stopped. Initial aggregate failures exposed a worker/API test-module basename collision, stale composition doubles and old table/head expectations; these were corrected before the passing full reruns. The new configuration and safety-context/booking-quote regression tests also ran red before their fixes. Delivery preserves the current local-commit/no-push workflow; six earlier local commits must not be published implicitly.

## Sections 41–46 telephony, serving, telemetry and costs — 2026-10-26

Final 2026-10-27 `make ci` and separate `make test` each passed **4,092 tests / 37 dependency deprecation warnings** (pytest **149.10 s / 148.45 s**). Ruff passed; mypy checked **177 source files**; the explicit CI catalog contains **238 modules**; the dependency lock check passed; **767 local Markdown links/anchors** resolved. Both commands used fresh disposable PostgreSQL/Redis through `20261026_0007`, then stopped their owned services. [Verification metadata](../benchmarks/voice-integration/verification-20261027.json) records exact source/material fingerprints, check-log hashes and failing-before/passing-after regression names. Earlier section/phase counts are historical.

[Level 10](levels/level-10.md) owns sections 41–44; [Level 11](levels/level-11.md) owns only 45–46. Check bounded/failing readiness, authentication, request/response/body/audio/text limits, warmup/overload/deadline ownership, cancellation/stale epochs, transport pressure/disconnect, RAG safety/revalidation and recording off-loop write/fsync ordering. Telemetry needs bounded labels/no payload retention and real measurement-boundary tests. Costs need exact Decimal sums, immutable idempotency, unknown usage/rate/TTL counts, currency separation, UTC day/month selection and auth/permission/error tests. Native resource values and synthetic harness statistics must preserve their scope and unknowns.

The reviewed direct-standard Haiku price source is dated 2026-10-26; this is a new versioned estimate, not historical repricing, a live invoice or a change to the conservative INR admission ledger. Carrier/local hardware rates remain unpriced without verified billing basis. Configured serving limits and fake model throughput do not prove native GPU capacity.

No native phone call, microphone, paid generation, actual local-model inference, intended working-database migration/write or private setting change is inferred from offline tests. Any authorized native service probe must record its actual scope separately. Full acoustic/representative quality, multilingual booking, native concurrency/combined RAM, only-Claude external GenAI traffic, institute consent/retention/legal and provider-account gates remain open. Sections 47–48 and Level 12 evaluation are unstarted by this increment.

The initial combined run exposed Alembic replacing/disabling existing application logging during in-process test migrations. A real SQL regression reproduced it; programmatic migrations now preserve existing logging/redaction/correlation handlers. Final review also reproduced the slow-successful-write backlog gap: local TTS now reserves bounded PCM before Pipecat queue admission, with generation-safe software-write acknowledgments. Urgent call cancellation is requested before cleanup waits. These fixes preserve the existing log-privacy assertions.

The [native observability receipt](../benchmarks/telemetry/native-20261026.json) passes all **22 synthetic checks** on pinned Prometheus 3.15.1/Grafana OSS 13.2.4, with matching final telemetry/configuration hashes and stopped owned processes/listeners. It does not establish real-call signals, acoustic latency or native model capacity. The [fake serving harness](../benchmarks/serving/fake-capacity-20261027.json) records 20/20 accepted serial mixed requests and, under intentionally overloaded concurrency two/admission one, two successes plus 18 explicit 429 rejections. GPU values remain null; fake timing is not a native capacity/SLO result.

Publishing for this requested increment follows the owner's later explicit commit/push instruction, superseding the historical phase-local no-push restrictions. No full gate is marked passed.


## Sections 47–53 analytics, evaluation, security and packaging — 2026-10-27

Final `make ci` and separate `make test` each passed **4,166 tests / 26 upstream deprecation warnings, with no failures or skips** (pytest **165.16 s / 161.05 s**). Ruff passed; mypy checked **198 source files**; the explicit offline catalog includes **253 modules** with no exclusions. Both targets used the patched lock in a fresh isolated Python 3.12 environment with optional generated-data MLX/vocabulary/AEC checks installed. Owned random-port native PostgreSQL/pgvector and Redis were migrated through `20261026_0007`, then stopped. Working `roma`, private `.env`, the working voice environment and selected speech weights remained unchanged. [Verification metadata](../benchmarks/evaluation/verification-20261027.json) records output hashes and the complete code/configuration tree identity; older receipts keep their dated source identities.

[Level 11](levels/level-11.md) owns SQL outcomes/funnel/branch analytics and metadata-only active-Admin supervision. Real disposable SQL tests prove conversion/percentiles/language/currency semantics, ordered milestone requirements and unknown attribution. A real SQL-to-Redis-to-WebSocket-adapter test proves that controller metadata reaches the subscriber without caller text or provider SID. Root and independent reviews corrected opening/caller ordinal collisions, detector-language projection, initial authorization bounds, slow-subscriber cleanup and combined database connection budgets.

[Level 12](levels/level-12.md) owns the [mandatory readiness/chaos report](../benchmarks/evaluation/project-readiness-20261027.json) and [mock-load report](../benchmarks/evaluation/mock-backend-native-20261027.json). The 36-case failure drill passed, including actual 100-request SQL booking contention: one success and 99 conflicts. The finite 372-call/4,464-turn mock backend ladder completed through 100 concurrent sessions with no errors/fallbacks; speech/GPU/Claude/carrier and durable checkpoint/booking load were not part of that ladder. Nine automatic receipts remain unapproved across 35 mandatory rows, so `full_gate_passed` remains false. The separate 38-case mock RAG golden run passed mechanical routing/span/deferral/safety checks; real-Claude groundedness stays null and the quality gate false.

The [security receipt](../benchmarks/security/section52_audit.json) records zero known advisories across 178 macOS and 191 Linux selected-runtime/dev dependency entries, with no vulnerability ignores. Patched Pillow required locked Pipecat 1.14; existing endpointing behavior now uses a Roma-owned timestamp rather than a removed upstream private field, and Twilio rejects unknown/missing event kinds before sequence updates. Scanner tests reproduce removed merge-only secrets and reject shallow clones; final all-ref history/current-file scanning uses the pinned verified Gitleaks binary. No full phone number, credential or runtime payload is added to tracked evidence. The audited lock still needs controlled operational rollout; separately provisioned runtimes are not silently upgraded.

[Level 13](levels/level-13.md) and the [deployment receipt](../benchmarks/deployment/section53_mock_stack.json) record explicit API/worker/model-contract Dockerfiles, three successful builds, six healthy mock services, migrations, private metrics, Prometheus/Grafana and exactly one worker effect. Initial same-image tag and later distinct compatible-image rollback rehearsals preserved SQL data; neither establishes historical production-release recovery. Missing/partial keys and entirely lost local state with retained volumes now fail closed. Two restarts preserved all original key bytes. Proof containers were stopped and ten owned volumes retained. Selected Mac MLX speech/GPU/native-bridge profiles deliberately fail closed; no native capacity is invented.

Two whole-suite timing assumptions were made deterministic without changing production guards: the cached-deferral test explicitly budgets its real SQL admission before stalled inference, and the duplex fake shares a 20 ms sample clock instead of submitting future-timestamped audio every 6 ms. Original cancellation, no-extra-TTS, full-onset and stale-epoch assertions remain intact. These tests failed before their fixture repairs and passed afterward, including focused repeats.

No paid Claude, microphone, phone, model-weight download, working-database migration, policy approval or complete acceptance gate followed from this increment. Remaining work is controlled runtime/migration rollout, representative multilingual/real-Claude/RAG/voice and physical interruption latency, real-model capacity and agreed SLOs, actual-call dashboard reconciliation, privacy/legal/account/erasure approval, an accepted native serving/deployment bridge, clean-host proof and historical image/backup recovery. Section 54+ is not started. Publishing follows the owner's explicit commit/push instruction.
