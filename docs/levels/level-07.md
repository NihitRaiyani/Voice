# Level 7 — Real-time reliability

**Roadmap sections:** 22–25. **Status:** Sections 22–25 streaming, cancellation, Redis/SQL state and provider reliability have offline evidence. Phases 5A–5C add persistent capture/epochs, retained TTS, local full duplex/AEC and synthetic replay; native reliability and the full gate remain pending.

## Entry gate

Level 6 local loop and durable state/booking gates.

## Remaining scope and acceptance

Add safe sentence streaming and bounded per-stage queues with measured high/low watermarks, capacity and overflow decisions. Offload blocking audio/inference work so the event loop remains responsive. Do not silently drop validated speech or committed business events.

Propagate barge-in/disconnect cancellation through model, aggregation/filter, TTS and carrier flush. Add bounded provider/retrieval timeouts, retry/circuit/fallback behavior and Redis transient coordination with PostgreSQL milestone checkpoints. Measure checkpoint lag, lock TTL behavior and recovery limits.

## Existing reuse in Voice_Agent

Pipecat cancellation/clear, isolation/watchdog tests, Redis checkpoint/degradation and spool/job recovery seams.

## Sections 22–23 implementation

At the sections 22–23 increment, `make --silent voice-prototype ARGS=--streaming` opted into sentence delivery and physical capture remained half duplex. Phase 5C supersedes that local input policy: local mode now defaults to full duplex and sentence streaming; mock mode stays buffered unless `--streaming` is supplied. See [the current local contract](#phase-5c-local-full-duplex-and-echo-cancellation). The existing Twilio/Pipecat/Sarvam path, its signed transport and production interruption logic remain unchanged. The new transport-neutral cancellation seam is exercised with fake input and real `WebSocketDisconnect` objects, without carrier calls or microphone access.

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

Per-turn tasks are separate from the long-lived final-transcript consumer. The sections 22–23 default required **600 ms** of probability-qualified continuous speech (configurable minimum and threshold) before barge-in; The 2026-10-28 repair restores **600 ms** for local full duplex and suppresses completed shorter overlaps before ASR/controller admission. Short noise resets without cancelling. Actual `SpeechInputService` VAD frames drive this seam in the transport-neutral input runner. That increment added no echo cancellation or full-duplex physical capture. Phase 5C adds local reference-based AEC; minimum duration alone cannot identify sustained echo.

The shared control path invalidates delivery, cancels generation/synthesis/playback, discards unfinished clauses and queued outputs, joins cooperative tasks, then invokes output clear. Accepted slots/stage stay checkpointed and the final-transcript worker accepts the next turn. The five-minute input-session cap is preserved. An explicit disconnect uses the same path, is idempotent, releases per-call resource/hold callbacks and marks the call terminal. Clear or close failure still attempts release and durable finish. Playback rechecks delivery ownership after persistence and fallback awaits; the next response waits for the previous output clear to finish. Cleanup deadlines close/poison an unresponsive session rather than permit another generation alongside old work. A provider which ignores cancellation forever still requires process/service-level termination; no source-only native cleanup guarantee is claimed.

`PostgresVoiceLabStore` reuses existing JSON events for `interrupted`/`disconnected` turn outcomes and an idempotent `voice_lab.disconnected` event. Existing `calls.status` does not permit a literal `disconnected`; it remains `failed` with the exact reason in the event. No migration or working-DB mutation is needed. Approved filler can be primed into the existing phrase cache and replayed through `play_cached_filler()` after a fresh safety check, without synthesis on playback. It does not autoplay over the caller, and filler audio is not used to manufacture a better meaningful-response TTFA.

The late-TTS core test uses delivery invalidation and task ownership. **Advanced turn epochs and their teacher test remain deferred**: the prerequisite Level 7 gate is not passed. No monotonic epoch, Redis-per-packet check or premature advanced-gate claim is introduced.

### Offline evidence

Focused queue/streaming/CPU/cancellation/privacy and existing voice/safety checks passed **160 tests**. Final isolated CI and full suites each passed **2,994 tests**, with 22 existing dependency warnings; Ruff/mypy passed. Reproducible four-call fake WebSocket/slow-CPU/TTS evidence is [saved here](../../benchmarks/voice/section22-23-offline-report.json), produced by `make --silent streaming-lab`. It reports per-call speech-end to first software speaker write, queue metrics and event-loop heartbeat delay. All ASR/LLM/TTS/audio I/O is synthetic. Cold process startup is included; no native pronunciation, acoustic TTFA, real resampler capacity or audible-jitter acceptance is inferred. Final full-suite results are recorded in [verification](../12-verification.md#sections-2223-streaming-and-interruption).

## Section 24 live state and supervised checkpoints

`LiveCallStateStore` composes the existing Redis and PostgreSQL adapters. `roma.main` injects one coordinator per carrier call; local prototype composition uses the same writer. That increment preserved provider settings, signed Twilio transport, Sarvam speech and the then-current physical microphone half duplex; Phase 5C changes only the local input policy. Plain injected legacy stores keep their load/save contracts in offline fixtures. A coordinator startup failure closes/releases owned resources and refuses fresh-state continuation. SQL remains authoritative for committed appointments.

Milestones are call started, accepted state/slot change, caller turn completed, stage changed, audited safety activation, booking confirmed and call ended. The existing short SQL adapter writes compatible latest snapshots. `CheckpointSafetyEventStore` first commits safety audit, then checkpoints the current state. No frame, audio packet or model token becomes a database write. Provider wording/playback never shares a database session with these writes.

The teaching supervisor is **awaited**, with no detached task or background queue. One immutable pending snapshot per call is serialized ahead of newer state. SQL commits before publishing Redis; a failed SQL milestone is not advertised as a new hot checkpoint. Default operation timeout is 1 s, two total attempts for known transient SQL errors, and 50 ms retry delay. Validation/conflict failures do not retry; a transient ownership read failure retains the snapshot for a later safe attempt. Failed writes expose `pending`, `lag_ms`, failure/retry/checkpoint/cache/recovery counts in PII-safe JSON events. Completed write latency measures the checkpoint wait. Shutdown attempts pending work and releases ownership; failed persistence stays visible rather than claiming success. These waits add measured database latency to milestone boundaries; this is not a latency SLO.

`RedisCallLease` uses hashed call keys and random owner tokens with explicit `SET NX EX`: default 360 s, bounded constructor range 6–600 s, no automatic renewal. The default covers the existing five-minute session ceiling plus cleanup. Ownership checks require more than five seconds remaining before bounded writes. Release compares the token atomically; an expired owner never reacquires its old session or deletes a successor. Failed initial acquisition can recover, including a lost acquire reply. Expiry is normal: a new store/owner restores the durable stage and slots once the lease is free. A crashed worker may leave admission blocked until its lease expires; no shorter recovery-time guarantee is claimed.

Redis hot state expires after one hour; PostgreSQL checkpoint retention remains four hours with schema/policy checks. Restore checks SQL even if Redis survived, because a failed cache refresh can leave same-turn stale data. Missing, expired or corrupt Redis data is rebuilt from SQL. SQL read/ownership failures stop recovery; they never mean start from empty. During Redis ownership outages, checkpoint admission fails safely until ownership is provable. Existing booking confirmation still uses a synchronous SQL transaction; conversational offers can use fresh SQL availability when advisory Redis holds fail, while foreign ownership and SQL uniqueness remain conflicts. A lost hold-acquire reply is reconciled using the original call/token identity.

State/lead/opener/status TTLs require exact integers from 1 to 86,400 s. Existing fixed phrase mirrors now have a one-hour TTL; hot phrase lookup remains in memory. The hourly counter uses one Redis transaction for `INCR` and initial `EXPIRE NX`, preserving the first expiry and repairing an immortal old bucket. Strict hold APIs do not pretend a lease exists during outages. Existing `SET NX` booking holds retain their bounded lease and owner checks.

**Recovery point:** no acknowledged milestone depends on Redis for durability. A process/Redis loss may discard in-progress work since the last SQL milestone; uncommitted speech/slots are not guaranteed. Complete loss of transient cache/locks/counters is accepted and reconstructed where possible; spend and appointment authority do not move to Redis. This policy requires no Redis persistence change; existing compatibility Compose already enables AOF, and no running Redis configuration was changed. Redis Streams, replay, durable event sequencing and AOF-based replay RPO remain advanced work after the core gate, with a separate owner-approved deployment recovery target.

Real disposable PostgreSQL/Redis tests demonstrate missing/expired hot keys, exact stage/slot reconstruction, successor ownership and rejection of stale writes/releases. Synthetic tests cover retry order, immutable snapshots, pending lag, safety/turn milestones, lease recovery and private metric rejection. See [verification](../12-verification.md#section-24-redis-state-and-milestone-recovery) for final counts: CI and the full suite each passed **3,078 tests**, with 22 existing dependency warnings; Ruff/mypy passed. No paid API, microphone, model, working `roma` or migration change was used; all gate checkboxes below remain pending.

## Section 25 provider reliability

`roma/providers/reliability.py` owns sanitized provider failure kinds, inherited absolute deadlines and a per-adapter circuit breaker. Existing authentication/state/booking/safety errors retain their own boundaries; provider bodies/tokens never become public errors. Claude extraction and wording share the existing configured turn timeout across queued production processors and the text console; the five-minute voice ceiling remains an outer bound. Lock/admission, backoff and provider I/O consume the remaining budget. A timer is active only while awaiting provider work, never across a yielded chunk, so downstream queue/playback delay cannot masquerade as caller cancellation.

Claude retries existing timeout/429/5xx transient cases only before text escapes and within its current retry/token/spend bounds. Authentication, invalid requests and exhausted quota are permanent failures. Errors can occur after an HTTP 200 SSE start; pre-token overload remains eligible for the same bounded retry policy. Once any text escapes, or cancellation occurs, no retry can duplicate speech. Existing deterministic approved fallback and safety/audit gates remain authoritative. Generator close promptly closes the SDK stream. SDK retries stay disabled to avoid nested retry multiplication; model/effort/output settings are unchanged.

The breaker opens after three failed logical transient requests, waits 15 s and admits one half-open probe. It is local to the adapter, not a distributed outage service. Local ASR keeps its current inference timeout and worker ownership; Indic-TTS keeps kill/reap on timeout and now bounds manifest/cache-identity waits too. Cancelled native hash work retains admission until it actually finishes. Neither native model is blindly rerun. Twilio call creation retains its 15 s HTTP timeout and zero automatic retries, because an ambiguous response may already have dialled. Existing Sarvam streaming reconnect behavior is preserved; no new cross-process circuit/native capacity claim is made.

Independently verified provider/text/voice/streaming regressions passed **682 tests** with one existing dependency warning. Test-first cases cover deadline admission/expiry, transient SSE and circuit recovery, native cancellation ownership, stream closure, extraction/wording deadline sharing, and timers not cancelling downstream consumers. [Verification](../12-verification.md#section-25-provider-reliability) owns exact results. The owner deferred paid, microphone, native and advanced acceptance until sections 25–28 are completed and separately reviewed. No gate below is marked passed.

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

## Reliability follow-up — 2026-10-25

The four-call lab now proves it traverses fake token generation: an existing stage acknowledgement replaces a factual question that section32 correctly routed deterministically. Every call must produce one fake request and two chunks; a regression test fails if that coverage is bypassed. The [fresh standalone report](../../benchmarks/voice/reliability-followup-20261025.json) records four played calls, four requests/eight chunks, first software audio **557.96–589.08 ms**, maximum excess heartbeat lag **2.383 ms**, all five queues bounded and no fallback. All input, generation, synthesis and playback are synthetic; these numbers are not a carrier capacity/SLO or acoustic measurement.

The local supervised CLI also gained explicit SQL-authoritative pause/resume, privacy-tombstone denial and sleep-aware input/output deadline checks. Disposable SQL and fake-device tests cover ownership exclusion, retained attempts/deadlines, terminal rejection and late-audio suppression. Full physical concurrency/interruption/cancellation/recovery and representative quality remain unaccepted. Epochs/Streams replay and adaptive jitter/PLC keep their documented prerequisites; no advanced gate is passed by these controls.


## Phase 5A persistent capture, epochs and replay

The 2026-10-25 owner instruction authorizes previously deferred advanced implementation before native acceptance, without waiving that gate. See the [capture/audio contract](../05-endpointing-vad.md#persistent-capture-and-advanced-audio-lab) and [synthetic metrics](../../benchmarks/voice/phase5a-adaptive-replay.json). Input stays open across software-muted half-duplex turns; shared epochs suppress stale ASR/controller/LLM/TTS/playback work. Lab Streams and adaptive jitter/PLC replay change no production media or business-state recovery. Physical cancellation latency, callback reliability, representative ASR and native naturalness remain pending. Phase commits stay local under the explicit no-push instruction.

## Phase 5B retained TTS

Local readiness explicitly warms and retains one cache-verified Indic-TTS worker and active voice. Bounded epoch-tagged IPC, cancellation/timeout cleanup and identity changes preserve ownership. Audited sentence delivery overlaps synthesis and playback; finite date/time/cardinal pronunciation preserves both source and spoken safety checks. [Verification](../12-verification.md#phase-5b-retained-tts-and-speech-rendering) records the separate offline and native synthetic evidence. No listening gate follows from those measurements.

## Phase 5C local full duplex and echo cancellation

The owner-requested local default is continuous full duplex. `--half-duplex` explicitly selects the earlier software-muted diagnostic path; `--buffered-speech` controls response delivery independently. `LocalAudioDevice.frames()` keeps mono PCM16 capture active at 16 kHz/20 ms through ASR, synthesis, playback and output clear. Under the 2026-10-29 timestamp policy, one delivered-sample clock spans valid and missing ADC timestamps. ADC/current/performance timestamp disagreement is telemetry even at or above 20 ms, and does not discard complete delivered PCM. Existing small-error alignment calibration remains capped at 50 μs per callback; corroborated clock-domain rebases affect the ADC diagnostic mapping. Native overflow, invalid PCM and application capacity/contention losses still fail explicitly. Sample counts do not establish a shared hardware clock or acoustic AEC alignment; dated earlier results remain historical. The callback only copies bounded frames; real DSP runs in the serial consumer.

The optional `duplex` extra pins `pywebrtc-audio==0.2.0` for local WebRTC AEC3. A bounded sample-addressed playback reference uses actual persistent-callback handoffs, an anchored sample clock that counts silence, and off-callback 8→16 kHz resampling. Missing/wrong-version/failed AEC refuses full duplex instead of forwarding raw microphone PCM. Noise suppression and automatic gain control are off by default. Clearing unplayed queued output preserves already-handed DAC-tail reference and learned echo state.

`DuplexVoiceSession` owns continuous VAD/endpointing independently of bounded decode/final queues and response work. Local interruption requires 600 ms of continuous qualified speech (608 ms in 32 ms VAD quanta, plus capture alignment/scheduling). Shorter speech that starts during a response is discarded as an acoustic overlap, even if output ends before that overlap: this cannot establish lexical intent or booking consent. A brief intentional interruption may also be ignored. Software clear timing begins after this decision; speech-onset-to-acoustic-stop latency remains a separate unmeasured quantity. Sustained near-end speech during an active response advances the shared turn epoch and starts output clear/cancellation; late results cannot start a new reply. Only eligible, already-captured onset frames transfer ownership, and discontinuities/pressure fail explicitly rather than stitch incomplete input. Existing safety, language policy, SQL booking authority and five-minute lifetime remain authoritative. An in-flight SQL commit still requires reconciliation.

Fake-device/session coverage and generated-sample real-DSP probes are separate evidence classes; [verification](../12-verification.md#phase-5c-duplex-verification-scope) and the [runbook](../runbook.md#phase-5c-local-full-duplex) define reproducible commands and limits. No physical speaker/microphone double-talk, acoustic cancellation latency, representative speech preservation or native acceptance is established by this increment. Models, production Twilio/Sarvam and working database policy are unchanged. Delivery remains one local phase commit with no push.

## Phase 6 guided acceptance tooling

`make acceptance-live` reuses the local full-duplex path with metadata-only observation hooks, prepared TEST ONLY booking, bounded aggregate reservations and signed local receipts. The runner distinguishes short-acknowledgement observations, epoch/control clear timing, actual SQL commit/restore and human scores. Acoustic latency and full native/reliability gates are never inferred from software timing or one prepared scenario. [The runbook](../runbook.md#phase-6-guided-live-acceptance) owns commands and evidence boundaries.

Capture discontinuities discard incomplete speech and pending stale work before bounded cleanup and a request to repeat the whole utterance. The same native input stream resumes with a new epoch; at most three recoveries fit within the original deadline. A fourth loss or failed cleanup ends the run. This is bounded discard/retry, not lossless capture or replay.

The supervised follow-up on 2026-10-26 produced two played turns/two playback requests, then failed after three recoveries and a fourth capture loss. Software first audio was **7.95/7.07 s**, with reported TTS timings **600/13 ms** and one cache hit. The owner rated naturalness **4/5**; the earlier **2.5/5** remains historical. Correctness, backchannel preservation and interruption success were not supplied, acoustic metrics remain unmeasured, and SQL booking was unverified (`booking_verified: false`, null commit/restore values). Native quality and the full reliability gate remain open. [Verification](../12-verification.md#phase-6-guided-acceptance--2026-10-26) records source identity and operational limits.

<!-- BEGIN CURRENT LIVE ACCEPTANCE -->

## Current live acceptance

Recorded 2026-10-29T06:53:40.141326Z; source commit `8197ab494d1aed3eb854c99086c01e2aafcf0eb9`. Run `5c1c378b96d64bcf85bd01cdd463a699`; status `interrupted`. Local HMAC receipts establish integrity, not independent acceptance. Acoustic first-audible and playback-stop latency remain unmeasured without independent instrumentation. Software timing and one supervised scenario do not pass the full reliability/concurrency gate.

<!-- END CURRENT LIVE ACCEPTANCE -->


## 2026-10-28 capture investigation and process boundary

The [blueprint comparison](../decisions.md#2026-10-28-remediation-blueprint-review) retains the recorded physical failure and adds diagnostics rather than asserting a GIL/Metal/swap cause. `LocalAudioDevice.capture_health` reports detached content-free **device-lifetime** counters/maxima: callback count/maximum interval, native flags, ADC gaps, queue high-water/capacity/contention, frame age, recovery count, and ordinary 10ms event-loop heartbeat lag. This is neither a priority scheduler nor per-turn/P99/acoustic evidence. Signed physical receipts retain the final snapshot after device cleanup.

`--speech-transport local_http` explicitly uses the already warmed private speech service for ASR/TTS; failure has no in-process or cloud fallback. Native speech artifacts are verified/warmed in that process, not imported for inference by the capture process. VAD/AEC and optional RAG embeddings remain separately accounted work; this is not a guarantee of a <180MB capture process or isolation from host-wide memory pressure. The served TTS content identity is validated through a bounded authenticated endpoint so process separation does not silently reuse PCM across different artifacts.

Completed call execution no longer marks booking verified unless the independent SQL probe observes both commit and restore. No physical microphone run, audio recording, model conversion/download, paid request, new human score or acceptance gate follows from these offline changes.


## Persistent callback playback repair — 2026-10-28

The supervised interruption failure prompted replacement of per-response blocking output writes with a single lazy-open persistent 16 kHz/20 ms callback stream. Normal barge-in advances the generation, flushes at most 80 ms of queued PCM and awaits a callback acknowledgement; it does not call native write/stop/abort concurrently. The 100 ms failure deadline bounds only the callback-acknowledgement wait; it does not bound subsequent DSP/reference draining or driver teardown and is not an accepted <50 ms acoustic result. A stopped callback, native underflow, reference overflow or active-producer starvation fails visibly rather than silently splicing speech.

The callback copies bounded epoch-tagged PCM and records actual native handoff metadata. Resampling, reference rendering and DSP stay outside the callback. Up to 640 ms of handoff receipts remain bounded inside the reference horizon; already-handed DAC-tail references survive interruption, while unplayed queued PCM cannot create echo reference. One reference clock anchors the first admitted handoff’s DAC estimate to monotonic time and advances by every native 20 ms callback, including silence. Per-callback wall-clock estimate differences are reported as bounded uncertainty counters/maxima instead of retiming PCM; the reference still rejects malformed or overlapping receipt data. Playback completion must account for callback-owned in-flight PCM, not just an empty producer queue. Software first-write and active/pending-output observations remain distinct from acoustic onset/stop.

Input/output construction ownership survives caller cancellation; call cleanup adopts and closes a stream created after cancellation. Output construction and normal lifecycle operations are serialized, while the existing input-start cancellation/abort contract is retained. These changes remove the concurrent blocking-output teardown path. Native open/start/abort/close or a stalled DSP worker can still block cleanup; the earlier Python SIGSEGV’s exact cause remains unproven. Independent acoustic alignment/stop measurements and containment of a hung native worker remain acceptance work. See [verification](../12-verification.md) for the final checks and native retry.


## Delivered PCM and timestamp uncertainty — 2026-10-29

Timestamp disagreement alone no longer marks microphone overflow or an incomplete utterance, including full-frame disagreements. `capture_adc_fullframe_uncertainty_frames` and `capture_adc_fullframe_uncertainty_max_ms` report those observations separately from the existing subframe counters. Native input overflow, invalid PCM, bounded-ring capacity loss and callback contention remain authoritative failures; no dropped block is silently joined. The existing small-error alignment adjustment stays capped at 50 μs per callback. This correction changes neither the dated 2026-10-28 outcomes nor their acceptance status, and proves no shared input/output hardware clock or physical echo alignment.
