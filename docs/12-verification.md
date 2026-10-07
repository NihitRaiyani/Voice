# Verification and acceptance evidence

Latest complete-suite verification after the [Level 2 section 11 increment](levels/level-02.md): **1,705 tests passed, 23 warnings, no failures or skips**. The Level 1 `make ci` skeleton includes native-service startup and migrations. For section 11, fresh Ruff and mypy checks (21 source files) passed, and `make test-ci` passed **59 provider/config tests** and **21 mocked API/architecture/migration-settings/text/language tests**. Pre-commit uses the same lint/type/provider checks; see the level brief for the latest invocation.

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

Section 10 adds the Level 2 state-machine facade and durable latest-checkpoint adapter. Focused verification covers the public stage API, transition/objection/interruption/missing-information helpers, `route_intent()` classification, PostgreSQL latest-checkpoint revision increments and compatible `PostgresConversationStateStore` save/restore by provider call ID. Section 11 adds direct Transformers plus the owner-authorized pinned Qwen3-1.7B INT4 Apple MLX/text/language lab. Both adapters share the provider contract and controller/safety boundaries. Offline tests cover runtime selection, cache/revision/token boundaries, pre-download memory admission, streaming/token timing, timeout/cancellation cleanup, validated extraction, deterministic final safety and native scoring rejection. Console/lab mock smoke and real tiny-random Qwen3 Transformers SDK smoke pass; the real 8B admission check refuses this 8 GiB host before loading. This does not pass real 8B inference, native review, safety-audit, redial/session or L7 recovery gates. See [the local inference guide](25-local-transformers-llm.md).

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

The pinned Apple INT4 candidate has actual trained generation, synthetic controller/console and 40-case output evidence in [Level 2](levels/level-02.md#mac-development-candidate-verification). Native scores remain blank; wrong-language/task samples and long-prompt latency prevent production promotion. Stale captured prompts cannot reuse native scores.
