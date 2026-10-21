# Sections 1–10 review against the OpenAI roadmap

This first audit is historical (2026-10-12). [The Claude review through section 20](#claude-source-review-through-section-20--2026-10-20) below is current; original model names/counts remain unchanged.


Reviewed on 2026-10-12 at pre-implementation commit `124b178`. The new source is [the OpenAI roadmap](README.md). Section 11 is implemented only after this review. This report distinguishes implemented foundations from teacher/live gates; it does not silently promote later levels.

| Section | Finding and source evidence | Remaining qualification |
|---|---|---|
| 1 Prerequisites | Async/audio/FSM/SQL/networking topics covered by [Level 0](../levels/level-00.md) and the study guide | Student explanation and teacher sign-off cannot be inferred from code |
| 2 Baseline | Twilio/Pipecat/Sarvam/OpenAI path, stages, safety, Redis, interruption and cost traced in [baseline](level-00-baseline.md) | Fresh paid latency measurements are deferred; historical timing is not a current SLO |
| 3 PostgreSQL/Redis | Explicit pool size/overflow/timeout/process allocation and short `PostgresUnitOfWork`; pool/cancellation/pressure tests | PgBouncer is conditional; real voice capacity is not established by pool tests |
| 4 Relational design | 25 application tables, documented keys/FKs/checks/uniques/indexes/cascades; metadata parity and repository tests | Knowledge tables remain Level 9; schema presence does not imply live producers |
| 5 Migrations | Preserved linear three-revision chain, offline SQL, empty/legacy DB upgrade, rollback/re-upgrade and separate demo tests | Roadmap migrations 001–005 are illustrative groupings, not a request to renumber applied history or add empty migrations |
| 6 Architecture | Layered `roma/`, thin routes/services/repositories and domain import-boundary test | Example `backend/app` tree does not justify package churn |
| 7 Providers | STT/LLM/TTS/embedding/telephony protocols and deterministic mocks pass | Old Qwen selection placeholders mismatch the new source; section 11 replaces the LLM selection with OpenAI/mock |
| 8 REST | All nine required v1 resources, envelopes, Pydantic/OpenAPI, pagination/filter/sort and 401/409/422/503 tests | Legacy dial route remains separate; RBAC is Level 8 |
| 9 Engineering skeleton | Native setup Makefile, Ruff, mypy, pre-commit, CI skeleton, fake-provider tests and disposable migration tests | Hosted CI must be checked per commit; historical startup/CI evidence is not a new live demo |
| 10 State machine | Seven-stage facade, allowed transitions, interruption/objection/missing-field handling, intent routing and compatible PostgreSQL latest checkpoints | Redial/session mapping and full Redis-loss recovery are later gates; models cannot commit bookings |

## Fresh evidence before section 11

Full existing offline suite: **1,674 passed, 23 existing dependency warnings**, with no skips. Ruff and mypy (**17 files**) passed; mocked CI slices passed **38 provider/config** and **11 API/architecture/migration-settings** tests. Disposable PostgreSQL tests cover actual schema/migration/constraint behavior independently of the working database.

Read-only configured database inspection confirmed `roma`, revision `20261004_0003 (head)`, **25 application tables and 0 application rows**. Every table's primary/foreign/check/unique constraint and index matches SQLAlchemy metadata. No working-database write, migration or seed ran. Retain the 2026-10-04 upgrade history; other environments must still upgrade before using turn-language.

The baseline commit `124b178` has a completed successful [hosted CI run](https://github.com/NihitRaiyani/Voice/actions/runs/38161170164), confirmed read-only on 2026-10-12. The later section 11 implementation commit `f7df8e0` also passed [hosted CI](https://github.com/NihitRaiyani/Voice/actions/runs/38220411614) after publishing; its final evidence is in [Level 2](../levels/level-02.md#section-11-implementation-and-evidence).

Main voice LLM remains OpenAI GPT-4o, with existing GPT-4o-mini extraction. The new section 11 text seam will use explicit environment model settings. No Qwen weights/runtime remain installed. OpenAI quota is unavailable; no provider, carrier or new live voice benchmark ran during this review. Native quality scores and voice approval stay pending.


## Claude source review through section 20 — 2026-10-20

Reviewed the selected source paragraphs against existing modules/level briefs, with the owner-directed Claude replacement. The unchanged source hash is `67a79ba87fbec07051d460fd4ec9d5520f7047c0e242da72a65bfdd09ede5f48`. “Present” describes code/contracts and recorded evidence; it is not blanket teacher/native acceptance. Fresh execution results for the replacement belong to the implementation verification, not the historical counts above.

| Section | Reused/aligned foundation | Remaining qualification |
|---|---|---|
| 1 Prerequisites | Level 0/study guide cover async, audio, FSM, SQL, Git and networking | Student explanation/teacher review pending |
| 2 Baseline | Existing Twilio/Pipecat trace, safety, Redis, recordings and booking truth retained | Representative current timing and clean-host/reviewer baseline pending; former OpenAI timing is historical |
| 3 PostgreSQL/Redis | Short UoW, explicit pools/process budgets, pressure/cancellation evidence | Real voice capacity not established; PgBouncer remains conditional |
| 4 Relational design | Existing 25-table schema, constraints/FKs/indexes/retention and repository evidence | Table presence does not prove every producer; knowledge tables remain Level 9 |
| 5 Migrations | Existing forward history, rollback/re-upgrade and separate opt-in demo workflow | No migration or renumbering justified by the LLM change |
| 6 Architecture | Layered `roma/`, domain boundary checks, services/repositories/adapters | Preserve example-tree interpretation; no package rewrite |
| 7 Providers | Shared STT/LLM/TTS/embedding/telephony protocols/mocks; Claude replaces OpenAI | Embedding/local Indic ASR readiness remains gated; no automatic provider fallback |
| 8 REST | Existing v1 resources, envelopes, validation, pagination/filter/sort, conflict/error handling | Full RBAC remains Level 8; carrier side effects stay separate |
| 9 Test/CI skeleton | Native Makefile, pytest/Ruff/mypy/hooks/CI; mock provider defaults | New hosted CI result must be checked after publication; tests do not spend/download |
| 10 FSM | Seven stages, code-owned guards/intent/date/confirmation, latest durable checkpoints | Redial identity and full failure-recovery drills remain later work |
| 11 Claude LLM | Thin async SDK adapter/settings, Haiku 5.5 low effort, streaming/cache accounting, budget/retry/fallback, console and separate 40-case lab | Fresh Claude verification separate from old OpenAI counts; Gujarati/Hindi native scoring and paid voice quality pending |
| 12 Safety | Existing deterministic filter/audit, bounded untrusted input, approved replacement and protected queries | Universal semantic correctness and native review not inferred from canary tests |
| 13 Booking | Short row-locked commit, active uniqueness, advisory TTL hold, cancel/reuse, same-SID SQL restore | Real approved branch/reference slots and teacher/native voice demo pending |
| 14 Audio | PCM/mu-law/resampling/framing/fixed jitter lab; live pre-decode sequence/alignment/gap checks | Adaptive jitter/PLC and real RTP impairment remain advanced gated work |
| 15 VAD/endpoint | Bundled Silero, code-owned hysteresis/silence/hard limits and short-ack tests | Representative noise/telephone endpoint quality pending |
| 16 Local ASR | Cache-only Indic/Whisper adapters; pinned Whisper base and pinned IndicConformer caches/runtimes available | Indic Gujarati/Hindi smoke readiness passes; matched WER/CER/native approval and a production choice remain pending |
| 17 Language routing | Script/keyword/history smoothing, Latin code-mix tests and saved router metadata | Caller language never changes Hindi-base Hinglish production output; native routing quality remains separate |
| 18 WER/CER/RTF | Existing manifests/scoring/failure exports, separate hosted/local reports and short immutable SQL evidence writes; local run executed 15/15 | Gujarati/Hindi/stitched local-base quality failed (100% WER in affected paths); hosted capacity incomplete; representative chosen-model/native acceptance pending |
| 19 Local TTS/cache | Installed manifest-pinned isolated Indic-TTS, normalization, cache identity, measured synthesis/RTF, approved-line spoken-text checks | Native listening/code-mix quality and production speech switch pending; canonical safety wording/assets unchanged |
| 20 Microphone prototype | Synthetic four-language paths, real local device seam, local-only ASR/TTS, short durable turns/timings; English physical Claude extraction and prepared-state wording/local-TTS turns heard clearly; checkpoint restore defect repaired | Levels 2–5 prerequisite/native gates unfinished; no full Claude Gujarati/Hindi/English/code-mix microphone/booking approval; section 21 outside scope |

No schema/migration/TTS change was required by the Claude replacement. A later explicit 2026-10-21 decision authorized the pinned IndicConformer snapshot/runtime in addition to Whisper base; it does not authorize paid HF capacity, hosted/private microphone ASR or a production switch. Existing historical reports/source files remain intact. Synthetic paid Claude verification and mock/offline tests cannot complete all gates through section 20.
