# Verification and acceptance evidence

Latest verification for the [Level 1 sections 3–5 increments](levels/level-01.md): **1,657 tests passed, 23 warnings, no failures or skips**, including disposable PostgreSQL/Redis integration, and **10/10 offline evaluations** with zero findings. All changed Python files pass lint; the full repository retains **two existing I001 import-format findings** in `roma/domain/conversation/state.py` and `roma/repositories/postgres/models/base.py`.

The earlier [Level 0 baseline report](roadmaps/level-00-baseline.md) remains historical evidence of 1,571 passing tests and five lint findings before this increment. Its test/identity record is not rewritten as current implementation evidence. L1 records capacity/pressure/timeout/cancellation and relational constraint/migration/legacy-compatibility and separate-seed workflow results and their limits; the full Level 1 gate remains pending.

Tests needed explicit tokenizer data and permission for disposable local database shared memory. Passing after those setup corrections does not prove clean-machine reproduction, local-model quality, live latency or production readiness. Level 0 remains pending prerequisite review, reproducible setup and representative P50/P95 timing.

## Commands and environment

```bash
uv run --no-sync pytest -q
uv run --no-sync ruff check roma tests scripts
uv run --no-sync python scripts/run_eval.py
```

Use the locked telephony/dev/workers environment, NLTK sentence data and disposable PostgreSQL/Redis prerequisites in the [runbook](runbook.md). Report skips/errors explicitly. Existing async tests use `asyncio.run`; dial tests pin the clock/window. No paid AI or carrier calls occur by default.

Section 4 metadata contracts passed 116 tests; focused relational/migration/repository/booking checks passed 46 tests. The complete suite includes these and Alembic schema parity; working-database migration and later restore/benchmark/cleanup features are not implied by those results. See the [relational catalog](20-relational-schema.md).

Section 5 adds 18 tests for database-only migration settings, ordered/offline schema/index review, database-only upgrade/check/rollback/re-upgrade and separate atomic/repeatable/concurrent seed behavior, preservation, head/environment guards and CLI privacy/opt-in. The final full suite passed in 62.67 seconds after new module-owned PostgreSQL fixtures were shortened to module scope; session-long extra servers had exhausted macOS shared-memory IDs in the initial run (1,647 passed, ten setup errors). No application or PostgreSQL server setting was changed to resolve that test-resource issue. All three revision files and retained v4 source bytes remain unchanged. An end-to-end demo CLI run on fresh disposable PostgreSQL inserted seven rows, then zero on repeat; reference rows survived downgrade/re-upgrade and a third invocation inserted zero. Documentation verification covered 60 non-archive Markdown files and 262 local links with zero issues; all 25 changed Python files pass lint. See the [migration guide](21-database-migrations.md).

## Evidence rules

Each level records revision, environment/data/model/prompt/document identity, reproducible command, actual outcome, demo/review and limitations. Distinguish source presence, offline verification, real local inference, authorized live verification and full gate pass. Reused foundations close individual checks, not whole levels automatically.

| Gate | Required evidence |
|---|---|
| L1 | Empty DB migrations, native startup, provider contracts/mocks, versioned API, reviewed pool budget and green CI |
| L2/L3 | Durable text restore, actual local inference/safety audit, committed live booking, 100-attempt race and cancellation/idempotency |
| L4–6 | Approved language/noise WER/CER/RTF, TTS intelligibility/first audio and complete local voice lab |
| L7 | Barge-in/double cancellation, late results/disconnect, Redis/provider loss, bounded pressure and event-loop lag |
| L8 | Roles/token lifecycle, forged/stale/duplicate callback policy, job crash/replay/retention and PII/access approvals |
| L9 | Eligible scoped/versioned evidence, local embeddings, golden Recall@K/MRR, grounding/deferral and final safety |
| L10 | Authorized local-model carrier call, serving/readiness/GPU admission, retrieval cancellation and zero external GenAI traffic |
| L11/L12 | Real telemetry/reconciled analytics, mock versus GPU capacity, chaos/security/golden regressions and measured SLOs |
| L13 | Clean-host startup/migrations/readiness, backup/restore and compatible rollback |

## Benchmark and documentation checks

Record sample count, hardware/concurrency, codec/noise/language mix, approved dataset, policy/model/document versions and warm/cold state. Measure user-stop-to-first-audio directly and report relevant P50/P95/P99, failures/fallbacks/queue wait/resource saturation. Do not add stage percentiles or promise roadmap example SLOs.

Documentation maintenance checks links/fences, stale carrier/package/roadmap references, level/status consistency, scoped runtime/config changes, preserved prompt/audio assets and original Word-file identity. Inspect the diff and preserve pre-existing user edits. Offline evidence distinguishes the historical baseline from each requested implementation increment; documentation edits do not require paid/live checks.
