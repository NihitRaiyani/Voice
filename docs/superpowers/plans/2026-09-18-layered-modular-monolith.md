# Layered Modular Monolith Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Roma from `src/roma` into a top-level, responsibility-oriented `roma` package while
preserving all current voice-agent behavior and tests.

**Architecture:** API entrypoints depend on services/domain rules; Redis and external SDK details
live in repositories/providers; Pipecat stays in a dedicated realtime area; post-call execution
lives under workers. The migration is mechanical first, then improves the application seams.

**Tech Stack:** Python 3.12, FastAPI, Pipecat, Redis, Twilio, Sarvam, OpenAI, pytest, Ruff, uv.

---

### Task 1: Create the secure local environment

**Files:**
- Create locally: `.env` (ignored)
- Verify: `.env.example`, `.gitignore`

- [ ] Copy supported non-obsolete values from `/Users/nihitraiyani/Weltec/.env` without printing
  them, add the supplied Twilio values, and omit VoBiz/frontend variables.
- [ ] Add safe defaults for missing non-secret settings and preserve `.env.example` as value-free.
- [ ] Verify `git check-ignore .env` succeeds and no secret is staged or printed.

### Task 2: Move the installable package to `roma/`

**Files:**
- Move: `src/roma/**` -> `roma/**`
- Modify: `pyproject.toml`
- Modify: repository scripts and documentation paths

- [ ] Move the package with Git history preserved.
- [ ] Change Hatch and Ruff source roots from `src` to `roma`; remove the pytest `pythonpath` shim.
- [ ] Update path references without changing `import roma...` statements.
- [ ] Run an import smoke test and the configuration tests.

### Task 3: Establish core and domain ownership

**Files:**
- Move: `roma/config.py` -> `roma/core/config.py`
- Move: `roma/logging_setup.py` -> `roma/core/logging.py`
- Move: `roma/guardrails/**` -> `roma/domain/safety/**`
- Move: `roma/controller/**` -> `roma/domain/conversation/**`
- Move appointment modules into `roma/domain/appointments/`
- Move calling policies from `roma/dialer/` into `roma/domain/calls/`
- Move: `roma/spend.py` -> `roma/domain/costs/spend.py`

- [ ] Add package initializers and update imports/re-exports.
- [ ] Keep domain modules independent of FastAPI/Twilio wherever already possible.
- [ ] Run controller, guardrail, spend, and dialer-policy tests.

### Task 4: Establish provider and repository seams

**Files:**
- Move Twilio modules to `roma/providers/telephony/twilio/`
- Move Google Calendar integration to `roma/providers/calendar/google.py`
- Move Redis stores/queues to `roma/repositories/redis/`
- Create narrow provider factory modules for Sarvam STT/TTS and OpenAI LLM
- Create repository interface package for the store operations used by services

- [ ] Update callers to import provider/repository modules rather than SDK/storage locations.
- [ ] Preserve dependency injection used by tests.
- [ ] Run Twilio, provider, Redis-store, and queue tests.

### Task 5: Separate API, services, realtime, and workers

**Files:**
- Create: `roma/main.py`
- Create: `roma/api/dependencies.py`
- Create: `roma/api/v1/calls.py`
- Create: `roma/api/v1/health.py`
- Create: `roma/api/v1/twilio_webhooks.py`
- Create: `roma/services/call_service.py`
- Move realtime modules to `roma/realtime/`
- Move post-call execution to `roma/workers/postcall/`

- [ ] Make API modules responsible only for transport validation/mapping and application assembly.
- [ ] Keep call triggering and status orchestration in the call service.
- [ ] Keep Pipecat/audio/interruption logic in realtime modules.
- [ ] Keep background execution in workers and persistence in repositories.
- [ ] Preserve `build_media_app` as the supported compatibility factory.
- [ ] Run API, media, realtime, and post-call tests.

### Task 6: Mirror tests and update operator entrypoints

**Files:**
- Move tests into `tests/api`, `tests/core`, `tests/domain`, `tests/services`,
  `tests/repositories`, `tests/providers`, `tests/realtime`, and `tests/workers`
- Modify: `scripts/*.py`, `scripts/start_roma.sh`
- Modify: `README.md`, `CLAUDE.md`, `HANDOFF.md`, `docs/01-architecture.md`, `docs/README.md`

- [ ] Preserve test names and behavior while aligning their locations with ownership.
- [ ] Update all startup/import/file-path references to `roma/`.
- [ ] Add a concise final tree and navigation guide to the active documentation.

### Task 7: Final verification

**Files:**
- Verify the complete working tree.

- [ ] Run `uv run --extra telephony --extra dev ruff check roma tests scripts`.
- [ ] Run the complete offline pytest suite with safe dummy required settings.
- [ ] Run `scripts/verify_media.py` and the shell syntax check.
- [ ] Confirm no active `src/roma`, VoBiz runtime, frontend, or stale module imports remain.
- [ ] Confirm `.env` exists, is ignored, and is absent from Git status.
- [ ] Review the final diff and update this checklist.
