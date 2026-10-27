# Level 13 — Container deployment

**Roadmap section:** 53. **Status:** Explicit mock full-stack packaging and local Docker Desktop rehearsal implemented; full clean-host/native deployment gate remains open.

## Authorized scope and architecture

The owner requested section 53 before earlier full gates close. `compose.stack.yaml` packages a deliberately mock-only FastAPI factory, SQL-leased worker, PostgreSQL/pgvector, Redis, Prometheus and Grafana. Each application service has a separate Dockerfile; shared targets remain available for the CI build matrix. Digests pin third-party images. Model weights are mounted rather than copied into images; `.dockerignore` excludes private `.env`, runtime/audio, caches, credentials and weights. This increment changes no working database or private configuration.

The selected ASR is pinned Whisper Turbo MLX on Apple Silicon, not a Linux/NVIDIA implementation. Both `gpu` and `native-mac` profiles are explicit fail-closed capability contracts. They do not silently switch models, download weights or claim working speech serving. GPU containers require an approved compatible backend and NVIDIA runtime on their host; the Mac-native bridge still needs implementation and acceptance.

## Startup, secrets and storage

`uv run --no-sync python scripts/container_stack.py up` generates private local stack keys under ignored `var/container-stack/`, builds images, starts dependencies, migrates and runs smoke checks. The mock API refuses nonmock provider configuration and loads no private `.env`. All published ports bind to loopback. Datastores/worker remain on the internal network; only published API/monitoring services also join a bridge for Docker Desktop forwarding. Secrets are injected into owner-restricted runtime volumes by a bounded network-free initializer, never baked into images. API/worker, Redis and monitoring run with explicit unprivileged users; PostgreSQL's official entrypoint bootstraps then drops privileges. Read-only roots, limits and health checks are explicit.

`down` stops this named project and preserves volumes without initializing keys. Startup checks this project’s retained volumes before key creation, refuses lost/partial secrets, and requires restoration of the original material. A complete legacy keyset can be adopted without rotation. Two real restarts preserved every original key byte; missing-state/key drills failed closed. It never uses destructive volume removal. `rollback --previous-tag TAG` requires an already-built compatible application image and never pulls an invented release. Schema compatibility and backups must be reviewed before an actual production rollback.

## Recorded rehearsal and limitations

[The sanitized receipt](../../benchmarks/deployment/section53_mock_stack.json) records image/source identities, six healthy services, migration head `20261026_0007`, one SQL worker effect, unauthenticated metrics denial/authenticated success, Prometheus target health and Grafana datasource health. A synthetic SQL marker survived the compatible-tag rollback rehearsal and was removed afterward. The initial previous-tag rehearsal used the same built image; the later per-service packaging run switched between distinct compatible local image IDs. Both prove orchestration/persistence, while rollback to a historical released binary remains unverified. The isolated proof containers were stopped while their volumes were retained.

Regression tests cover Compose contracts, safe context/secrets/permissions, startup commands, nonmock refusal, dependency readiness and avoiding a Python package collision with the Docker SDK. See [verification](../12-verification.md) and the [runbook](../runbook.md) for combined evidence and commands.

## Acceptance gate

- [ ] Clean approved second host reaches the selected working profile with one command.
- [x] Local mock stack migrations, readiness, worker and monitoring smoke checks pass.
- [ ] Approved native speech/GPU profile works without changing selected models.
- [ ] Historical compatible-image rollback and backup/restore meet agreed recovery objectives.
- [ ] Production access, secret rotation, volumes and resource budgets receive operational review.

No whole Level 13 gate passes. Section 54+ remains unrequested.

Return to the [documentation index](../README.md).
