# Level 7 — Real-time reliability

**v4 sections:** 22–25. **Status:** Cloud lifecycle/recovery seams present; local-stack reliability gate pending.

## Entry gate

Level 6 local loop and durable state/booking gates.

## Scope when implementation is requested

Add safe sentence streaming and bounded per-stage queues with measured high/low watermarks, capacity and overflow decisions. Offload blocking audio/inference work so the event loop remains responsive. Do not silently drop validated speech or committed business events.

Propagate barge-in/disconnect cancellation through model, aggregation/filter, TTS and carrier flush. Add bounded provider/retrieval timeouts, retry/circuit/fallback behavior and Redis transient coordination with PostgreSQL milestone checkpoints. Measure checkpoint lag, lock TTL behavior and recovery limits.

## Existing reuse in Voice_Agent

Pipecat cancellation/clear, isolation/watchdog tests, Redis checkpoint/degradation and spool/job recovery seams.

## Acceptance gate

- [ ] Barge-in, double cancellation, disconnect and late-result tests leave no stale audible output or leaked tasks.
- [ ] Pressure tests prove bounded queues and acceptable event-loop lag with documented overflow actions.
- [ ] Redis loss/slow providers degrade safely; retries respect remaining turn budget.
- [ ] Checkpoint restore and observed recovery limits are demonstrated.

## Boundaries and advanced work

Advanced turn epochs, state event log/Redis streams/outbox and replay start after the core gate. Do not claim lossless recovery without evidence.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
