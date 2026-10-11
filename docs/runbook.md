# Operating guide for Voice_Agent

Commands describe the existing comparison backend and the Level 1 native foundation path. Local-model/container-deployment commands will be added only when implemented. No live calls or provider spending are part of documentation maintenance.

## Environment and local services

Use the locked project environment with telephony/dev/workers extras, or let the Makefile run the same locked tooling:

```bash
make sync
```

Use `.env.example` as the settings reference; do not overwrite an existing `.env`. Configure local PostgreSQL and Redis endpoints, Twilio/Sarvam/OpenAI settings as needed by the selected baseline, `API_TOKEN`, public HTTPS base and keyed caller identity. Do not print or commit their values.

For the v4 foundation use native PostgreSQL/Redis services, each bound to the intended local/private interface with persistent data outside source. `DATABASE_URL` uses `postgresql+asyncpg://`; `REDIS_URL` matches the configured private Redis service. Use the installed service's administrator tools to create a dedicated development database/user; credentials are local secrets.

Existing `compose.yaml` starts PostgreSQL/Redis as pre-v4 compatibility tooling. It is optional for restoring that baseline, not the Level 1 learning requirement or a completed Level 13 deployment.

For the Level 1 native path, install PostgreSQL and Redis with the teacher-approved system package manager first, then use the Makefile. It creates project-local service state under ignored `var/dev-services/` and uses only local development credentials:

```bash
make services-up
make db-upgrade
make app-start
```

`make app-start` is the one-command development startup: it starts native PostgreSQL/Redis if needed, applies Alembic migrations and runs `roma.main:create_app` on `127.0.0.1:8020`. Override `HOST`/`PORT` for a local smoke check. Stop local services with `make services-down`. The older `scripts/start_roma.sh` remains the live-call/tunnel helper and still requires real carrier settings.

Apply existing migrations to the intended development database before answer-webhook testing. Database commands need only `DATABASE_URL` from the environment or `.env`, without voice-provider/Redis credentials. Follow the [migration guide](21-database-migrations.md) for forward changes, SQL review and revision-specific rollback:

```bash
make db-upgrade
```

Latest head is `20261004_0003`; it adds four checkpoint/model/benchmark tables and nullable turn-language metadata. Applied migrations `20260920_0001` and `20260930_0002` remain unchanged. Seed/demo data now has a [separate opt-in command](21-database-migrations.md#seeds-stay-outside-schema-history): `APP_ENV=dev python scripts/seed_demo.py --demo`. It requires head, preserves existing data, inserts only inactive synthetic offerings plus role vocabulary, and creates no accounts. Keep schema upgrades seed-free. Do not downgrade a working database merely to demonstrate rollback; tests use disposable databases.

The new migration is verified on empty and legacy-populated disposable databases, and the configured `roma` database was upgraded to `20261004_0003 (head)` on 2026-10-04. Run `alembic upgrade head` in every other intended environment before starting code that queries turn-language metadata, and use `alembic current`/`alembic check` to verify its state. Existing rows retain NULL language; schema migrations seed no historical guesses or sample records; the separate demo command is explicit.

A rollback to `20260930_0002` drops the four new tables and the new language column, destroying only data introduced through this increment. Export/backup any new evidence/checkpoints and coordinate compatible code before a real rollback. Tests exercise downgrade/re-upgrade only on disposable data; existing calls/turns survive both directions. [Schema rationale and retention](20-relational-schema.md) define what future producers may store. Knowledge tables/extensions remain Level 9.

## Database capacity and pool pressure

Keep the engine defaults unless measurement warrants tuning. These new non-secret settings describe the entire deployment sharing this database:

```dotenv
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT_SECS=5.0
DATABASE_POOL_PROCESSES=4
DATABASE_CONNECTION_BUDGET=60
```

This example counts one API process, one recording worker, one dispatcher and one Dramatiq process: retained capacity 20, peak 60. Four Dramatiq threads share its process pool. Scale replicas/processes by updating the count on every process; configuration cannot discover or enforce a cluster-wide semaphore. Mixed pool configurations require summing each group's peak and allocating independently. Positive pool size/finite timeout and nonnegative overflow are enforced; budget mismatch fails settings validation before engine use. An omitted budget retains compatibility but is not production sizing approval.

Check the actual server before starting/scaling:

```bash
uv run --no-sync python scripts/check_database_pool.py --headroom 10
```

The command reads `max_connections`, `superuser_reserved_connections` and `reserved_connections` (zero if unavailable), subtracts headroom for other clients/operations, and exits nonzero when planned peak does not fit. Increase `--headroom` to cover all other applications, migration/inspection tools and operational margin; its default 10 is an example, not observed demand. It prints only non-secret sizing data and does not write business rows. It uses existing environment settings; offline setup can supply unused cloud-key placeholders because this tool invokes no AI/carrier providers.

Run synthetic pressure on a dedicated development/disposable database:

```bash
uv run --no-sync python scripts/check_database_pool.py --headroom 10 \
    --run-load --concurrency 100 --units-per-call 3 --audio-wait-ms 10
```

Each simulated call opens a short session/transaction, runs `SELECT 1`, commits/closes, then waits outside the session. Output includes completed units, timeouts/errors, peak/final checked-out connections and P50/P95 unit latency (checkout + query + commit/close). Capacity preflight must pass before load runs. This measures one process and simulated media waits; benchmark representative writes/real audio and the full process topology before claiming production capacity. Do not increase pools solely to hide long transactions. Consider PgBouncer when measured process fan-out needs it; validate asyncpg prepared statements and pooling mode first.

A live call has many independent units of work. Do not pass an ORM session into the media pipeline, await inference/audio/Redis or make external provider calls inside a unit of work. Use plain domain records after exit. Existing repository writes require explicit commit; omitted commit, exceptions and cancellation rollback/close, and active nested reuse is rejected.

## Backend and private diagnostics

```bash
uv run --extra telephony uvicorn roma.main:create_app --factory --host 127.0.0.1 --port 8020
```

`roma.main` configures logging, database/service lifecycle and REST hang-up. It rejects an unreachable localhost public base because Twilio reaches `/answer` and `/ws` externally. The development `scripts/serve_media:create_app` adds debug routes using the same production composition. Those routes may reveal transcript/turn diagnostics and must stay private.

`GET /health` is process health, not evidence that models, carrier credits, migrations or GPU capacity are ready. `/answer` requires migrated PostgreSQL and returns 503 when durable acceptance is unavailable.

`scripts/start_roma.sh` restarts Redis/tunnel/backend processes and rewrites the temporary public URL in `.env`. It is an operational action, not an offline verification command. Tunnels are development tooling; production uses an owner-selected stable TLS origin. Protect access logs because outbound answer URLs currently contain a lead token query.

## Workers and inspection

Run these as separate processes after services/migrations/configuration are ready:

```bash
uv run --extra telephony --extra workers python scripts/run_postcall_worker.py
uv run --extra telephony --extra workers python scripts/dispatch_background_jobs.py
uv run --extra telephony --extra workers python scripts/run_background_worker.py
```

Use one recording consumer. Its `--queue=spool` path supports spool recovery and `--drain` processes existing messages. Dispatcher `--once` publishes one batch. Dramatiq defaults to one process/four threads and is stopped normally to settle active work.

Inspect PostgreSQL using a private authenticated SQL client:

```sql
SELECT job_type, status, attempts, available_at, last_error
FROM followup_jobs ORDER BY created_at DESC LIMIT 20;
```

Do not replay dead letters until the business effect/idempotency/privacy consequences are understood. `send_followup` remains ineligible; these processes do not authorize messaging.

## Offline checks

```bash
make ci
make test
uv run --no-sync python scripts/run_eval.py
```

Existing PostgreSQL tests try a disposable Testcontainers database, then installed native `initdb`/`pg_ctl` binaries (including `/Library/PostgreSQL/18/bin`). They never target the configured working database. Report unavailable-service skips/failures explicitly. Broker integration tests require a disposable local Redis server.

Sentence aggregation needs NLTK `punkt_tab` environment data; if missing, install it explicitly as setup and verify provenance before tests. Tests must not download resources implicitly. Current async tests use `asyncio.run`.

## Authorized carrier checks and recovery

Before a separately authorized test, verify destination/consent, all provider readiness/credits, migrated database, window/denylist/budget/cap, public TLS reachability, signature external URL and hang-up cleanup. Current ₹200 source ceiling is not a provider balance. `scripts/place_test_call.py` is for an approved verified handset, not production dialing clearance.

Recovery drills at L8–13 cover GPU OOM/restart/admission, Redis reconstruction from durable checkpoints, PostgreSQL restore/PITR, safe job replay, immediate knowledge-version deactivation/cache exclusion and compatible API/worker/model rollback. Define RTO/RPO, stop new intake, preserve queue/spool/database state and record observed recovery evidence. These are future acceptance procedures, not guarantees of the present code.

See [verification](12-verification.md), [security](07-security.md), [webhooks](18-webhook-idempotency.md), [jobs](19-background-task-framework.md) and [decisions](decisions.md).
