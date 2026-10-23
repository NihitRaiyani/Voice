# Level 7 — Real-time reliability

**Roadmap sections:** 22–25. **Status:** Sections 22–24 streaming, cancellation and supervised Redis/SQL state are implemented and verified offline; real-call reliability/native gates, advanced tracks and section 25 remain pending.

## Entry gate

Level 6 local loop and durable state/booking gates.

## Remaining scope and acceptance

Add safe sentence streaming and bounded per-stage queues with measured high/low watermarks, capacity and overflow decisions. Offload blocking audio/inference work so the event loop remains responsive. Do not silently drop validated speech or committed business events.

Propagate barge-in/disconnect cancellation through model, aggregation/filter, TTS and carrier flush. Add bounded provider/retrieval timeouts, retry/circuit/fallback behavior and Redis transient coordination with PostgreSQL milestone checkpoints. Measure checkpoint lag, lock TTL behavior and recovery limits.

## Existing reuse in Voice_Agent

Pipecat cancellation/clear, isolation/watchdog tests, Redis checkpoint/degradation and spool/job recovery seams.

## Sections 22–23 implementation

`make --silent voice-prototype ARGS=--streaming` opts into the new local sentence-delivery path. Without the flag, the existing full-response prototype is unchanged. The physical microphone remains half duplex: capture closes before response processing/playback. The existing Twilio/Pipecat/Sarvam path, its signed transport and production interruption logic remain unchanged. The new transport-neutral cancellation seam is exercised with fake input and real `WebSocketDisconnect` objects, without carrier calls or microphone access.

`TextConversationService` still owns deterministic stages, validated slots and booking commits. Optional sentence callbacks activate bounded token/chunk/safety stages. The chunker recognizes commas, full stops, question/exclamation marks and Indic danda, preserves decimal/thousands separators and caps unfinished tails and the entire turn. Existing final-speech rules check each clause plus its bounded cumulative prefix, preventing a risky noun and its later amount from evading checks across a comma. The first activation substitutes an approved line, audits it and stops the remaining generation. Extraction JSON never enters the sentence path. Accepted controller state is checkpointed before streaming begins.

`StreamingDelivery` overlaps checked sentences, local synthesis and playback. Every phrase is checked again by `SpeechSynthesisService`; cached speech keeps the same gate. A short ready receipt precedes playback. Queue overflow cancels the turn and records loss, clears supported output and attempts the approved fallback; if the client cannot accept it, its close callback is invoked. No text or audio is silently removed while pretending the answer completed. Existing safe stage codes remain; `STREAM-BACKPRESSURE` is the additional shared pressure code.

### Queue policies

All eight application channels use `BoundedChannel`; underlying `asyncio.Queue` capacity is explicit. No sentinel needs an extra queue slot. Default ordered-stage configuration is **capacity 8 / high 6 / low 2 / producer timeout 2 s**; `StreamConfig` validates it. Input channels default to capacity/high 8 and low 0. Hysteresis resumes producers only at/below low water. Snapshots and `queue_summary` JSON records contain current/peak depth, capacity, high-water hits, oldest/newest drops, cancelled/discarded items and separate producer/consumer/total wait milliseconds.

| Queue | Overflow decision |
|---|---|
| `audio_in` | DropOldest for unaccepted stale capture; any loss rejects the incomplete input turn and clears/closes the session instead of making an ASR final across a gap |
| `partials` | DropOldest; display/diagnostic updates only, never a controller/booking input |
| `finals` | Preserve order with bounded backpressure; counted DropNewest on timeout fails the stream |
| `tokens`, `chunks` | Preserve reply order with bounded backpressure; timeout fails/closes the generator rather than silently losing words |
| `safe_chunks`, `tts_text`, `tts_audio` | Preserve approved speech order; timeout is counted DropNewest with explicit fallback/close |

The current local STT contract returns finals. `StreamingInput.partial()` accepts optional provider/transport interim updates, but does not fabricate incremental Whisper transcripts or re-decode extra windows. Existing carrier interim behavior remains intact. Library-owned Pipecat queues are outside the new opt-in queue implementation; this is not a claim that every third-party queue was replaced.

### CPU and model boundaries

The streaming CLI shares a `CpuWorker` across turns: two spawned processes, at most eight submitted jobs and eight admission waiters, with a bounded admission timeout. Prefix safety/control checks, heavy normalization and the same pure-Python PCM-to-mu-law conversion run there. Cancellation abandons results but retains a job's admission lease until the process work actually finishes; it cannot create an unbounded backlog. Normal and process-offloaded audio framing are tested byte-identical. Worker logging uses the same PII-safe formatter.

No model is downloaded, changed or multiplied into these CPU workers. Claude remains behind the existing streaming provider, Indic-TTS already uses its isolated runtime subprocess, and existing local ASR stays on its reviewed nonblocking worker boundary. Dedicated speech model serving/deployment remains the Level 10 boundary; this increment does not deploy a new inference service or claim real-model capacity. Slow native jobs cannot always be physically preempted, so late results are invalidated and bounded rather than claimed cancelled inside a kernel.

### Interruption and disconnect

Per-turn tasks are separate from the long-lived final-transcript consumer. A continuous probability-qualified speech span must reach **600 ms** (configurable minimum and probability threshold) before barge-in. Short noise resets without cancelling. Actual `SpeechInputService` VAD frames drive this seam in the transport-neutral input runner. No echo cancellation or full-duplex physical microphone capture is added; minimum duration cannot identify sustained echo by itself.

The shared control path invalidates delivery, cancels generation/synthesis/playback, discards unfinished clauses and queued outputs, joins cooperative tasks, then invokes output clear. Accepted slots/stage stay checkpointed and the final-transcript worker accepts the next turn. The five-minute input-session cap is preserved. An explicit disconnect uses the same path, is idempotent, releases per-call resource/hold callbacks and marks the call terminal. Clear or close failure still attempts release and durable finish. Playback rechecks delivery ownership after persistence and fallback awaits; the next response waits for the previous output clear to finish. Cleanup deadlines close/poison an unresponsive session rather than permit another generation alongside old work. A provider which ignores cancellation forever still requires process/service-level termination; no source-only native cleanup guarantee is claimed.

`PostgresVoiceLabStore` reuses existing JSON events for `interrupted`/`disconnected` turn outcomes and an idempotent `voice_lab.disconnected` event. Existing `calls.status` does not permit a literal `disconnected`; it remains `failed` with the exact reason in the event. No migration or working-DB mutation is needed. Approved filler can be primed into the existing phrase cache and replayed through `play_cached_filler()` after a fresh safety check, without synthesis on playback. It does not autoplay over the caller, and filler audio is not used to manufacture a better meaningful-response TTFA.

The late-TTS core test uses delivery invalidation and task ownership. **Advanced turn epochs and their teacher test remain deferred**: the prerequisite Level 7 gate is not passed. No monotonic epoch, Redis-per-packet check or premature advanced-gate claim is introduced.

### Offline evidence

Focused queue/streaming/CPU/cancellation/privacy and existing voice/safety checks passed **160 tests**. Final isolated CI and full suites each passed **2,994 tests**, with 22 existing dependency warnings; Ruff/mypy passed. Reproducible four-call fake WebSocket/slow-CPU/TTS evidence is [saved here](../../benchmarks/voice/section22-23-offline-report.json), produced by `make --silent streaming-lab`. It reports per-call speech-end to first software speaker write, queue metrics and event-loop heartbeat delay. All ASR/LLM/TTS/audio I/O is synthetic. Cold process startup is included; no native pronunciation, acoustic TTFA, real resampler capacity or audible-jitter acceptance is inferred. Final full-suite results are recorded in [verification](../12-verification.md#sections-2223-streaming-and-interruption).

## Section 24 live state and supervised checkpoints

`LiveCallStateStore` composes the existing Redis and PostgreSQL adapters. `roma.main` injects one coordinator per carrier call; local prototype composition uses the same writer. Provider settings, signed Twilio transport, Sarvam speech and physical microphone half duplex are unchanged. Plain injected legacy stores keep their load/save contracts in offline fixtures. A coordinator startup failure closes/releases owned resources and refuses fresh-state continuation. SQL remains authoritative for committed appointments.

Milestones are call started, accepted state/slot change, caller turn completed, stage changed, audited safety activation, booking confirmed and call ended. The existing short SQL adapter writes compatible latest snapshots. `CheckpointSafetyEventStore` first commits safety audit, then checkpoints the current state. No frame, audio packet or model token becomes a database write. Provider wording/playback never shares a database session with these writes.

The teaching supervisor is **awaited**, with no detached task or background queue. One immutable pending snapshot per call is serialized ahead of newer state. SQL commits before publishing Redis; a failed SQL milestone is not advertised as a new hot checkpoint. Default operation timeout is 1 s, two total attempts for known transient SQL errors, and 50 ms retry delay. Validation/conflict failures do not retry; a transient ownership read failure retains the snapshot for a later safe attempt. Failed writes expose `pending`, `lag_ms`, failure/retry/checkpoint/cache/recovery counts in PII-safe JSON events. Completed write latency measures the checkpoint wait. Shutdown attempts pending work and releases ownership; failed persistence stays visible rather than claiming success. These waits add measured database latency to milestone boundaries; this is not a latency SLO.

`RedisCallLease` uses hashed call keys and random owner tokens with explicit `SET NX EX`: default 360 s, bounded constructor range 6–600 s, no automatic renewal. The default covers the existing five-minute session ceiling plus cleanup. Ownership checks require more than five seconds remaining before bounded writes. Release compares the token atomically; an expired owner never reacquires its old session or deletes a successor. Failed initial acquisition can recover, including a lost acquire reply. Expiry is normal: a new store/owner restores the durable stage and slots once the lease is free. A crashed worker may leave admission blocked until its lease expires; no shorter recovery-time guarantee is claimed.

Redis hot state expires after one hour; PostgreSQL checkpoint retention remains four hours with schema/policy checks. Restore checks SQL even if Redis survived, because a failed cache refresh can leave same-turn stale data. Missing, expired or corrupt Redis data is rebuilt from SQL. SQL read/ownership failures stop recovery; they never mean start from empty. During Redis ownership outages, checkpoint admission fails safely until ownership is provable. Existing booking confirmation still uses a synchronous SQL transaction; conversational offers can use fresh SQL availability when advisory Redis holds fail, while foreign ownership and SQL uniqueness remain conflicts. A lost hold-acquire reply is reconciled using the original call/token identity.

State/lead/opener/status TTLs require exact integers from 1 to 86,400 s. Existing fixed phrase mirrors now have a one-hour TTL; hot phrase lookup remains in memory. The hourly counter uses one Redis transaction for `INCR` and initial `EXPIRE NX`, preserving the first expiry and repairing an immortal old bucket. Strict hold APIs do not pretend a lease exists during outages. Existing `SET NX` booking holds retain their bounded lease and owner checks.

**Recovery point:** no acknowledged milestone depends on Redis for durability. A process/Redis loss may discard in-progress work since the last SQL milestone; uncommitted speech/slots are not guaranteed. Complete loss of transient cache/locks/counters is accepted and reconstructed where possible; spend and appointment authority do not move to Redis. This policy requires no Redis persistence change; existing compatibility Compose already enables AOF, and no running Redis configuration was changed. Redis Streams, replay, durable event sequencing and AOF-based replay RPO remain advanced work after the core gate, with a separate owner-approved deployment recovery target.

Real disposable PostgreSQL/Redis tests demonstrate missing/expired hot keys, exact stage/slot reconstruction, successor ownership and rejection of stale writes/releases. Synthetic tests cover retry order, immutable snapshots, pending lag, safety/turn milestones, lease recovery and private metric rejection. See [verification](../12-verification.md#section-24-redis-state-and-milestone-recovery) for final counts: CI and the full suite each passed **3,078 tests**, with 22 existing dependency warnings; Ruff/mypy passed. No paid API, microphone, model, working `roma` or migration change was used; all gate checkboxes below remain pending.

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
