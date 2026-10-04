# Level 1 — Backend foundation

**v4 sections:** 3–9. **Status:** Sections 3–6 persistence/pool, relational foundations, migration/separate-demo workflow and architecture boundaries implemented; remaining sections 7–9 and full gate pending.

## Entry gate

Level 0 remains pending clean-machine restore, representative timing and prerequisite review. The user explicitly authorized these bounded section 3/4/5 increments; it does not pass or waive those remaining gates.

## Full-level scope and remaining sections

Add PostgreSQL durable entities and Redis transient boundaries. Use SQLAlchemy 2 short units of work with explicit pool/overflow/timeout settings and a process-wide connection budget. Define leads, calls/turns, appointments, users, jobs/audit and knowledge metadata as required by later gates; avoid speculative unused entities.

Introduce Alembic migrations with tested upgrade/rollback strategy and separate seeds. Keep a modular monolith and domain rules free of SDKs. Define ASR, LLM (wording and extraction), TTS and telephony contracts with mocks/configuration.

Design `/api/v1` envelopes/errors, pagination/filter/sort and OpenAPI without silently replacing compatibility routes. Establish pytest/Ruff/type-checking/pre-commit/CI incrementally, and a documented local-service startup without Docker.

PostgreSQL/schema/layers already exist here. Review and fill their gate gaps; public routes remain unversioned despite the `api/v1` directory. Existing infrastructure Compose stays compatibility tooling; the new foundation startup path uses native services.

## Existing reuse in Voice_Agent

Layered `roma/`, async SQLAlchemy pools/units of work, three Alembic migrations, FastAPI, repository contracts and pytest/Ruff. Complete gaps; do not rebuild these foundations.

## Acceptance gate

- [ ] One documented command starts required development services without Docker.
- [x] Section 4: schema keys/constraints/indexes/deletion choices reviewed; empty and legacy-populated migration/rollback verified.
- [x] Section 5: preserved Alembic history, database-only development workflow, tested rollback and separate opt-in synthetic seeds; approved real business-data imports remain owner-governed.
- [x] Section 3: bounded pool configuration, process allocation and synthetic pressure/release/timeout/cancellation evidence; real deployment budget must be checked against its server.
- [x] Section 6: modular monolith package map documented; domain dependency direction enforced; concrete Redis/worker leaks removed from domain exports.
- [ ] Provider contract mocks and versioned API checks pass; CI is green.

## Boundaries and advanced work

No local model loading, RAG, microservice split or Docker. New tooling is implemented deliberately, not claimed available now.

## Section 3 accepted design and implementation

Preserve the existing SQLAlchemy 2 async lifecycle, Alembic migrations, durable record models/repositories, atomic answer acceptance and post-call/job writes. Redis retains active-call checkpoints, lead lookup, status projections, caches, locks/rate counters and job-ID delivery. No schema migration or provider/conversation behavior change is needed for this increment.

The alternatives were leaving capacity as prose, adding bounded settings/preflight with measured tests, or deploying PgBouncer immediately. Use the bounded-settings option: it closes a concrete gap without new infrastructure. Consider PgBouncer after direct-pool measurements show excessive process fan-out; test transaction pooling and asyncpg prepared-statement behavior before adoption.

Implementation sequence:

1. Validate positive pool size/finite timeout, nonnegative bounded overflow and positive process count. Validate optional deployment allocation against `(size + overflow) × processes`.
2. Reject reentry into an active `PostgresUnitOfWork` so its session cannot be replaced/leaked. Preserve sequential reuse and commit/rollback behavior.
3. Add `scripts/check_database_pool.py`: read actual PostgreSQL capacity/reserved slots, subtract explicit headroom, reject unsafe planned demand, and optionally run read-only synthetic DB units with audio waits outside sessions.
4. Verify real PostgreSQL pressure, all-waiting release, bounded exhaustion/recovery and cancellation, then synchronize configuration/operator/status guidance.

Default engine settings stay 5 retained + 10 overflow with a 5-second checkout wait. Count API replicas, one recording worker, dispatcher and each Dramatiq process; threads in one process share its engine. Budget is optional for compatibility, so operators must set/check their real allocation before scaling. Mixed configurations require summing each pool's peak rather than one shared multiplier.

The modeled turns, safety events, costs and audit records have repository round-trip coverage. That does not claim all live events are collected: durable milestones/safety audit are L2/L7, booking integration L3, complete metering/privacy jobs L8/L11. The current OpenAI spend guard is a protected append-only disk ledger, not Redis; preserve its fail-closed behavior until durable metering is integrated.

## Section 3 verification evidence

Section 3 verified on 2026-10-04 against parent revision `60ad7304f4859f2714a59d9f604545f364760a9b` plus the section 3 increment (historical verification before section 4). Locked dependencies unchanged; installed Python 3.12.12, SQLAlchemy 2.0.54 and asyncpg 0.31.0. Local native PostgreSQL/disposable Redis and explicit tokenizer data were used. Record the full-level demo/reviewer result when all remaining sections are implemented.

| Check | Actual outcome |
|---|---|
| Complete offline suite | **1,591 passed, 23 warnings, 52.36 seconds; no skips** |
| Focused configuration/session suite before CLI cases | **47 passed** |
| Focused pool/migration/repository/booking tests | **18 passed** |
| CLI unsafe-preflight/privacy regressions | **2 passed**, also included in final full suite |
| Offline conversation evaluation | **10/10 passed, zero findings; filter canaries pass** |
| Lint of all changed Python files | **Pass** |
| Whole-repository lint | **3 pre-existing I001 findings** in conversation state, model base and PostgreSQL repositories |

Reproduce the offline suite after preparing the locked environment/tokenizer/native services described in the [runbook](../runbook.md):

```bash
NLTK_DATA=/private/tmp/voice-agent-tokenizer PYTHONDONTWRITEBYTECODE=1 \
    .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache roma tests scripts
.venv/bin/python scripts/run_eval.py
```

The tokenizer path identifies this run's temporary setup; use the approved data directory on another machine. Repository-wide lint currently exits nonzero for the listed existing findings; tests/evaluation pass. The touched configuration/database import blocks were normalized.

Operator CLI measurements used a fresh disposable PostgreSQL server per measurement session on macOS 26.5.2 arm64. Server maximum was 100 with 3 reserved slots and 10 explicit other-client/operational headroom, leaving 87 available. The declared deployment had four same-sized engine-owning processes and budget 60; **only one process was load-tested**. Each profile ran 100 simulated calls × three `SELECT 1` units, with 10 ms simulated audio waits after session closure. No business rows, migrations, provider traffic or real audio were involved.

| Size + overflow | Planned four-process peak | Observed one-process peak | Completed units | P50/P95 unit ms | Timeouts/errors | Checked out after |
|---|---|---|---|---|---|---|
| 2 + 0 | 8 | 2 | 300 | 18.393 / 35.258 | 0 / 0 | 0 |
| 2 + 1 | 12 | 3 | 300 | 14.807 / 33.012 | 0 / 0 | 0 |
| 5 + 10 | 60 | 15 | 300 | 18.671 / 40.554 | 0 / 0 | 0 |

A separate unsafe-headroom CLI run returned exit 1 before load began. Real tests also prove all 100 repository units return their connections before simulated audio resumes, one held connection causes a bounded domain availability error in a one-connection pool, release restores operation, and cancellation returns its checkout. Existing schema upgrade/downgrade, durable-record round trips and the 100-attempt same-slot race passed.

These cold-start synthetic read measurements do not select an optimal pool size or prove production audio/write latency, deployment throughput, live event collection, clean-machine setup or a full level gate. Retain current defaults. Before deployment/scaling, set the actual process topology/allocation and run capacity preflight against the intended database; benchmark representative writes and voice traffic separately. Other sections 7–9, Level 0 gaps and full-level reviewer/demo remain pending.
Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

## Section 4 relational design and implementation

The requested design extends the current schema rather than replacing old definitions. The chosen approach adds four missing tables and one nullable column through migration `20261004_0003`; alternate choices were documenting missing entities only or recreating the whole schema. Additive migration gives tested compatibility while making the requested relational foundations concrete.

- `conversation_states`: one current checkpoint per call, call-owned deletion, positive revision/schema, canonical stage/policy identity, object payload and required expiry.
- `model_registry`: provider/name/version/task identity, optional artifact/checksum/license metadata, active-candidate lookup and deletion protection while measured results reference it.
- `benchmark_runs`: unique execution key, corpus/version/config/environment identity, validated status/timestamps/sample counts and optional evidence expiry.
- `benchmark_results`: run/model FKs, unique run/model/language/metric, finite Decimal value, explicit unit/sample count and object metadata. Run deletion cascades results; measured model deletion is restricted.
- `call_turns.language`: nullable observation metadata, propagated through the existing plain record/repository adapter. No guessed historical backfill or reply-language change.

Implementation sequence: define models/compatibility field; generate/review additive migration against the prior schema; verify keys/checks/indexes/cascades/orphans/duplicate rejection and old-row preservation; synchronize the [schema catalog](../20-relational-schema.md), operator commands and later-level consumers. Existing `rule` remains the safety identifier; knowledge tables/pgvector stay L9. No old keys/constraints/indexes are removed or renamed and both applied migration files remain byte-identical.

This implements relational foundations, not live durable restoration, checkpoint compare-and-swap, a model loader/benchmark runner, login/RBAC or retention cleanup. Those consumers remain later gates. The catalog lists all **25 tables, 123 named constraints and 28 explicit indexes**, explaining identity/query/deletion/retention choices; primary/unique constraints also provide implicit indexes.

## Section 4 verification evidence

Verified 2026-10-04 against parent revision `60ad7304f4859f2714a59d9f604545f364760a9b` plus uncommitted section 3/4 increments. Dependencies/tokenizer setup and native disposable-service environment are unchanged from section 3. Tests did not use the configured working database or make provider calls.

| Check | Actual outcome |
|---|---|
| Complete offline suite | **1,639 passed, 23 warnings, 60.18 seconds; no skips** |
| Schema metadata/key/FK/index/record contracts | **116 passed** |
| Focused new relational + existing migration/repository/booking tests | **46 passed** |
| Offline conversation evaluation | **10/10 passed, zero findings; filter canaries pass** |
| Changed Python files and new migration lint | **Pass** |
| Whole-repository lint | **2 existing I001 findings** in conversation state and model base; touched repository imports normalized |
| Prior migration hashes | Both old migration files byte-identical |

The suite verifies every modeled table/constraint/index against an empty-database migration and Alembic metadata parity. New behavioral cases cover checkpoint/result/model/run duplicates, orphan references, invalid stage/shape/expiry/run lifecycle, NaN rejection, signed finite metrics, owned-data cascades, measured-model deletion protection and optional turn-language round trips. A separate prior-head fixture inserts an old call/turn, upgrades, checks NULL language/data preservation, downgrades to `20260930_0002`, checks old data again, and re-upgrades successfully. `alembic check` reports no pending metadata operations.

Reproduction uses the full offline commands recorded above. The targeted PostgreSQL command is:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    tests/repositories/postgres/test_relational_extensions.py \
    tests/repositories/postgres/test_migrations.py \
    tests/repositories/postgres/test_repositories.py \
    tests/repositories/postgres/test_appointment_concurrency.py
```

The new migration is ready for the intended environment and is not automatically applied to the working database. Upgrade before code uses the new turn-language column. Downgrade destroys new checkpoint/benchmark records and language metadata while preserving legacy records; coordinate backup/code compatibility before any real rollback. Fixture expiry durations are test data, not approved retention policy. Section 5 workflow results follow below; sections 7–9, Level 0 gaps and the full Level 1 gate remain pending.

## Section 5 migration workflow and separate seeds

Preserve the three existing revisions and single `20261004_0003` head. The roadmap's numbered examples describe ordered changes; rewriting the implemented history would risk deployed databases. This increment adds no schema revision and changes no table/index definition. All three revision files remain byte-identical.

`MigrationSettings` loads only the database URL and environment, normalizes PostgreSQL URLs, hides invalid secret inputs and avoids unrelated AI/carrier/Redis requirements. Alembic keeps disposable migration connections outside application pools and disposes them after failure. The [migration guide](../21-database-migrations.md) owns forward-generation/review commands, metadata checks, index handling and explicit per-revision rollback/data-loss guidance.

`scripts/seed_demo.py --demo` is separate from migrations and requires `APP_ENV=dev`/`test` plus the current schema head. It inserts seven synthetic reference rows atomically with stable identities, preserves existing roles/names, handles concurrent repeats and rejects conflicting identities. Demo institute/branch/course records are inactive; no accounts, callers, appointments or production facts are invented. Schema upgrades remain seed-free. Real reference imports remain owner-approved independently.

Implementation sequence: isolate database command configuration; preserve/review existing upgrade/downgrade/index history; add bounded separate opt-in seeding; verify lifecycle, concurrency, privacy and rollback; synchronize current scope/evidence and operator guidance.

## Section 5 verification evidence

Verified 2026-10-04 against parent revision `60ad7304f4859f2714a59d9f604545f364760a9b` plus the uncommitted sections 3–5 increments, with locked dependencies unchanged (SQLAlchemy 2.0.54, asyncpg 0.31.0, Alembic 1.20.0). Tests use disposable native PostgreSQL/Redis and make no paid provider calls. The configured working database is not migrated or seeded.

| Check | Actual outcome |
|---|---|
| Complete offline suite | **1,657 passed, 23 warnings, 62.67 seconds; no failures or skips** |
| New section 5 test cases | **18 passed** in the complete suite |
| Actual demo CLI on fresh disposable PostgreSQL | **7 inserted**, then **0 inserted**; rollback/re-upgrade retains reference data and repeat remains 0 |
| Initial focused migration/settings/seed checks | **15 passed** before three additional CLI/atomic rollback cases |
| Offline conversation evaluation | **10/10 passed, zero findings; filter canaries pass** |
| Changed Python files | **25 files lint pass**, including cumulative sections 3–5 changes |
| Whole-repository lint | **2 existing I001 import-format findings** in conversation state and model base |
| History/source identity | All three revision files and retained v4 document byte-identical |
| Documentation links/fences | **60 non-archive Markdown files, 262 local links; zero issues** |

The initial full run passed 1,647 tests but hit ten setup errors when new session-scoped disposable servers exceeded macOS shared-memory IDs. The diagnostic run confirmed PostgreSQL `shmget` exhaustion. The new fixtures now release servers after their module, reusing existing Docker/native provisioning; the final complete run above passes without server configuration changes or application changes for that issue.

Tests cover single-head ordered history and offline SQL/index review; database-only `.env`/environment configuration and secret-safe validation; upgrade/check/downgrade/re-upgrade without voice keys; seed-free migration; seven-row first seed and zero-row repeat; existing role/name preservation; concurrent seed deduplication; early namespace and late role conflict rollback; mismatched-head refusal; explicit CLI opt-in and non-development refusal before database access. Existing schema parity and legacy-populated rollback tests remain passing.

Reproduce with the full offline suite recorded above, or target the increment:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    tests/core/test_migration_settings.py tests/core/test_demo_seed_cli.py \
    tests/repositories/postgres/test_demo_seeds.py \
    tests/repositories/postgres/test_migration_workflow.py \
    tests/repositories/postgres/test_migrations.py \
    tests/repositories/postgres/test_relational_extensions.py
```

Section 5 is complete for this bounded increment; sections 7–9, Level 0 prerequisites, deployment backup/restore timing and the full Level 1 gate remain pending.

## Section 6 backend architecture boundaries

Preserve `roma/` as the actual backend application package. The v4 `backend/app`
tree maps to existing packages rather than authorizing a broad rename:
`roma/main.py` composes the app, `roma/api/v1` owns HTTP adapters,
`roma/core` owns settings/logging/database/shared privacy helpers,
`roma/domain` owns business rules and protocols, `roma/repositories` owns
PostgreSQL/Redis adapters, `roma/services` owns use cases, `roma/providers`
owns SDK adapters, `roma/realtime` owns the current voice pipeline and
`roma/workers` owns retryable background effects. Future `roma/rag`,
`knowledge/`, `benchmarks/` and static config directories arrive with their
own later sections/levels when they have real consumers.

The useful Level 6 change is enforceable dependency direction. Domain code must
not import FastAPI, provider SDKs, SQLAlchemy, Redis clients, concrete
repositories, realtime processors or workers. API handlers stay as transport
mapping; business rules stay in domain/services; repositories and providers are
adapters; workers are asynchronous execution boundaries.

Two concrete leaks were removed:

- `roma.domain.conversation` no longer re-exports `RedisCallStateStore`. It now
  exposes the checkpoint protocol plus an in-memory adapter for tests/offline
  runs; the realtime composition imports the Redis adapter from
  `roma.repositories.redis.conversation_state`.
- `roma.domain.costs.spend` no longer imports post-call worker path helpers.
  Owner-only file creation/opening now lives in `roma.core.private_files`, while
  `roma.workers.postcall.paths` remains the owner of post-call path names and
  re-exports the helpers for compatibility.

`tests/architecture/test_dependency_direction.py` locks the domain-layer rule
so future work cannot accidentally pull transport/storage/provider concerns
back into business modules. This section does not implement provider contract
mocks, public `/api/v1` schemas, pre-commit/CI, type checking or native
one-command startup; those are section 7-9/full-gate work.

## Section 6 verification evidence

Verified 2026-10-04 after upgrading the configured `roma` database to head
(`20261004_0003`). The database upgrade is an operator action, not a code
change; no demo rows were seeded.

| Check | Actual outcome |
|---|---|
| Focused architecture/domain regression suite | **31 passed**, one pytest cache write warning from the restricted workspace |
| Changed Python file lint | **Pass** after import ordering fix |
| Live `roma` database schema | Alembic current **`20261004_0003 (head)`**; `alembic check` reports **No new upgrade operations detected** |
| Live `roma` database inventory | **25** expected business tables, **74** indexes, **291** constraints, no missing/extra expected tables, all inspected business tables have **0** rows |

Focused command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
    tests/architecture/test_dependency_direction.py \
    tests/domain/conversation/test_state_machine_api.py \
    tests/domain/costs/test_spend.py
.venv/bin/ruff check --no-cache \
    roma/core/private_files.py roma/domain/conversation/__init__.py \
    roma/domain/conversation/state_machine.py roma/domain/costs/spend.py \
    roma/realtime/pipeline.py roma/workers/postcall/paths.py \
    tests/architecture/test_dependency_direction.py
```

Run the complete offline suite before claiming the full Level 1 gate. Existing
whole-repository lint debt remains as recorded in [verification](../12-verification.md).

Return to the [documentation index](../README.md).
