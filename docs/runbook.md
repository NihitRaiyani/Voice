# Operating guide for Voice_Agent

Commands describe the existing comparison backend. Local-model/versioned-API/container-deployment commands will be added only when implemented. No live calls or provider spending are part of documentation maintenance.

## Environment and local services

Use the locked project environment with telephony/dev/workers extras:

```bash
uv sync --locked --extra telephony --extra dev --extra workers
```

Use `.env.example` as the settings reference; do not overwrite an existing `.env`. Configure local PostgreSQL and Redis endpoints, Twilio/Sarvam/OpenAI settings as needed by the selected baseline, `API_TOKEN`, public HTTPS base and keyed caller identity. Do not print or commit their values.

For the v4 foundation use native PostgreSQL/Redis services, each bound to the intended local/private interface with persistent data outside source. `DATABASE_URL` uses `postgresql+asyncpg://`; `REDIS_URL` matches the configured private Redis service. Use the installed service's administrator tools to create a dedicated development database/user; credentials are local secrets.

Existing `compose.yaml` starts PostgreSQL/Redis as pre-v4 compatibility tooling. It is optional for restoring that baseline, not the Level 1 learning requirement or a completed Level 13 deployment. The current startup helper does not start PostgreSQL, so migrate/start it separately. A complete native one-command startup remains an L1 deliverable.

Apply existing migrations to the development database before answer-webhook testing:

```bash
uv run --no-sync alembic upgrade head
```

Latest head is `20260930_0002`. Keep seed/demo data separate. Do not downgrade a working database merely to demonstrate rollback; tests use disposable databases.

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
uv run --no-sync pytest -q
uv run --no-sync ruff check roma tests scripts
uv run --no-sync python scripts/run_eval.py
```

Existing PostgreSQL tests try a disposable Testcontainers database, then installed native `initdb`/`pg_ctl` binaries (including `/Library/PostgreSQL/18/bin`). They never target the configured working database. Report unavailable-service skips/failures explicitly. Broker integration tests require a disposable local Redis server.

Sentence aggregation needs NLTK `punkt_tab` environment data; if missing, install it explicitly as setup and verify provenance before tests. Tests must not download resources implicitly. Current async tests use `asyncio.run`.

## Authorized carrier checks and recovery

Before a separately authorized test, verify destination/consent, all provider readiness/credits, migrated database, window/denylist/budget/cap, public TLS reachability, signature external URL and hang-up cleanup. Current ₹200 source ceiling is not a provider balance. `scripts/place_test_call.py` is for an approved verified handset, not production dialing clearance.

Recovery drills at L8–13 cover GPU OOM/restart/admission, Redis reconstruction from durable checkpoints, PostgreSQL restore/PITR, safe job replay, immediate knowledge-version deactivation/cache exclusion and compatible API/worker/model rollback. Define RTO/RPO, stop new intake, preserve queue/spool/database state and record observed recovery evidence. These are future acceptance procedures, not guarantees of the present code.

See [verification](12-verification.md), [security](07-security.md), [webhooks](18-webhook-idempotency.md), [jobs](19-background-task-framework.md) and [decisions](decisions.md).
