# Documentation and Learning System Design

**Date:** 2026-09-18
**Scope:** Documentation, guidance, and project-local skills only. Runtime code, tests,
configuration, prompts, and dependencies are outside this change.

## Goal

Turn Roma's documentation into one coherent system that serves two readers at the same time:

1. an engineer who needs the exact truth about the current Twilio voice backend; and
2. a student using the repository to master backend engineering for placements.

## Design

The documentation will use a dual-layer structure.

- **Operational layer:** explains what exists now, how calls flow, which invariants are
  non-negotiable, and how to verify the current system.
- **Learning layer:** teaches the backend concept demonstrated by each subsystem, connects it
  to the mentor roadmap, and names the next exercise without pretending it is implemented.

Every roadmap item must carry one status:

- **Implemented:** present in the repository and supported by evidence.
- **Next:** the recommended upcoming learning increment.
- **Planned:** valuable but not yet implemented.
- **Optional:** introduce only after a measured need.
- **Historical:** retained as decision or migration context, not current instructions.

## Information architecture

- `README.md` is the concise entry point and current-system quick start.
- `CLAUDE.md` is the engineering constitution for human and agent changes.
- `docs/README.md` is the documentation map and source-of-truth policy.
- `docs/00-project-charter.md` explains the product goal and learning goal.
- `docs/01` through `docs/09` explain the existing real-time system.
- `docs/10-build-order.md` becomes the four-level implementation curriculum.
- `docs/11` and `docs/12` remain prompt and verification runbooks.
- New roadmap chapters cover persistence/concurrency, API/security,
  observability/testing/deployment, and interview study.
- `LOG.md` and `docs/superpowers/` remain historical records.
- Project-local skills enforce the roadmap boundary and critical runtime invariants.

## Guardrails

- Do not alter Python, shell, configuration, dependency, test, or runtime prompt files.
- Do not document PostgreSQL, JWT/RBAC, workers, OpenTelemetry, Prometheus, Grafana, Docker,
  or CI/CD as implemented until the repository contains them.
- Keep Twilio as the telephony provider; SIP/VoBiz material may remain only in explicitly
  historical migration records.
- Preserve the deterministic pre-call, pre-TTS, signature-validation, PII, and no-live-call
  verification boundaries.
- Prefer a modular monolith. Kafka, Kubernetes, microservices, vector databases, and complex
  agent orchestration require a demonstrated problem before adoption.

## Success criteria

- A new reader can distinguish current behavior from future architecture without reading code.
- A student can follow a four-level path from the current voice system to production backend
  engineering and explain the trade-offs in interviews.
- Future agents are told which documents are authoritative and cannot silently implement the
  whole roadmap during a focused task.
- All changed files are documentation or project-local skill text.
