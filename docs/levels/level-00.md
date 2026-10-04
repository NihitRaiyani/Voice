# Level 0 — Baseline and prerequisites

**v4 sections:** 1–2. **Status:** Assessment recorded; gate pending.

## Entry gate

None; inspect the existing project before changing it.

## Scope when implementation is requested

Trace one call from transport through ASR, controller, extraction/generation, final safety, TTS and teardown. Explain async/cancellation, HTTP/WebSocket, audio basics, database transactions and provider boundaries.

Pin source/environment identity, reproduce offline checks on a clean setup and measure representative stage P50/P95 latency. Freeze the comparison behavior without treating the existing cloud implementation as the target architecture.

Inventory the existing dirty worktree without changing user edits. Do not import test counts or carrier facts from another checkout.

## Existing reuse in Voice_Agent

Current Twilio/Pipecat pipeline, PostgreSQL/Redis/jobs, lockfile, tests and offline evals; fresh evidence is in the baseline report.

## Acceptance gate

- [ ] Reviewed prerequisite self-check and call-path explanation.
- [ ] Clean-environment restore, tokenizer setup and green offline checks.
- [ ] Representative timing report with P50/P95, sample/configuration identity and limitations.

## Boundaries and advanced work

No provider switch, database feature or level implementation. No inferred live readiness.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
