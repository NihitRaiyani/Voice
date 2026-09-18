# Roma documentation map

This directory is both the operating manual for the current voice backend and the curriculum for
turning it into a production-grade backend platform.

## Status vocabulary

| Status | Meaning |
|---|---|
| **Implemented** | Present in the repository and backed by code/tests or recorded runtime evidence |
| **Next** | Recommended upcoming learning increment; not yet implemented |
| **Planned** | Accepted future direction without an implementation commitment |
| **Optional** | Add only when measurements or product needs justify it |
| **Historical** | Preserved context; not current instructions |

If a tutorial describes a target design, the status label wins. Never infer that prose equals code.

## Current-system track

| Document | What it answers |
|---|---|
| [00 — Project charter](00-project-charter.md) | Why the project exists and how learning is measured |
| [01 — Architecture](01-architecture.md) | What runs during a call and where boundaries live |
| [02 — Pipeline and latency](02-pipeline.md) | Where turn latency and provider cost come from |
| [03 — Phase machine](03-phase-machine.md) | How software—not the LLM—owns conversation state |
| [04 — Guardrails](04-guardrails.md) | How unsafe claims are stopped before speech |
| [05 — Endpointing, VAD, barge-in](05-endpointing-vad.md) | How realtime turn-taking and cancellation work |
| [06 — State and cache](06-state-and-cache.md) | What Redis owns today and what it must not own later |
| [07 — Security](07-security.md) | Current trust boundaries, secrets, signatures, and PII |
| [08 — Concurrency](08-concurrency.md) | Per-call isolation and asynchronous work |
| [09 — Recording](09-recording-storage.md) | Recording lifecycle, durability, and retention |
| [11 — Prompts](11-prompts.md) | Runtime prompt assembly and editing contract |
| [12 — Verification](12-verification.md) | Evidence required before a live test |

## Backend-learning track

| Document | Main concepts |
|---|---|
| [10 — Build order](10-build-order.md) | Four levels, milestones, and five mandatory additions |
| [13 — Backend roadmap](13-backend-roadmap.md) | Current-to-target architecture and module status |
| [14 — Data and concurrency](14-data-and-concurrency.md) | PostgreSQL, Redis, schemas, transactions, locking |
| [15 — API, security, and jobs](15-api-security-and-jobs.md) | REST, auth/RBAC, webhooks, idempotency, workers |
| [16 — Observability, testing, delivery](16-observability-testing-and-delivery.md) | Logs, metrics, traces, test pyramid, load, Docker, CI/CD |
| [17 — Placement study guide](17-placement-study-guide.md) | Study sequence, interview questions, proof portfolio |

## Supporting records

- `decisions.md` contains current architectural decisions and durable operational lessons.
- `LOG.md` at the repository root is append-only historical evidence.
- `docs/superpowers/specs/` and `docs/superpowers/plans/` are historical designs and execution
  plans. They may mention systems that were later replaced.
- `HANDOFF.md` is the current short handoff; `SESSION.md` is the session template.

## Source-of-truth order

When documents disagree, use this order:

1. tested current code and configuration contracts;
2. `CLAUDE.md` safety and engineering boundaries;
3. current-system documents (`README.md`, `docs/01`–`docs/12`, `docs/decisions.md`);
4. target tutorials (`docs/13`–`docs/17`);
5. historical logs, specifications, and plans.
