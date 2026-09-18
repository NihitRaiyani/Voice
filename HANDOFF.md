# Current Handoff

## Current system

Roma is a backend-only Twilio voice agent. Outbound calls use the Twilio Calls API; signed
`/answer` and `/ws` callbacks establish the bidirectional media stream. The Pipecat pipeline uses
Silero, Sarvam Saaras/Bulbul, OpenAI, a software-owned seven-stage controller, deterministic
pre-TTS safety filtering, Redis operational state, and a post-call recording workflow.

The former web frontend and VoBiz runtime integration are not part of the active system.

## Current documentation state

Documentation now has two connected tracks:

- `docs/01`–`docs/12`: current operational behavior and the concepts it demonstrates;
- `docs/13`–`docs/17`: mentor-aligned backend roadmap and tutorial material.

Use `docs/README.md` as the map and `CLAUDE.md` as the engineering constitution. Historical build
records remain in `LOG.md` and `docs/superpowers/`.

## Recommended next engineering milestone

Level 1 begins with PostgreSQL, SQLAlchemy 2.x, and Alembic, followed by durable call/lead records
and transaction-safe appointment booking. This is **not implemented yet**. Write and approve a
focused design before changing runtime code.

## Before any work

1. Check the roadmap status; do not treat planned features as current behavior.
2. Preserve pre-call gates, pre-TTS filtering, Twilio signature validation, PII redaction,
   per-call isolation, and the no-live-call automated-test rule.
3. Keep slow persistence and worker tasks outside the realtime path.
4. Define the business invariant, failure cases, and verification evidence.

## Live testing boundary

Before a manual live call, confirm `/health`, signed callbacks, API token, calling window,
denylist, spend budget, hourly limit, Redis, and an approved test handset. Never commit the
destination number. Any credential previously shared in chat must be rotated and stored only in
the ignored local environment.
