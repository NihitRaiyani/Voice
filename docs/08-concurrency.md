# Concurrency, cancellation and delivery

Each call owns its Pipecat task/provider streams, controller state, aggregation, queues and recorder. Share only explicitly safe client pools/immutable data; never lead state. Async I/O does not make blocking CPU/GPU inference non-blocking. Offload it and measure event-loop lag.

## Current concurrency guarantees and limits

The appointment repository locks an existing slot and has active-slot uniqueness; `/answer` commits a unique receipt/call/event atomically. These are implemented database boundaries, not proof of live booking integration or measured call capacity.

Use **one recording queue consumer**: its startup inflight recovery assumes one consumer. Dramatiq consumers are a different pool and claim targeted PostgreSQL rows under locks. Do not infer arbitrary recording-worker scaling from atomic Redis list operations.

Replica growth must review signature/external-URL handling, shared Redis coordination, per-process database pools, admission and shutdown. No source-only test establishes a supported number of real GPU calls.

## Level 7 real-time gate

Bound queues with capacities, high/low watermarks, age, timeout and explicit overflow behavior. Disposable data may have a documented drop policy; validated caller speech and committed business events cannot silently disappear. Capture pressure/drop/lag metrics.

Cancellation must be idempotent: stop generation, discard partial text, cancel TTS and send Twilio `clear` before stale audio can play. Test double interruption/disconnect, late callbacks and task/resource cleanup. Bounded retries/circuit/fallback must respect remaining turn/call budget and safety.

Measure PostgreSQL milestone lag and Redis failure/lock expiry behavior. Advanced turn epochs/event logs/outbox are follow-on work after core cancellation/recovery.

## Current jobs and shutdown

Recording conversion/retention and external sync stay off the spoken turn. Post-call worker stores or policy-discards audio, commits call/job intents, then acks the original `PostcallJob.raw` payload; reserialization breaks exact Redis removal.

Dramatiq notifications carry job UUIDs; PostgreSQL owns leases, business attempts, effects and terminal state. Broker redelivery is at least once. Dispatcher duplicates are safe for business effects but need future publication/backpressure bounds. Dead-letter retention/replay needs explicit governance.

Stop new calls first, allow or terminate active work through the cleanup contract, stop publishing, drain the recording queue and let actors settle before shutdown. Preserve pending database/spool intents. See [jobs](19-background-task-framework.md), [recordings](09-recording-storage.md) and [verification](12-verification.md).
