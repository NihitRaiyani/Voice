# Verification and acceptance evidence

Current source/provider policy changed on 2026-10-20: [Claude roadmap](roadmaps/README.md) and Claude Haiku 5.5 replace OpenAI. Dated checks, model names, counts and former source hashes below remain historical evidence; they do not prove Claude quality/cost/latency. No OpenAI requests or fallback are permitted. Native/representative and complete voice gates remain pending.


Latest complete-suite verification after the [Level 2 section 10 increment](levels/level-02.md): **1,674 tests passed, 23 warnings, no failures or skips**. The new `make ci` skeleton starts native local services, runs Ruff, runs mypy on the Level 1 contract surface, applies Alembic to the local database, passes **38 provider/config tests** and passes **11 mocked API/architecture/migration-settings tests**. Pre-commit hooks pass across all files.

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

The explicit mixed-language path now preserves `gu-en`/`hi-en`, detects one bounded phrase gap before ASR and routes Gujarati/Hindi to pinned IndicConformer. Whisper inspects the suffix without a forced language; only a detected, credible English result is accepted, otherwise the suffix remains with Indic. No-gap Indic output receives only exact reviewed domain-loanword normalization. Provider failures still propagate. Focused tests cover direct routes, gap bounds, noisy false positives, Whisper language arbitration, provider failure and exact-token normalization beside Indic punctuation.

The unchanged 15-case public report completed offline with zero provider errors. Gujarati/Hindi-only remains **15.43% WER / 3.98% CER**. Clean/telephone stitched code-mix is **12.50% / 3.50%**, while noisy stitched code-mix is **40.48% / 22.64%**. Mixed median wall time is **2.031 s**, so noisy quality and the proposed 1.2-second latency target fail. The final immutable SQL run `section18-adaptive-local-20261021-v3` stores 15 samples / 90 aggregate rows under three model identities; earlier v1/v2 runs remain historical intermediate measurements. No transcript/reference text, schema or business data changed.

Paid Claude and microphone testing stayed deferred. Production speech, weights and reply policy stayed unchanged. Native intra-sentential/domain audio and human review remain required; see [Level 4](levels/level-04.md#adaptive-local-code-mix-routing--2026-10-21).
