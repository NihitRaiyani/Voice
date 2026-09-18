# Documentation and Learning System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Combine accurate operational documentation with a tutorial-style backend curriculum
based on the mentor roadmap.

**Architecture:** Keep current-system truth separate from target-state learning material. Use a
documentation index, explicit status vocabulary, focused roadmap chapters, and project-local
skills that preserve the boundary between implemented and planned work.

**Tech Stack:** Markdown, Claude project skills, Git.

---

### Task 1: Establish the documentation contract

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Create: `docs/README.md`
- Create: `docs/00-project-charter.md`

- [x] Rewrite the entry points around the current Twilio backend and student-learning purpose.
- [x] Define the status vocabulary and source-of-truth order.
- [x] State clearly that roadmap technologies are targets, not installed features.
- [x] Confirm that no runtime or configuration file changed.

### Task 2: Connect existing runtime chapters to backend concepts

**Files:**
- Modify: `docs/01-architecture.md`
- Modify: `docs/02-pipeline.md`
- Modify: `docs/03-phase-machine.md`
- Modify: `docs/04-guardrails.md`
- Modify: `docs/05-endpointing-vad.md`
- Modify: `docs/06-state-and-cache.md`
- Modify: `docs/07-security.md`
- Modify: `docs/08-concurrency.md`
- Modify: `docs/09-recording-storage.md`

- [x] Add a consistent current-status and learning-objective preface to each chapter.
- [x] Add a short roadmap bridge that identifies the next relevant backend exercise.
- [x] Preserve verified implementation details and safety invariants.

### Task 3: Replace the obsolete build sequence with the mentor curriculum

**Files:**
- Modify: `docs/10-build-order.md`
- Modify: `docs/11-prompts.md`
- Modify: `docs/12-verification.md`
- Modify: `docs/decisions.md`

- [x] Record the existing voice pipeline as the completed baseline.
- [x] Define Levels 1–4 and the five mandatory additions.
- [x] Preserve prompt and verification contracts while identifying what they teach.
- [x] Record the modular-monolith and evidence-before-complexity decisions.

### Task 4: Add the tutorial chapters

**Files:**
- Create: `docs/13-backend-roadmap.md`
- Create: `docs/14-data-and-concurrency.md`
- Create: `docs/15-api-security-and-jobs.md`
- Create: `docs/16-observability-testing-and-delivery.md`
- Create: `docs/17-placement-study-guide.md`

- [x] Explain the target architecture and status of every roadmap module.
- [x] Teach PostgreSQL/Redis boundaries and appointment transaction invariants.
- [x] Teach API design, authentication, webhook security, idempotency, and workers.
- [x] Teach logs, metrics, traces, tests, load testing, Docker, and CI/CD.
- [x] Add interview questions and evidence-based completion criteria.

### Task 5: Refresh working guidance and skills

**Files:**
- Modify: `HANDOFF.md`
- Modify: `SESSION.md`
- Modify: `skills/README.md`
- Modify: `skills/sync-prompts.md`
- Modify: `.claude/skills/roma-guardrail/SKILL.md`
- Create: `.claude/skills/roma-backend-roadmap/SKILL.md`

- [x] Make session and handoff templates distinguish current, next, and planned work.
- [x] Replace obsolete skill notes with task-triggered guidance.
- [x] Add a narrow roadmap skill that prevents scope creep and false implementation claims.
- [x] Validate the new skill frontmatter and structure.

### Task 6: Verify the documentation-only boundary

**Files:**
- Verify all changed paths.

- [x] Run `git diff --name-only` and confirm every path is Markdown or project-skill text.
- [x] Search active documentation for stale VoBiz/SIP/frontend instructions.
- [x] Search for roadmap technologies and confirm they are labelled as planned where needed.
- [x] Review links and headings, then inspect the final diff.
