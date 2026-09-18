# 06 — State & Cache (Redis)

**Status:** Redis usage is implemented for transient operational state. PostgreSQL durable state is
the recommended next major addition.

**Learning objective:** Choose storage by lifetime and consistency needs. Redis is fast operational
memory; it is not the future system of record for appointments, audit logs, or cost ledgers.

Redis plays three roles: per-call state, cache, and the post-call job queue. Keep them in
separate key namespaces.

## 1. Per-call state (resume-on-drop)
- **Key:** `call:{call_sid}:state` → the call-state object from `docs/03`.
- **Written** on every phase transition and slot fill (not every token — durable checkpoints
  only).
- **TTL:** expire a few hours after call end; the post-call worker reads it before expiry.
- **Resume:** if a call drops mid-flow and reconnects (or a retry dials back), load the last
  checkpoint so Roma resumes at the phase reached, not from P1. Discovery answers persist —
  never re-ask a slot already filled.

## 2. Cache (latency + cost)
The same lines are spoken on every single call. Cache them.
- **Fixed-phrase TTS cache:** pre-render Bulbul audio for lines that never change — greeting,
  the four filter substitution lines, the readback template, "No-cost EMI available."
  **Key:** `tts:{voice}:{hash(text)}` → audio bytes. Huge win: these skip STT→LLM→TTS
  entirely on repeat.
- **Filler-token cache:** the 200–300ms "achha…" / "haan ji…" clips. Always cached, never
  live-generated.
- **KB cache:** the DM course facts are static; cache the retrieved grounding so P3/P4 don't
  re-fetch.
- **Cache invalidation:** fixed-phrase and KB caches are versioned by a content hash — change
  the line, the key changes, old entry is ignored. No manual purge needed.

## 3. Post-call queue
- **Key:** `queue:postcall` (Redis list or stream).
- **Producer:** pipeline pushes a job `{call_sid, recording_ref, locked_slot}` on call end.
- **Consumer:** the post-call worker (see `docs/08`, `docs/09`).
- Decouples slow work (recording storage, CRM write) from the real-time loop.

## What NOT to put in Redis
- Raw audio streams (they flow through Pipecat, not Redis).
- Secrets (see `docs/07` — secret store, not Redis).
- Anything global-mutable shared across calls. All call data is namespaced by Call SID.

## Concurrency safety
- One writer per `call:{sid}` key (that call's task). No cross-call contention by design.
- The queue is the only shared structure; Redis list/stream ops are atomic — safe for
  multiple workers.

## Roadmap bridge: Redis versus PostgreSQL

| Keep in Redis | Move/add in PostgreSQL |
|---|---|
| Active-call context and TTL checkpoints | Callers, calls, turns, and appointments |
| Cached audio and grounded facts | Safety events and audit logs |
| Short-lived counters and rate limits | Provider usage and normalized costs |
| Distributed locks and job delivery state | Recording metadata and retention state |

See `docs/14-data-and-concurrency.md` for the target schema and transaction exercises.
