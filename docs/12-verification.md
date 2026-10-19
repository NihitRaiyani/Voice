# Verification and acceptance evidence

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
| L2/L3 | Durable text restore, OpenAI streaming/text evidence and safety audit, committed live booking, 100-attempt race and cancellation/idempotency |
| L4–6 | Approved language/noise WER/CER/RTF, TTS intelligibility/first audio and complete local voice lab |
| L7 | Barge-in/double cancellation, late results/disconnect, Redis/provider loss, bounded pressure and event-loop lag |
| L8 | Roles/token lifecycle, forged/stale/duplicate callback policy, job crash/replay/retention and PII/access approvals |
| L9 | Eligible scoped/versioned evidence, local embeddings, golden Recall@K/MRR, grounding/deferral and final safety |
| L10 | Authorized local-model carrier call, serving/readiness/GPU admission, retrieval cancellation and OpenAI-only external GenAI traffic |
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

[Level 3 evidence](levels/level-03.md#acceptance-gate-and-evidence) records **1,825 offline tests**, the expanded **56 + 245** CI selection, Ruff and the 36-file mypy check. Native PostgreSQL tests exercise a 100-request HTTP race (one 201, 99 conflicts), concurrent different-slot attempts for one call, cancellation/reuse, stable retries, commit rollback and index enforcement. Native Redis verifies atomic ownership/TTL; controller/pre-TTS tests reject false confirmation and restore SQL appointment truth. No paid calls, schema mutation or configured-database seed is part of verification. Real approved slots, teacher demonstration and complete voice approval remain separate.

## Section 14 digital audio

[Level 4 section 14 evidence](levels/level-04.md#section-14-acceptance-and-reference-evidence) records **1,859 offline tests**, the **56 + 287** CI selection, Ruff and 44-file mypy checks. Codec/reference vectors, anti-alias/image rejection, streaming frame/phase/tail behavior, fixed 40/50/60 ms jitter cases, live pre-decode ordering and clean energy-based VAD consumption are covered. The reproducible virtual-clock impairment report is not neural ASR quality, actual carrier loss or end-to-end voice latency. The complete Level 4 and advanced adaptive/PLC/real UDP-ASR gates stay pending. Production retains native serialization and its current sample rates; no working-DB/secret/fixed-asset change is part of this increment.

## Sections 15–17 speech input

The [Level 4 speech evidence](levels/level-04.md#verification-boundary) separates deterministic endpointing/router/SDK-contract tests from real ASR quality. Existing bundled Silero silence/reset runs locally; real IndicConformer/Whisper inference and hosted endpoint access do not run in CI. `make speech-lab` uses synthetic data without `.env`/downloads. Tests cover worker busy admission through cancellation, HTTP error/privacy limits, normalized text, 20→32 ms framing and accepted-turn/checkpoint integration. The later [implemented-boundary audit and hosted smoke](levels/level-04.md#implemented-boundary-audit-and-hosted-smoke) records actual public FLEURS Gujarati/Hindi IndicConformer and English Whisper inference without local ASR downloads. Its three clean read clips and reported timings do not establish representative accuracy/RTF, noisy endpoint or complete voice approval. Audit regressions cover specific safety/input/auth/storage/audio/STT failures and do not imply exhaustive bug absence.

Implemented-boundary audit evidence before sections 18–19, on 2026-10-19: **2,412 offline tests**, **56 + 840** bounded CI tests, Ruff and **54-file mypy** pass; **65 Markdown files / 355 local links** pass. The explicit public smoke also passes the actual configured adapters in all three languages and the bundled-Silero-to-hosted-ASR Gujarati path. [Level 4](levels/level-04.md#integrated-audit-verification--2026-10-19) retains exact timings, scope and remaining quality gates.

## Sections 18–19 verification — 2026-10-19

Fresh final checks: **2,574 offline tests passed** (23 existing dependency warnings, 84.59 seconds); `make test-ci` passed **56 provider/config + 1,002 bounded tests**. Ruff and **62-file mypy** pass; all pre-commit hooks pass. Local Markdown links/anchors pass. Native PostgreSQL test fixtures release the new benchmark cluster at module end; the suite passes without changing application or working-server configuration. Model/runtime installation and real inference are separate explicitly authorized checks, never default CI work.

[Level 4](levels/level-04.md#section-18-asr-benchmark) owns the small public ASR corpus, scores, missing English spans, saved/read failure WAVs and 12 explicit Whisper service errors. Indic/Whisper evidence was persisted to existing `roma` benchmark tables: two runs and 102 metric rows. No schema/migration changes occurred. Native code-mix/representative quality and a complete matched-model comparison remain pending.

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

The physical microphone/headphone conversation, all-local ASR models on this Mac and native prerequisite gates remain unverified/unavailable. HF-hosted ASR cannot substitute for the OpenAI-only cloud constraint. The optional `voice-lab` dependency installs PortAudio I/O software only; production provider selections, canonical safety wording, fixed assets and migrations remain unchanged. Section 20 integration code is implemented; its real Level 6 gate is **not passed**.
