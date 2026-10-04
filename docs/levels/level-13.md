# Level 13 — Container deployment

**v4 sections:** 53. **Status:** Pre-v4 infrastructure tooling present; full v4 packaging/deployment planned.

## Entry gate

Level 12 production gates and owner-approved deployment/backup policies.

## Scope when implementation is requested

Introduce Docker last. Provide Compose profiles for application, PostgreSQL, Redis, local-model/GPU services and observability as needed. Mount model weights/data; inject secrets at runtime and define health/readiness/resource limits.

Document clean-host one-command startup, migrations, smoke tests, backup/restore and rollback. Preserve source-controlled configuration identity and durable-data compatibility. Test upgrade/recovery with real persistence, not only container health.

The existing database/Redis Compose predates v4. Expand it only here into the validated full deployment, without deleting working persistence or confusing compatibility tooling with gate evidence.

## Existing reuse in Voice_Agent

Pre-v4 PostgreSQL/Redis Compose, local scripts/migrations and validated provider/job/recovery boundaries. Existing Compose is not full deployment evidence.

## Acceptance gate

- [ ] Clean approved host starts the selected profile with one documented command.
- [ ] Migrations/smoke/readiness and no-external-GenAI checks pass.
- [ ] Backup/restore and rollback demonstrate the agreed recovery objectives.
- [ ] Images contain no secrets/PII/embedded model weights; mounts/permissions/resource budgets are reviewed.

## Boundaries and advanced work

No Docker earlier for convenience, production host assumption, Kubernetes or deployment authorization from the roadmap.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
