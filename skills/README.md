# Project skills and workflow

Skills support the requested work; [the docs index](../docs/README.md), [level briefs](../docs/10-build-order.md) and [decisions](../docs/decisions.md) own project contracts. Tool availability depends on the active environment.

| Local guidance | Use when |
|---|---|
| `.claude/skills/roma-backend-roadmap/SKILL.md` | v4 level planning, architecture, persistence, API, jobs, security, retrieval, measurement or delivery |
| `.claude/skills/roma-guardrail/SKILL.md` | Spoken output, deterministic safety, sentence flushing or cancellation |

These documents must reference the actual `roma/` package and current v4 contracts. They cannot override explicit user scope or describe implemented PostgreSQL/jobs as entirely planned.

Use relevant available design/planning, debugging, review and verification skills. Inspect installed APIs and current official provider/library docs before integrating them. No missing historical plugin is a build prerequisite; no skill automatically starts all levels or creates new project skills.

Keep backend-only scope, deterministic safety/control, fake paid providers in tests and evidence-driven complexity. See [workflow examples](sync-prompts.md).

The user authorizes automatic GitHub commits/pushes after each completed requested level or section. Follow the [publishing workflow](../docs/10-build-order.md#how-to-work): verify checks, synchronize docs, preserve unrelated edits/secrets and report the pushed commit link; do not advance scope or force-push.
