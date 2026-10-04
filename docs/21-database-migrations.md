# Database migrations and separate demo data

**Level 1 section 5.** SQLAlchemy 2.x defines the relational metadata; Alembic owns schema, constraints and indexes. [The schema catalog](20-relational-schema.md) explains their purpose. This workflow retains all existing revisions and the current single head. Verification still uses disposable databases, while the configured `roma` database was upgraded to `20261004_0003 (head)` on 2026-10-04; every other intended environment must run the same upgrade before new schema consumers start.

## Preserved revision history

| Revision | Parent | Schema change |
|---|---|---|
| `20260920_0001` | base | Initial durable schema, including users/roles, callers/calls/turns, appointments, safety, costs, reference data and operational records |
| `20260930_0002` | `20260920_0001` | Atomic answer-webhook receipts and replay protection |
| `20261004_0003` | `20260930_0002` | Conversation checkpoints, model registry, benchmark runs/results and nullable turn language |

The roadmap's five numbered examples illustrate ordered migrations; they do not require splitting or renumbering history already present in this project. All three revision files remain unchanged in section 5. There is no empty fourth migration: this increment changes tooling and documentation, not schema. Future schema/index changes append a reviewed revision to the current head. Never edit an applied revision to make a new model match it.

## Configuration and operator workflow

Run from `<project-root>` with the locked virtual environment available. Migrations and demo seeding load database-only settings from `DATABASE_URL` or `.env`; they need no AI, carrier or Redis credentials. Both `postgresql://` and `postgresql+asyncpg://` normalize to the asyncpg driver. Keep real connection strings out of tracked files and command history. Explicit Alembic `Config` URLs remain supported for isolated tests. Migration connections use `NullPool`, and dispose even after failure; they are separate from the application pool budget.

```bash
source .venv/bin/activate
alembic heads
alembic history --verbose
alembic current
alembic upgrade head
alembic current
alembic check
```

`heads`/`history` inspect files; `current` and `check` require PostgreSQL. The expected head is `20261004_0003`; the configured `roma` database was confirmed at that head on 2026-10-04. `check` confirms no proposed metadata operations, not data integrity, service readiness or performance. Upgrade every other intended environment before code queries the new turn-language column. Alembic's [migration tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html#running-our-first-migration) describes revision ordering and upgrade execution.

Preview SQL without connecting to PostgreSQL:

```bash
alembic upgrade head --sql > "$TMPDIR/roma-schema-review.sql"
```

This renders the base-to-head schema. For a database at the prior head, preview only the pending range with `alembic upgrade 20260930_0002:head --sql`. Offline rendering requires a database URL to choose the dialect; it does not apply changes. SQL review complements integration tests.

## Developing a forward migration

1. Modify the appropriate SQLAlchemy model and explain each new key/constraint/index in the schema catalog.
2. Use a disposable development database at the current head. Run `alembic revision --autogenerate -m "describe the schema change"`.
3. Review both `upgrade()` and `downgrade()`. Autogeneration proposes changes; check rename interpretation, nullability/defaults, existing rows, FK deletion rules, partial predicates and index order. Do not accept accidental drops of old tables or implicit data loss.
4. Test base-to-head, prior-head-to-head with representative existing rows, `alembic check`, and downgrade/re-upgrade on disposable databases. Add behavioral tests for meaningful new constraints and indexes.
5. Update the catalog, revision table, rollback impact and level evidence together. Apply through one controlled migration process before starting consumers of the new schema.

Never use application startup `create_all()` or `alembic stamp head` to bypass missing migrations. Avoid simultaneous migration runners. A future large backfill should use an explicit restartable job between compatible expand/contract migrations; no long business-data transformation belongs in a live-call transaction. If a future large-table index needs PostgreSQL `CONCURRENTLY`, review its nontransactional execution and failure/retry cleanup separately; present indexes use ordinary migration operations.

## Rollback and data preservation

Choose the exact known revision and coordinate code compatibility before any working-database downgrade. Stop affected writers, take a restorable backup, and confirm the restore procedure in an isolated environment. Prefer a compatible application rollback or a reviewed forward correction once new data exists. A reversible schema operation does not restore dropped rows.

| Downgrade | Effect and required decision |
|---|---|
| `20261004_0003` → `20260930_0002` | Drops checkpoints, model registry and benchmark evidence plus turn language. Legacy rows/keys remain, but new records/language are lost; export/backup them and deploy code that does not query removed columns/tables. |
| `20260930_0002` → `20260920_0001` | Removes webhook receipts and durable replay protection. Stop callback intake and review receipt preservation/replay controls before considering this rollback. |
| `20260920_0001` → base | Removes the durable business schema and its data. Use only for disposable fixtures or a planned destructive reset with restore, never routine recovery. |

For a **disposable database**, the current-head rollback/re-upgrade exercise is:

```bash
alembic downgrade 20260930_0002
alembic upgrade head
alembic check
```

Full backup/restore recovery timing and clean-host deployment remain later acceptance work; these migration tests do not pass those gates.

## Seeds stay outside schema history

Alembic revisions insert no demo reference data. A separate opt-in command provides synthetic local reference data after schema upgrade:

```bash
APP_ENV=dev python scripts/seed_demo.py --demo
```

The command refuses environments other than `dev`/`test`, refuses a database not at the current single head, and never upgrades or stamps it automatically. On an empty schema it inserts **seven rows** in one short atomic transaction: one institute, one branch, one course, one branch-course association and three role names. The demo institute/branch/course are **inactive**, so they do not represent approved counselling offerings. Role names create no accounts, passwords, assignments or permissions. No callers, calls, appointments, phone numbers, recordings, jobs or benchmark results are seeded.

Stable IDs and database uniqueness make reruns and concurrent invocations safe. Existing names/descriptions and legitimate same-name roles are preserved; conflicting demo identity causes rollback rather than attaching demo rows to business records. Output contains the profile and inserted-row count; bounded failures omit connection strings and SQL parameters. A second successful invocation normally reports zero inserted rows.

Approved real institute/course/counsellor data needs its own reviewed import and owner approval of facts; synthetic data is not a source for production advice. Do not automatically seed during application startup, schema upgrades or production deployment. For demo reset, recreate a disposable database instead of deleting arbitrary business rows. Alembic's [data migration guidance](https://alembic.sqlalchemy.org/en/latest/cookbook.html#data-migrations-general-techniques) explains why separate data scripts suit this separation.

## Verification and learning outcome

The [Level 1 brief](levels/level-01.md) records current evidence and remaining gates. Tests verify the preserved single revision chain, offline schema/index SQL, metadata parity, database-only configuration, upgrade/downgrade/re-upgrade, seed-free schema creation, repeatable/concurrent seeds, preservation of existing data, atomic conflict rollback and production/implicit seed refusal. Integration checks use disposable PostgreSQL; the configured `roma` database state is verified separately with `alembic current` and was at `20261004_0003 (head)` on 2026-10-04.

Students should explain why applied history is immutable, why generated migrations need review, why a downgrade can lose data, why indexes belong in schema history, and why demo content must remain separate from durable business facts.

Return to the [documentation index](README.md).
