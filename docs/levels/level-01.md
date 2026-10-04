# Level 1 — Backend foundation

**v4 sections:** 3–9. **Status:** Foundation partially implemented; provider/API/CI/native-startup gate pending.

## Entry gate

Level 0 gate reviewed.

## Scope when implementation is requested

Add PostgreSQL durable entities and Redis transient boundaries. Use SQLAlchemy 2 short units of work with explicit pool/overflow/timeout settings and a process-wide connection budget. Define leads, calls/turns, appointments, users, jobs/audit and knowledge metadata as required by later gates; avoid speculative unused entities.

Introduce Alembic migrations with tested upgrade/rollback strategy and separate seeds. Keep a modular monolith and domain rules free of SDKs. Define ASR, LLM (wording and extraction), TTS and telephony contracts with mocks/configuration.

Design `/api/v1` envelopes/errors, pagination/filter/sort and OpenAPI without silently replacing compatibility routes. Establish pytest/Ruff/type-checking/pre-commit/CI incrementally, and a documented local-service startup without Docker.

PostgreSQL/schema/layers already exist here. Review and fill their gate gaps; public routes remain unversioned despite the `api/v1` directory. Existing infrastructure Compose stays compatibility tooling; the new foundation startup path uses native services.

## Existing reuse in Voice_Agent

Layered `roma/`, async SQLAlchemy pools/units of work, two Alembic migrations, FastAPI, repository contracts and pytest/Ruff. Complete gaps; do not rebuild these foundations.

## Acceptance gate

- [ ] One documented command starts required development services without Docker.
- [ ] Migrations work on an empty database; seed and rollback strategy verified.
- [ ] Connection budget/pool behavior reviewed; no session crosses inference/audio waits.
- [ ] Provider contract mocks and versioned API checks pass; CI is green.

## Boundaries and advanced work

No local model loading, RAG, microservice split or Docker. New tooling is implemented deliberately, not claimed available now.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
