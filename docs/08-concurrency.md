# 08 — Concurrency

Roma runs many calls at once. The model is: **one call = one isolated async task; shared work
is pushed to queues.**

## The three pools
1. **Dialer pool** — places outbound calls at a controlled rate. Bounded concurrency (respect
   Twilio limits + calling-window/DND rules). Backpressure: don't dial faster than pipeline
   capacity.
2. **Pipeline tasks** — one async task per active call, each an isolated Pipecat instance.
   No call-specific global state. All per-call data keyed by Call SID in Redis.
3. **Post-call worker pool** — consumes `queue:postcall`: store recording, then CRM/calendar
   write. Slow work lives here, never in the pipeline.

## Isolation rules (what prevents cross-call bugs)
- Nothing mutable is shared across calls except Redis (namespaced) and the queue (atomic ops).
- Each pipeline owns its own Bulbul socket, its own STT stream, its own call-state key.
- A crash in one call's task must not take down others — supervise tasks; isolate failures.

## The barge-in / cancellation hot spot
This is where concurrency bugs hide. Within a single call, endpointing, LLM streaming, filter,
and TTS run concurrently, and barge-in cancels across all of them mid-flight. Requirements:
- Cancellation is **idempotent** — a double barge-in must not double-flush or deadlock.
- Cancellation propagates **in order**: stop LLM → flush filter → flush Bulbul → Twilio
  `clear`. Out-of-order flushing causes overtalk or leaked audio.
- The filter stage must be cancellation-aware (see `docs/04`).
- **Run code-review on every change to this path.** (Skill wiring in `skills/`.)

## Scaling
- Vertical: more pipeline tasks per process until CPU/socket limits.
- Horizontal: more replicas; Redis + queue are shared. Stateless replicas — all state in Redis.
- Capacity is gated by the slowest real-time dependency (STT/LLM/TTS throughput), not CPU —
  measure before assuming.

## Graceful shutdown
- Drain: stop the dialer, let in-flight calls finish, flush the post-call queue, then exit.
- A recording must never be lost because a worker was killed mid-job — jobs are ack'd only
  after the recording is durably stored.
