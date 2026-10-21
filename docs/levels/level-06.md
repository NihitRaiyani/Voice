# Level 6 — Local voice prototype

**Roadmap sections:** 20–21. **Status:** Section 20 integration verified offline; physical English/local-TTS checks passed with Claude extraction and, in a prepared-state demo, Claude wording. The complete multilingual/native/booking gate remains pending and the owner deferred further live checks due to limits. Section 21 is not implemented by this increment.

## Entry gate

Levels 2, 3, 4 and 5 core gates remain prerequisites for a passed microphone demonstration. The owner-selected local development ASR is now one MLX 4-bit Whisper Turbo model for all five routes; IndicConformer weights are removed, with base retained for comparison. Exact loanword normalization remains; the slow cascade stays removed. [The 15-case Turbo run](level-04.md#whisper-turbo-replacement--2026-10-21) executes but fails Gujarati/Hindi and full English-suffix recognition. Further paid Claude, microphone and native checks remain deferred; offline tests cannot pass the all-language microphone gate.

## Section 20 implementation

`make voice-prototype` runs a bounded synthetic integration without credentials, network, audio hardware, downloads or working-database writes. The [current Turbo-composition mock report](../../benchmarks/voice/section20-turbo-mock-report.json) exercises Gujarati, Hindi, English and mixed unsafe-fee inputs through endpointing, the single-provider router/normalizer, scripted ASR, controller/safety, synthetic PCM and fake speaker. All four paths play and restore state with one ASR call each. The [Indic single-pass report](../../benchmarks/voice/section20-single-pass-mock-report.json) and [earlier report](../../benchmarks/voice/section20-mock-report.json) are historical. Five route-specific regressions verify controller, timing, storage, safety and playback integration; these checks do not measure real ASR or native quality.

| Boundary | Implementation and invariant |
|---|---|
| Microphone/speaker | `roma/providers/audio/local_device.py`, optional `voice-lab` dependency: bounded 20 ms mono PCM16/16 kHz input, then speaker output at the validated TTS PCM rate. Half duplex: the input stream is closed before controller/LLM/TTS/playback, avoiding self-hearing. Native reads/writes are joined during cancellation; overflow rejects incomplete input, underflow is reported. No retained audio files. |
| VAD/ASR | Reuse bundled Silero, `SpeechInputService` and code-owned endpointing. Local mode accepts cache-only `whisper_mlx` (current Turbo development model) or explicit `whisper` comparison. All five states use one complete selected-model call plus exact loanword normalization. No slicing or secondary decode occurs. Provider failures never trigger fallback. `STT_LANGUAGE` initializes the first ASR hint before any transcript exists; subsequent language decisions remain smoothed controller state. Input is bounded to 30 seconds and the session to five minutes. |
| Controller/booking | Reuse `TextConversationService`, durable checkpoints and `BookingSession` with existing PostgreSQL/Redis stores. SQL confirmation remains authoritative. No fabricated reference slots or demo bookings are seeded by startup. |
| Speech | Production policy remains Hindi-base Hinglish. A prototype-only `voice_hi` prompt requests Devanagari phonetic dialogue; the existing text/live default remains unchanged. Exact discovery/booking/readback/sign-off pronunciations preserve current canonical words and validated slot/branch values. They do not authorize arbitrary translation or safety exceptions. |
| Final safety/TTS | Reuse `SafetyService`/durable event store and local `SpeechSynthesisService`, including approved six-line safety bindings, actual spoken-text checking and phrase-cache identity. Unsupported/unmapped lexical output fails closed; no word dropping or cloud fallback. |
| Durable turns | `PostgresVoiceLabStore` uses existing calls, call_turns and call_events. Every attempt has a short durable receipt; accepted logical turns reuse safety correlation rows. Ready speech is saved before playback; played/failed/cancelled status and timing follow. No SQL session spans inference or audio. The isolated CLI pool is two connections with zero overflow; budget those connections per running lab process. |
| Privacy | Dedicated `voice-lab-…` call IDs. Full caller/agent transcripts require explicit `--persist-transcripts`; default turn/event evidence omits transcript text. Required checkpoint slots still contain participant-provided state. Four-hour retention metadata is recorded, not an assertion of automatic deletion. No audio recording or ordinary transcript logging. |

## Timing and failure behavior

Each attempted turn records available capture, VAD/endpoint, ASR, LLM, routing/controller, final safety/normalization, TTS/cache/framing, waveform duration, playback and total response timings. LLM wall time includes extraction, generation and fallback paths. Routing/controller time includes controller-owned text checks; it is not isolated language-classifier CPU time. TTS inference is distinct from an audited cache hit. These complete-waveform measurements are not model TTFT or end-of-speech-to-first-audible latency.

Persistence failure before playback releases no audio. A final timing-write failure stops the session even when the ready turn was already saved. Provider/audio errors and cancellation retain fixed failure codes and stop unsafe reuse. Empty/failed ASR does not advance the controller or speak a fabricated transcript. No Level 7 full-duplex interruption, acoustic echo cancellation, adaptive queues or carrier change is introduced.

The [five-question local synthesis check](../../benchmarks/voice/section20-fixed-questions.json) reuses installed Indic-TTS runtime/weights and verifies that all exact discovery pronunciations produce nonempty validated PCM. It uses a synthetic in-memory audit sink, no microphone/network/downloads and no native listening approval. A separate [actual booking-line render](../../benchmarks/voice/section20-booking-pronunciation.json) verifies a fixed synthetic Wednesday/October/date/time/branch confirmation; it creates no real appointment or claim of native approval.

## Run and acceptance gate

The [runbook](../runbook.md#section-20-microphone-to-speaker-prototype) owns commands, prerequisites and retention boundaries.

- [x] Offline four-language integration, final safety, persistence-before-playback and failure paths.
- [x] Native disposable PostgreSQL turn/event writes, retry identity, safety-anchor reuse and transcript opt-in tested.
- [x] Local audio adapter overflow/underflow/cleanup/cancellation tested with an injected backend.
- [x] Existing discovery question pronunciations synthesize with the installed Hindi model.
- [x] Complete booking controller/SQL flow passes separately with disposable demo data and offline scripted extraction; this is Level 3 integration evidence, not a microphone booking pass.
- [ ] Levels 2–5 native/representative prerequisite gates approved.
- [x] Local Whisper base runtime and pinned model cache available on this machine; local readiness verified with installed TTS and bundled Silero.
- [x] Pinned IndicConformer runtime/cache and same-corpus local comparison available (12 supported successes / 3 unsupported English cases). Native/code-mix quality remains pending.
- [ ] Real microphone/headphone conversation with Claude in Gujarati, Hindi, English and code-mix, including booking and native review.

The current LLM selection is Claude Haiku 5.5 behind `--llm-provider claude`; `--allow-paid-api` remains required. Explicit `--llm-provider mock` uses real local audio/ASR/TTS without constructing any paid client, and still requires microphone/DB opt-ins. Synthetic mode remains mocked. A mock-LLM hardware result cannot establish Claude quality. No OpenAI calls or fallback.

Local startup first verifies cached ASR inference for all three language hints, short safe TTS and bundled Silero on synthetic silence before opening the microphone or creating the lab call. No failed model setup can capture participant audio. The previous missing-runtime rejection is superseded by the [actual local readiness check](../../benchmarks/voice/section20-local-readiness.json): local base ASR, installed TTS and bundled Silero passed in 29.826 seconds without capture, any LLM call or DB writes. Claude API availability is a separate prerequisite; automated tests never consume it. The local microphone gate is **not passed**. Only the explicitly approved Whisper base snapshot was downloaded; the production Twilio/Sarvam path remains unchanged.

Return to the [documentation index](../README.md).


## Local base microphone follow-up — 2026-10-19

Only the pinned multilingual Whisper base snapshot was provisioned. The local readiness evidence confirms ASR, installed TTS and bundled Silero are available. Configured `roma` was read-only confirmed at head, and its existing local Redis endpoint was started without changing `.env`. The first physical English capture finalized on silence with **1.921 s ASR processing**; no LLM generation ran (`llm_ms=0`, no OpenAI metrics), and playback failed. A second explicit mock-LLM attempt timed out without speech; neither attempt passes the microphone gate.

The first failure exposed an existing approved re-offer pronunciation gap: absent branch setup selects booking-unavailable wording, and the unchanged confirmation filter selects `SAFE_REOFFER_LINE`. Both exact existing re-offer variants now have finite Hindi phonetic bindings in `voice_pronunciation.py`, following the existing constant-equality guards. Additional words do not match a binding. Canonical wording, business/safety rules, fixed production audio and booking authority are unchanged. [Actual local synthesis](../../benchmarks/voice/section20-reoffer-pronunciation.json) passed both variants with normal safety checks and no OpenAI/downloads; native listening approval remains separate. No branch/slot was seeded to make the lab appear bookable.


After the exact pronunciation repair, the [physical English microphone report](../../benchmarks/voice/section20-base-microphone-mock.json) records one completed turn: actual microphone → bundled Silero/endpointing → local base ASR → controller/safety → installed local TTS → physical speaker/headphones. OpenAI configuration/client construction was explicitly forbidden in this run; the mock LLM was selected, and no LLM generation was needed for this deterministic response. ASR finalized on silence in **585 ms**; cold TTS took **8.768 s**, playback **5.160 s**, and response processing including playback **14.041 s**. These are boundary timings, not first-audible latency or p95. No microphone audio was retained or uploaded.

Configured `roma` readback confirms the isolated call is `completed` with one `played` event, `error_code=null` and transcript retention disabled. The owner confirmed **“Heard it clearly”**. This is a single English-input/Hindi-base Hinglish-response hardware integration pass; it does not approve multilingual ASR, all stages, booking, interruptions or native language quality. Branch/reference-slot setup was absent, so the unchanged controller used its approved re-offer rather than inventing availability. OpenAI remains the main LLM for later authorized quota-available testing; all further API calls remain deferred.


The preceding 2026-10-19 results retain their original provider exclusions. On 2026-10-20 the owner replaced OpenAI with Claude Haiku 5.5 and authorized synthetic paid Claude verification. The physical turn remains mock-LLM evidence; native multilingual, booking and complete Claude microphone gates remain pending.


## Claude provider replacement — 2026-10-20

The local CLI now accepts only `--llm-provider claude|mock`; Claude additionally requires `--allow-paid-api`, while the default synthetic path and real-hardware mock mode remain quota-free. No OpenAI selection/fallback is available on active paths. Existing ASR/TTS, microphone ownership, short DB writes and timings remain intact. Actual synthetic Claude text passed safety and rendered with installed local TTS; no fresh microphone session, new weights, schema migration or booking seed was performed for this provider replacement. The full Claude Gujarati/Hindi/English/code-mix microphone gate is still pending prerequisite ASR/native approval and a separately coordinated physical demonstration.


## Real Claude English microphone follow-up — 2026-10-20

The owner explicitly authorized physical English testing with Claude Haiku and the existing cache-only Whisper base. [Working-DB evidence](../../benchmarks/voice/section20-claude-english-hardware.json) records three actual microphone/endpoint/ASR/controller/safety/local-TTS/headphone turns, all saved as played without errors in `roma` at the unchanged head. Turn 3 used a valid real Claude student-status extraction (one attempt, model TTFT 2.401 s, 830 input / 29 output tokens, 806 cache-creation tokens, estimated rounded charge ₹0.02). First-turn ASR took 585 ms; the following two took 579/555 ms. The owner confirmed “Yes, all three were clear.” These turns used fixed approved speech because `roma` has zero branches/appointment slots; they do not establish generated dialogue or a booking.

Readback exposed a PostgreSQL checkpoint defect: immutable nested durable-record data restored the English language route as the default and left controller collections frozen. The adapter now converts the full nested JSON into a detached mutable copy before `CallState.from_dict`. A native disposable-PostgreSQL regression failed before the fix, then passed for English/history, slot attempts, objections, offers, facts and a second save/restore. Read-only reload of the actual microphone call now preserves English, discovery and filled name/status slots, with education next. No saved checkpoint or schema was rewritten.

The owner approved clearly labelled demo references. They were created only in a disposable loopback PostgreSQL instance using existing migrations: one TEST ONLY institute/branch and two future test slots. No working `roma` reference data was seeded. [Failure observations](../../benchmarks/voice/section20-claude-failures.json) retain a silent/coordination-limited attempt that hit the existing five-minute ceiling and a prepared-state Claude reply whose unsupported Latin words caused local TTS to stop before playback. A synthetic reproduction identified the worker vocabulary rejection; no lexical characters were silently dropped.

The repair is limited to the local `voice_hi` dialogue seam: explicit Devanagari loanword/acronym spelling, one approved fact, a short task, the configured bounded output ceiling, audited substitution for unsupported generated script, and rejection of output cut off at the token limit. Existing fixed discovery/booking pronunciations and approved fallbacks remain exact. Other dialogue policies, model weights, safety wording, booking authority and production speech are unchanged.

The [repaired prepared-state check](../../benchmarks/voice/section20-claude-english-prepared.json) starts from declared synthetic VALUE/profile data; it is not a fresh full conversation. Four real English microphone turns completed with local Whisper base, safety, installed local TTS/cache and headphones. Two replies used actual Claude wording and a later turn used Claude time extraction. All four SQL played events had no error; checkpoint restore retained English/PIVOT. The owner confirmed “Yes, clear and understandable.” A temporary operator harness used a short tone to coordinate capture; this is not new standard CLI behavior. The demo database was removed after readback.

The repaired run recorded three metered Claude requests (estimated rounded total ₹0.09) and no OpenAI, hosted ASR or new downloads. It ended with an offer and **zero confirmed appointments**; it does not pass slot acceptance/readback/commit. Model TTFT ranged 0.984–1.926 s; these few controlled samples are not p95 or end-of-speech-to-first-audible latency. TTS cold inference was roughly 8.6–10.1 s; the repeated offer used the audited cache. Full caller/agent transcripts and microphone recordings were not retained; ordinary checkpoint slots still contain necessary state.

The owner deferred Hindi, Gujarati, code-mix and formal native scoring due to limits. No further paid or microphone requests are authorized by this follow-up after that deferral. Representative ASR quality, fresh complete-stage conversation, booking acceptance/commit, approved real availability and the remaining Levels 2–5 human gates stay open. The section 20 gate remains pending; section 21 is outside scope.

On 2026-10-21 the same production booking path was completed offline with scripted extraction and disposable PostgreSQL/Redis demo data: offer, exact readback, explicit affirmation, durable commit, SQL restore and competing-caller re-offer all passed. The stronger output assertion found and repaired a deterministic conflict re-offer wording/pronunciation interaction; booking atomicity and schema required no change. This resolves the offline booking-code evidence gap only; the microphone booking demonstration, paid Claude run and native multilingual review remain deferred.

## Single-pass integration follow-up — 2026-10-21

The owner-selected routing simplification is connected to the existing local hardware composition and the default mock lab. The [fresh offline report](../../benchmarks/voice/section20-single-pass-mock-report.json) records four played paths, one ASR call each, preserved safety, per-stage timing and restored state. Five additional regressions exercise the real `SpeechInputService` → `LocalLanguageSTT` → controller/safety → synthetic TTS/speaker path for Gujarati, Hindi, English and both mixed states; exact emitted loanwords arrive canonicalized before the controller. CI now explicitly runs the local-router tests.

The existing complete booking/readback/affirmation/SQL commit/restore/conflict path, short durable turn writes and safety anchors are reverified in disposable PostgreSQL/Redis tests. The working database receives only the separately identified aggregate ASR benchmark evidence; no demo slots, schema or business records are added. The [audio report](../levels/level-04.md#single-pass-local-routing--2026-10-21) still fails long English code-switch recognition, despite faster mixed decoding.

Section 20 code and offline integration are present. The roadmap explicitly requires an actual microphone conversation in all three languages; paid Claude, physical microphone and native-speaker scores remain deferred by the owner, so that gate is pending. No offline fake is reported as a live/native pass. Section 21 remains outside scope.

## Turbo composition follow-up — 2026-10-21

The owner-approved single-model replacement is wired into the isolated local prototype and WAV lab with mock defaults. The default four-path report and five route regressions preserve final safety, controller authority, durable timing/turn seams and state restore. Real Turbo inference is measured separately in [Level 4](level-04.md#whisper-turbo-replacement--2026-10-21) and fails the recognition quality prerequisite. No paid Claude, physical microphone, new demo booking or production switch was run. The section 20 gate remains pending; no later section is started.
