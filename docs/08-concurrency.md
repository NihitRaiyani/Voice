# Concurrency, cancellation and delivery

Each call owns its Pipecat task/provider streams, controller state, aggregation, queues, recorder and Twilio audio sequence counters. Section 14 lab streams separately own their fixed jitter queue/filter history; neither is shared across calls. Share only explicitly safe client pools/immutable data; never lead state. Async I/O does not make blocking CPU/GPU inference non-blocking. Offload it and measure event-loop lag.

## Current concurrency guarantees and limits

The appointment repository locks an existing slot and has active-slot uniqueness; `/answer` commits a unique receipt/call/event atomically. Section 13 integrates booking with the production controller/API, adds expiring owner-token holds and tests commit-before-win, retries, cancellation and a 100-request HTTP race. These guarantees do not establish measured real-call capacity.

Use **one recording queue consumer**: its startup inflight recovery assumes one consumer. Dramatiq consumers are a different pool and claim targeted PostgreSQL rows under locks. Do not infer arbitrary recording-worker scaling from atomic Redis list operations.

Replica growth must review signature/external-URL handling, shared Redis coordination, per-process database pools, admission and shutdown. No source-only test establishes a supported number of real GPU calls.

## Implemented sections 22–23 opt-in path

[Level 7](levels/level-07.md#sections-2223-implementation) owns the bounded streaming contract and evidence. `BoundedChannel` provides explicit capacities/high-low watermarks and recorded overflow/wait metrics. `StreamingText` separates tokens, punctuation and audited clauses; `StreamingDelivery` overlaps TTS/playback. `CpuWorker` isolates bounded pure-Python work without duplicating model weights. `StreamingInput` keeps unstable partials out of the controller and owns each response task separately so barge-in cannot kill the final-transcript consumer.

Cancellation invalidates old delivery before joining tasks. Confirmed speech cancels the active turn; disconnect additionally clears/closes output, attempts resource release and writes an idempotent disconnect event. Output pressure is an explicit safe fallback or transport-close outcome. Physical microphone input remains half duplex; advanced epochs, native reliability/latency and sections 24–25 remain pending. The existing carrier pipeline is preserved.

## Level 7 real-time gate

Bound queues with capacities, high/low watermarks, age, timeout and explicit overflow behavior. Disposable data may have a documented drop policy; validated caller speech and committed business events cannot silently disappear. Capture pressure/drop/lag metrics.

Cancellation must be idempotent: stop generation, discard partial text, cancel TTS and send Twilio `clear` before stale audio can play. Test double interruption/disconnect, late callbacks and task/resource cleanup. Bounded retries/circuit/fallback must respect remaining turn/call budget and safety.

Measure PostgreSQL milestone lag and Redis failure/lock expiry behavior. Advanced turn epochs/event logs/outbox are follow-on work after core cancellation/recovery.

## Current jobs and shutdown

Recording conversion/retention and external sync stay off the spoken turn. Post-call worker stores or policy-discards audio, commits call/job intents, then acks the original `PostcallJob.raw` payload; reserialization breaks exact Redis removal.

Dramatiq notifications carry job UUIDs; PostgreSQL owns leases, business attempts, effects and terminal state. Broker redelivery is at least once. Dispatcher duplicates are safe for business effects but need future publication/backpressure bounds. Dead-letter retention/replay needs explicit governance.

Stop new calls first, allow or terminate active work through the cleanup contract, stop publishing, drain the recording queue and let actors settle before shutdown. Preserve pending database/spool intents. See [jobs](19-background-task-framework.md), [recordings](09-recording-storage.md) and [verification](12-verification.md).
