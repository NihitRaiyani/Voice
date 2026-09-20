# PostgreSQL System of Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-shaped PostgreSQL system of record for Roma's organization, callers,
calls, appointments, safety, usage, cost, recording, job, identity, and audit data while preserving
Redis as transient live-call infrastructure.

**Architecture:** Domain/application code depends on plain repository protocols and a unit of work.
Async SQLAlchemy adapters live under `roma/repositories/postgres`; Alembic owns schema evolution;
PostgreSQL integration tests prove database-specific constraints. No durable write is added to the
audio-frame hot path, and existing Redis adapters remain unchanged.

**Tech Stack:** Python 3.12, the locally available supported PostgreSQL version, SQLAlchemy 2.x
async ORM, asyncpg, Alembic, Pydantic Settings, pytest, Ruff, and Docker Compose only when local
database test infrastructure is otherwise unavailable.

---

## File map

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | Runtime/test database dependencies |
| `.env.example`, `roma/core/config.py` | Secret-safe database and PII hashing settings |
| `roma/core/database.py` | Async engine/session construction and lifecycle |
| `roma/domain/persistence.py` | Plain commands/results and stable persistence errors |
| `roma/repositories/interfaces/durable.py` | Repository and unit-of-work protocols |
| `roma/repositories/postgres/models/*` | SQLAlchemy schema grouped by domain |
| `roma/repositories/postgres/repositories.py` | Async repository implementations |
| `roma/repositories/postgres/unit_of_work.py` | Transaction boundary and error translation |
| `alembic.ini`, `migrations/*` | Versioned schema from zero |
| `compose.yaml` | Local PostgreSQL and Redis dependencies, not application deployment |
| `scripts/seed_reference_data.py` | Explicit idempotent reference seed entrypoint |
| `tests/repositories/postgres/*` | Metadata, migration, repository, rollback, and concurrency proof |
| `docs/*`, `README.md`, `CLAUDE.md` | Implemented-state and operator/learning guidance |

### Task 1: Database dependencies and configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `roma/core/config.py`
- Modify locally only: `.env`
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests asserting an async PostgreSQL URL is exposed without leaking its secret and that the
PII hash key is a `SecretStr`:

```python
def test_database_configuration_is_secret(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://roma:pw@localhost/roma")
    monkeypatch.setenv("PII_HASH_KEY", "test-pepper-with-at-least-32-bytes")
    settings = _settings()
    assert isinstance(settings.database_url, SecretStr)
    assert "pw" not in repr(settings.database_url)
    assert isinstance(settings.pii_hash_key, SecretStr)


def test_database_url_requires_asyncpg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://roma:pw@localhost/roma")
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        _settings()
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.venv/bin/pytest tests/core/test_config.py -q`  
Expected: failure because `database_url` and `pii_hash_key` do not exist.

- [ ] **Step 3: Add dependencies and settings**

Add runtime dependencies `sqlalchemy[asyncio]>=2.0,<3`, `asyncpg>=0.30,<1`, and
`alembic>=1.16,<2`; add `testcontainers[postgres]>=4.12,<5` to dev dependencies. Add:

```python
database_url: SecretStr = SecretStr("")
pii_hash_key: SecretStr = SecretStr("")
database_pool_size: int = 5
database_max_overflow: int = 10
database_pool_timeout_secs: float = 5.0
```

Validate non-empty database URLs begin with `postgresql+asyncpg://`. Document value-free variables
in `.env.example`; generate a local 32-byte hash key and add the local database URL to ignored
`.env` without printing either value.

- [ ] **Step 4: Lock and verify dependencies**

Run: `uv lock && uv sync --extra dev --extra telephony`  
Expected: lock succeeds and imports for `sqlalchemy`, `asyncpg`, and `alembic` succeed.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/core/test_config.py -q && .venv/bin/ruff check roma/core tests/core`  
Expected: all configuration tests and Ruff pass.

Commit: `build: add PostgreSQL dependencies and settings`

### Task 2: Plain persistence contracts and PII hashing

**Files:**
- Create: `roma/domain/persistence.py`
- Create: `roma/repositories/interfaces/__init__.py`
- Create: `roma/repositories/interfaces/durable.py`
- Test: `tests/domain/test_persistence.py`

- [ ] **Step 1: Write failing domain tests**

Test canonical hashing, immutable command records, error hierarchy, and protocol availability:

```python
def test_phone_hash_is_stable_but_never_contains_phone():
    digest = hash_phone_e164("+919876543210", "x" * 32)
    assert digest == hash_phone_e164("+919876543210", "x" * 32)
    assert len(digest) == 64
    assert "9876543210" not in digest


def test_phone_hash_changes_with_pepper():
    assert hash_phone_e164("+919876543210", "a" * 32) != hash_phone_e164(
        "+919876543210", "b" * 32
    )
```

- [ ] **Step 2: Confirm red state**

Run: `.venv/bin/pytest tests/domain/test_persistence.py -q`  
Expected: import failure for `roma.domain.persistence`.

- [ ] **Step 3: Implement domain records and errors**

Define frozen, slotted dataclasses `CallerRecord`, `CallRecord`, `CallTurnRecord`,
`AppointmentRecord`, `SafetyEventRecord`, `ProviderUsageRecord`, `CallCostRecord`,
`RecordingRecord`, `FollowupJobRecord`, and `AuditLogRecord`. Define:

```python
class PersistenceError(RuntimeError): ...
class PersistenceUnavailable(PersistenceError): ...
class PersistenceConflict(PersistenceError): ...
class RecordNotFound(PersistenceError): ...

def hash_phone_e164(phone: str, pepper: str) -> str:
    if not phone.startswith("+") or not phone[1:].isdigit():
        raise ValueError("phone must be canonical E.164")
    if len(pepper.encode()) < 32:
        raise ValueError("PII hash key must be at least 32 bytes")
    return hmac.new(pepper.encode(), phone.encode(), hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Define narrow repository protocols**

Define async protocols `CallerRepository`, `CallRepository`, `AppointmentRepository`,
`EvidenceRepository`, `ReferenceDataRepository`, and `DurableUnitOfWork`. Repository methods accept
the domain dataclasses/primitive IDs; no method exposes `AsyncSession` or an ORM model. The unit of
work exposes repositories plus `commit()` and `rollback()` and supports `async with`.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/pytest tests/domain/test_persistence.py -q && .venv/bin/ruff check roma/domain roma/repositories/interfaces tests/domain`  
Expected: tests and Ruff pass.

Commit: `feat: define durable persistence contracts`

### Task 3: SQLAlchemy schema metadata

**Files:**
- Create: `roma/repositories/postgres/__init__.py`
- Create: `roma/repositories/postgres/models/__init__.py`
- Create: `roma/repositories/postgres/models/base.py`
- Create: `roma/repositories/postgres/models/organization.py`
- Create: `roma/repositories/postgres/models/identity.py`
- Create: `roma/repositories/postgres/models/calls.py`
- Create: `roma/repositories/postgres/models/appointments.py`
- Create: `roma/repositories/postgres/models/operations.py`
- Test: `tests/repositories/postgres/test_metadata.py`

- [ ] **Step 1: Write the failing metadata contract test**

Assert exact required tables:

```python
EXPECTED = {
    "callers", "institutes", "branches", "courses", "branch_courses",
    "counsellors", "calls", "call_turns", "call_events", "appointments",
    "appointment_slots", "safety_events", "provider_usage", "call_costs",
    "recordings", "followup_jobs", "users", "roles", "user_roles", "audit_logs",
}

def test_complete_relational_schema_is_registered():
    assert set(Base.metadata.tables) == EXPECTED
```

Also assert every primary/foreign key, named check/unique constraint, partial appointment index,
and delete action stated in the design specification.

- [ ] **Step 2: Confirm red state**

Run: `.venv/bin/pytest tests/repositories/postgres/test_metadata.py -q`  
Expected: import failure for PostgreSQL models.

- [ ] **Step 3: Implement shared SQLAlchemy conventions**

Create `Base(AsyncAttrs, DeclarativeBase)`, UUID and timestamp mixins, naming convention for stable
Alembic names, `utcnow()` server defaults, JSONB columns, timezone-aware timestamps, and
non-native string enums/check constraints.

- [ ] **Step 4: Implement organization and identity models**

Create `institutes`, `branches`, `courses`, `branch_courses`, `roles`, `users`, `user_roles`, and
`counsellors` exactly as the approved specification states. Use `CITEXT` only if the migration
installs it; otherwise enforce normalized lower-case email in the repository and use a unique
ordinary string column.

- [ ] **Step 5: Implement caller/call models**

Create `callers`, `calls`, `call_turns`, and `call_events`. Enforce non-negative latency/cost,
positive turn number, unique provider ID, unique `(call_id, turn_number)`, and idempotent events.

- [ ] **Step 6: Implement appointment models**

Create slots and appointments with a composite foreign key on branch/date/start, checks for
positive capacity and ordered times, and:

```python
Index(
    "uq_appointments_active_slot",
    "branch_id", "appointment_date", "start_time",
    unique=True,
    postgresql_where=text("status IN ('booked', 'confirmed')"),
)
```

- [ ] **Step 7: Implement operational ledger models**

Create `safety_events`, `provider_usage`, `call_costs`, `recordings`, `followup_jobs`, and
`audit_logs`, including idempotency, retention, runnable-job, resource, and time indexes from the
specification. Keep usage and cost as separate append-only facts.

- [ ] **Step 8: Verify and commit**

Run: `.venv/bin/pytest tests/repositories/postgres/test_metadata.py -q && .venv/bin/ruff check roma/repositories/postgres tests/repositories/postgres`  
Expected: metadata contract and Ruff pass.

Commit: `feat: model Roma relational schema`

### Task 4: Async database lifecycle and unit of work

**Files:**
- Create: `roma/core/database.py`
- Create: `roma/repositories/postgres/unit_of_work.py`
- Test: `tests/core/test_database.py`
- Test: `tests/repositories/postgres/test_unit_of_work.py`

- [ ] **Step 1: Write failing lifecycle and rollback tests**

Test that engine creation uses pool pre-ping and configured limits without exposing the URL. Test
that exiting an uncommitted unit of work rolls back and that `PersistenceConflict` and
`PersistenceUnavailable` are raised instead of SQLAlchemy/asyncpg exceptions.

- [ ] **Step 2: Confirm red state**

Run: `.venv/bin/pytest tests/core/test_database.py tests/repositories/postgres/test_unit_of_work.py -q`  
Expected: imports fail.

- [ ] **Step 3: Implement database lifecycle**

Provide:

```python
@dataclass
class Database:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database": ...

    async def close(self) -> None:
        await self.engine.dispose()
```

Use `pool_pre_ping=True`, configured pool bounds/timeouts, `expire_on_commit=False`, and no SQL
echo containing parameters.

- [ ] **Step 4: Implement unit-of-work behavior**

Open one session per context, construct repositories from that session, require explicit commit,
roll back on exception or uncommitted exit, and close exactly once. Translate `IntegrityError` to
`PersistenceConflict` and connectivity/timeout errors to `PersistenceUnavailable`, preserving the
original exception as the cause.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/pytest tests/core/test_database.py tests/repositories/postgres/test_unit_of_work.py -q && .venv/bin/ruff check roma/core roma/repositories/postgres tests/core tests/repositories/postgres`  
Expected: focused tests and Ruff pass.

Commit: `feat: add async PostgreSQL unit of work`

### Task 5: Alembic migration from zero

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/20260920_0001_initial_system_of_record.py`
- Test: `tests/repositories/postgres/test_migrations.py`

- [ ] **Step 1: Write a migration integration test**

Start a disposable PostgreSQL instance matching the supported local version, inject its sync URL
into Alembic, run `upgrade head`,
inspect all expected tables/constraints/indexes, run `downgrade base`, and assert application tables
are gone. Mark the test `postgres` but do not silently substitute SQLite.

- [ ] **Step 2: Confirm red state**

Run: `.venv/bin/pytest tests/repositories/postgres/test_migrations.py -q`  
Expected: failure because Alembic configuration/migration does not exist.

- [ ] **Step 3: Configure async-aware Alembic**

Load `DATABASE_URL` from the environment/settings, import `Base.metadata`, use
`async_engine_from_config`, enable `compare_type=True`, and render named constraints. Never store a
password in `alembic.ini`.

- [ ] **Step 4: Create the explicit initial migration**

Create tables in dependency order and indexes/checks from the approved design. Downgrade drops in
reverse dependency order. Do not insert demo business data in the schema migration.

- [ ] **Step 5: Prove migration and metadata agree**

Run the migration test plus `alembic check` against the disposable database.  
Expected: upgrade/downgrade passes and Alembic reports no new upgrade operations.

- [ ] **Step 6: Commit**

Commit: `feat: add initial PostgreSQL migration`

### Task 6: PostgreSQL repository adapters

**Files:**
- Create: `roma/repositories/postgres/repositories.py`
- Test: `tests/repositories/postgres/test_repositories.py`

- [ ] **Step 1: Write failing repository round-trip tests**

Against migrated PostgreSQL, test caller upsert by hash, call creation/provider-ID assignment,
lifecycle update, ordered turns, append-only events, slot creation, appointment booking, evidence
ledgers, recording metadata, follow-up job identity, reference data, and audit writes.

- [ ] **Step 2: Add failure tests before implementation**

Prove duplicate provider call IDs, turn numbers, event/usage/cost/job idempotency keys, and active
slot bookings become `PersistenceConflict`; prove a transaction containing a later invalid row
rolls back its earlier rows.

- [ ] **Step 3: Implement caller, call, and reference repositories**

Use SQLAlchemy `select`, `insert`, and `update` statements scoped to the active unit-of-work
session. Convert ORM rows to domain records before returning. Caller upsert uses the unique hash and
never stores the raw phone number.

- [ ] **Step 4: Implement appointment repository**

Offer slots by indexed branch/date/status order. Book by validating/locking the slot inside the
current transaction, inserting an active appointment, and relying on the partial unique index as
the final arbiter.

- [ ] **Step 5: Implement evidence repository**

Append safety events, raw provider usage, normalized costs, recording metadata, follow-up job
identities, and audit logs. Do not expose update/delete methods for append-only ledgers.

- [ ] **Step 6: Verify and commit**

Run: `.venv/bin/pytest tests/repositories/postgres/test_repositories.py -q`  
Expected: all round-trip, uniqueness, idempotency, and rollback tests pass.

Commit: `feat: implement PostgreSQL repositories`

### Task 7: Appointment concurrency proof and local infrastructure

**Files:**
- Create: `compose.yaml`
- Test: `tests/repositories/postgres/test_appointment_concurrency.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the concurrent booking test**

Create one slot, launch 100 independently transacted booking attempts with `asyncio.gather`, and
assert exactly one returns an appointment while 99 return `PersistenceConflict`; finally query the
database and assert one active row.

- [ ] **Step 2: Add local dependency services**

Define PostgreSQL and Redis services matching supported local versions, with health checks, named volumes, local-only published
ports, non-production development credentials, and no application container. Add a `postgres`
pytest marker.

- [ ] **Step 3: Run the concurrency proof**

Run: `.venv/bin/pytest tests/repositories/postgres/test_appointment_concurrency.py -q`  
Expected: one winner, 99 conflicts, one stored active appointment.

- [ ] **Step 4: Commit**

Commit: `test: prove PostgreSQL appointment concurrency`

### Task 8: Idempotent reference seed command

**Files:**
- Create: `scripts/seed_reference_data.py`
- Test: `tests/scripts/test_seed_reference_data.py`

- [ ] **Step 1: Write a failing double-run test**

Run the seed function twice against migrated PostgreSQL and assert one institute, one branch,
configured courses, and the canonical roles `admin`, `counsellor`, and `viewer` with stable codes.

- [ ] **Step 2: Implement explicit seed behavior**

Use repository methods inside a unit of work and upsert only reference rows by stable code/name.
Accept institute/branch seed values from command arguments or value-free environment configuration;
never create callers, calls, appointments, users, or credentials.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest tests/scripts/test_seed_reference_data.py -q`  
Expected: the second run creates no duplicates.

Commit: `feat: add idempotent reference seed command`

### Task 9: Documentation and complete verification

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/01-architecture.md`
- Modify: `docs/06-state-and-cache.md`
- Modify: `docs/10-build-order.md`
- Modify: `docs/13-backend-roadmap.md`
- Modify: `docs/14-data-and-concurrency.md`
- Modify: `docs/decisions.md`
- Modify: `docs/12-verification.md`

- [ ] **Step 1: Update documentation truthfully**

Mark the PostgreSQL foundation/schema/repository boundary implemented, document local startup,
migration/seed/test commands, explain that REST resources and durable worker execution remain later
work, and preserve the Redis ownership table. Record the decisions: async SQLAlchemy, real
PostgreSQL tests, HMAC phone identity, partial unique active slot, append-only evidence, and no DB
writes in the audio-frame path.

- [ ] **Step 2: Run static verification**

Run: `.venv/bin/ruff check roma tests scripts migrations`  
Expected: no findings.

- [ ] **Step 3: Run focused PostgreSQL verification**

Run: `.venv/bin/pytest tests/repositories/postgres tests/core/test_database.py tests/domain/test_persistence.py -q`  
Expected: all tests pass against the supported local PostgreSQL version with no paid APIs.

- [ ] **Step 4: Run the complete offline suite**

Run: `.venv/bin/pytest -q`  
Expected: previous 1,358 tests plus new tests pass, with no regression.

- [ ] **Step 5: Verify secrets and migration state**

Run: `git check-ignore .env && git diff --check && git status --short`  
Expected: `.env` is ignored, no secret file is staged, and only intended implementation files are
present.

- [ ] **Step 6: Commit and push the completed phase**

Commit documentation with `docs: explain PostgreSQL and Redis ownership`, verify the branch is
ahead of `origin/main` only by intended commits, and push `main` to `origin`.

## Self-review

- Spec coverage: every requested table is registered in Task 3 and migrated in Task 5; keys,
  constraints, indexes, cascades, idempotency, transaction boundaries, and retention are covered.
- Redis boundary: no Redis implementation file is changed; ownership is verified in Task 9.
- Test depth: metadata, migration, repository, rollback, idempotency, and 100-way concurrency are
  tested against PostgreSQL rather than SQLite.
- Scope control: no REST CRUD, login/RBAC behavior, analytics endpoint, retry engine, provider call,
  or audio-pipeline persistence is added.
- Type consistency: domain records feed protocol methods, PostgreSQL repositories implement those
  protocols, and the unit of work is the only transaction owner.
