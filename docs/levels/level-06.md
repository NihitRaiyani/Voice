# Level 6 — Local voice prototype

## Current readiness

The owner resumed bounded paid Claude and supervised microphone checks on 2026-10-22. English, Hindi, Gujarati and a mixed-input trial played replies with local Turbo/Indic-TTS; the owner reported **“All four were clear and relevant.”** A natural-consent defect was reproduced and repaired, and a real English consent produced one confirmed TEST ONLY appointment with correct SQL restore. [Live evidence and remaining limits](#supervised-claude-and-booking-follow-up--2026-10-22) supersede earlier deferral statements for this bounded follow-up. Formal native scores, representative recognition and acceptable voice latency remain pending; no full gate passes. [Section 21 structured logging](#section-21--structured-logging) is now implemented and verified offline.

The half-duplex prototype uses **Whisper Turbo 4-bit MLX** for current local development ASR; IndicConformer ASR weights were removed. Earlier base/Indic installation and English microphone results below are historical evidence, not approval of the current complete voice experience. No gate is marked passed.

Setup now validates repo-local `punkt_tab` before tests, app startup and prototype startup. Prototype errors use the shared ASR/TTS/DB/DEVICE/CAPTURE/LLM/SAFETY/BOOKING-DATA categories. Phase 2 adds a shared turn policy, continuation grace, deadline flushing and audio-structure batch eligibility. Phase 3 adds batch-row rejection, explicit turn timing, optional unset budgets and disposable booking-data tooling. Phase 4 adds metadata/scoring tooling; representative audio collection, real-model timing and native acceptance remain pending. Those phases deferred paid/microphone tests; the owner subsequently resumed the bounded follow-up below. Formal native-speaker scoring remains pending; the production Twilio/Sarvam path is unchanged.

**Roadmap sections:** 20–21. **Status:** Section 20 integration is verified offline, with supervised English/Hindi/Gujarati/mixed-input Claude/local-TTS checks and one committed TEST ONLY microphone booking plus SQL restore. Formal native/representative and voice latency approval remain pending. Section 21 adds scoped JSON events and request/call/turn correlation; offline verification is recorded below.

## Entry gate

Levels 2, 3, 4 and 5 core gates remain prerequisites for a passed microphone demonstration. Local development ASR remains pinned Turbo 4-bit; structural batching uses at most two complete speech chunks with context-aware language selection and reference-compatible waveform padding; no-gap single-call decoding remains unchanged. [The new 15-case evidence](level-04.md#single-model-speech-batching--2026-10-21) recovers English output but does not pass Gujarati/Hindi/noisy quality or voice latency. The bounded 2026-10-22 paid/microphone follow-up is recorded below; formal native scores remain pending. Offline tests alone cannot pass the all-language gate.

## Section 20 implementation

`make voice-prototype` runs a bounded synthetic integration without credentials, network, audio hardware, downloads or working-database writes. The [current batch-composition mock report](../../benchmarks/voice/section20-turbo-batch-mock-report.json) records four played/restored paths with one provider request each. Scripted ASR does not execute or measure actual model batching. [The whole-turn Turbo mock](../../benchmarks/voice/section20-turbo-mock-report.json), [Indic single-pass mock](../../benchmarks/voice/section20-single-pass-mock-report.json) and [original mock](../../benchmarks/voice/section20-mock-report.json) remain historical. Five route integrations plus batch-specific tests cover metadata, timing, storage, controller/safety and playback; [Level 4](level-04.md#single-model-speech-batching--2026-10-21) owns actual ASR evidence.

| Boundary | Implementation and invariant |
|---|---|
| Microphone/speaker | `roma/providers/audio/local_device.py`, optional `voice-lab` dependency: bounded 20 ms mono PCM16/16 kHz input, then speaker output at the validated TTS PCM rate. Half duplex: the input stream is closed before controller/LLM/TTS/playback, avoiding self-hearing. Native reads/writes are joined during cancellation; overflow rejects incomplete input, underflow is reported. No retained audio files. |
| VAD/ASR | Reuse bundled Silero, `SpeechInputService` and code-owned endpointing. Local mode accepts cache-only `whisper_mlx` (current Turbo development model) or explicit `whisper` comparison. Pure states retain one full selected-model call. Explicit mixed states can use the owner-approved two-chunk batch, retaining all PCM and one ASR model plus exact loanword normalization. No second-model cascade or retry occurs. Provider failures never trigger fallback. `STT_LANGUAGE` initializes the first ASR hint before any transcript exists; subsequent language decisions remain smoothed controller state. Input is bounded to 30 seconds and the session to five minutes. |
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
- [x] **Historical:** Pinned IndicConformer runtime/cache and same-corpus local comparison available at the earlier installation checkpoint (12 supported successes / 3 unsupported English cases). The ASR weights were subsequently removed; this is historical evidence only. Native/code-mix quality remains pending.
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

## Single-model batching integration follow-up

The explicit owner approval for speech-chunk batching supersedes the earlier whole-turn-only constraint. Mixed route metadata now reaches the same Turbo adapter through both local microphone composition and WAV lab; actual microphone testing remains deferred. The default four-path mock remains offline, and the existing durable booking/controller/safety/TTS seams are unchanged. Recovered benchmark English does not pass native recognition or first-audible latency; the section 20 gate remains pending.


## Offline turn-boundary repair — Phase 2

The shared fake-clock/audio/ASR [integration harness](../../tests/support/voice_turns.py) drives the real endpoint detector, speech input, local language wrapper, router and controller. This proves orchestration, not recognition quality. The prototype remains half-duplex; input closes before the controller and speaker run.

[TurnPolicy](../../roma/domain/audio/turn_policy.py) owns the provisional defaults: endpoint silence 512 ms, continuation grace 300 ms, minimum internal batch gap 256 ms, maximum utterance 30,000 ms and minimum batch turn 2,000 ms. Configure `VOICE_ENDPOINT_SILENCE_MS`, `VOICE_CONTINUATION_GRACE_MS`, `VOICE_BATCH_GAP_MIN_MS`, `VOICE_MAX_UTTERANCE_MS`, `VOICE_BATCH_MIN_TURN_MS`. The batch gap must be smaller than the base endpoint window. The prototype defers finalization through the grace window; legacy standalone endpoint configurations remain explicit.

| Pause between sentences (ms) | Harness outcome with default grace | Second sentence retained |
| --- | --- | --- |
| 256 | Same turn | Yes |
| 384 | Same turn | Yes |
| 448 | Same turn | Yes |
| 512 | Same turn | Yes |
| 640 | Same turn | Yes |
| 800 | Same turn | Yes |

Silero's 32 ms frame rounding makes the 812 ms endpoint-plus-grace window 832 ms. Relative to zero grace, the synthetic run adds 320 ms; device block alignment can add up to a further 20 ms. This is deliberate latency, not a measured live response improvement. Continuous speech is flushed at the capture deadline, with original source bytes retained and `endpoint_reason=deadline`; empty ASR there raises `CAPTURE-DEADLINE-NO-TRANSCRIPT`. The five-minute session ceiling still bounds capture and response.

Batch eligibility now examines qualifying audio structure on all five saved language routes, rather than requiring a previously mixed label. No qualifying gap retains the original single-call audio/options. At most two contiguous chunks retain all PCM; English needs probability ≥0.8 and stronger evidence than the known Indic context. Saved language changes still require two matching informative turns. The harness assigns 5 ms to a single decode and 10 ms to a batch, hence 5 ms **simulated** extra ASR time for a qualifying paused pure-language turn. Real model batch overhead is unmeasured in this work; it is not a latency claim. Native quality, paid Claude, microphone and all gates remain pending.


## Offline output and demo readiness — Phase 3

The batch decoder rejects individual rows with `no_speech_prob > 0.6`, `avg_logprob < -1.0`, or compression ratio >2.4, and rejects invalid/non-finite metadata. These are configurable provisional thresholds (`WHISPER_BATCH_NO_SPEECH`, `WHISPER_BATCH_AVG_LOGPROB`, `WHISPER_BATCH_COMPRESSION_RATIO`). Thresholds start at Whisper's numeric defaults; our batch policy independently rejects each failing criterion, with no temperature retry. Clean rows remain in order; all rejected rows produce empty text and `ASR-BATCH-REJECTED`, without controller advancement or audio. The ordinary single-call decoding path is unchanged.

The prototype measures last scored speech-frame end to the first speaker write. Read-block timestamps retain device startup/ASR timing boundaries; VAD resolution remains 32 ms. First write is the software output boundary, not proof of acoustic headphone latency. Reports show endpoint wait, ASR, LLM, safety (including safety normalization/audit), TTS/cache/framing and playback-start, plus independently measured total. Controller and DB overhead need not sum to those stage columns. Full playback duration is separate.

| Stage | Shared fake-clock harness (ms) |
| --- | ---: |
| Endpoint wait | 844 |
| Scripted ASR | 5 |
| Mock LLM | 0 |
| Safety/normalization | 0 |
| Synthetic TTS/cache | 0 |
| Playback start | 0 |
| Speech end → first write | 849 |

Zeroes mean instantaneous fake-clock work, not zero-cost real inference. The synthetic audio duration is 100 ms; the fake output advances its clock by 1 ms for the write. `VOICE_TURN_BUDGET_MS` defaults to unset. Optionally supply a JSON object keyed by `endpoint_wait_ms`, `asr_ms`, `llm_ms`, `safety_ms`, `tts_ms`, `playback_start_ms` or `speech_to_first_audio_ms`. Only configured thresholds can report `over_budget`; no acceptance threshold or gate pass is claimed.

`make demo-db` explicitly starts a separate native loopback PostgreSQL on a free port, migrates it and seeds TEST ONLY institute/course/branch codes and tomorrow-through-three-days slots at 11:00, 15:00 and 17:00 IST. The branch's canonical name remains Vadodara to satisfy the existing controller allowlist; its code is `TEST_ONLY_VADODARA`, under a TEST ONLY institute. The 18:00 cutoff is preserved. Copy the printed `DATABASE_URL` into the lab shell; never substitute the working `roma` URL. `make demo-db-down` stops/removes only the owned disposable directory. Neither command runs at app startup. Non-loopback, database `roma` and environments outside dev/test are refused.

Before models, Redis, call creation or microphone construction, a read-only short session checks active branches and available future/unoccupied slots for the selected branch, respecting the 90-minute lead. Empty data produces `BOOKING-DATA-MISSING` rather than speaking a repeated re-offer. Empty/seeded disposable DB and CLI ordering tests prove the check. No working database, real microphone or paid provider was used; native and complete voice gates remain pending.

## Supervised Claude and booking follow-up — 2026-10-22

The [paid smoke](../../benchmarks/voice/section20-claude-smoke-20261022.json) records three actual Claude Haiku requests from synthetic English/Hindi/Gujarati input, successful bounded streaming and usage/TTFT metrics, with Hindi-base Hinglish output and deterministic safety. The [supervised microphone report](../../benchmarks/voice/section20-turbo-live-20261022.json) retains every completed attempt, setup failure and error. Local Turbo, Silero/endpointing, controller, unchanged Claude provider, safety, installed Indic-TTS/cache, speaker and disposable PostgreSQL/Redis ran on this Mac. No audio or full transcripts were retained, and no audio was uploaded. The owner confirmed the four language-labelled replies were clear and relevant.

The live check exposed a deterministic consent defect: the conservative vocabulary rejected natural phrases such as “Yes, please confirm it.” Exact reviewed English/Hindi/Gujarati consent phrases now work after an existing readback. Questions, negations, tentative wording, revisions and consent without a selected readback still cannot commit. A clear CLOSE-stage consent skips unnecessary LLM slot extraction; date/time changes retain extraction and normal validation. Red-before/green-after tests cover both vocabulary and the unavailable-extractor seam. The database remains the final authority and the spoken confirmation follows commit.

The final prepared PIVOT/profile check selected **2026-10-23 15:00 at Vadodara**, spoke the readback, received real microphone consent, committed and played the approved confirmation. The [database verification](../../benchmarks/voice/section20-booking-database-20261022.json) proves exactly **one confirmed appointment**, its booked slot, and authoritative same-SID SQL restore. Final consent has **0 ms LLM time**, **1.332 s ASR**, and **12.241 s speech-end to first software speaker write**. This includes **9.841 s TTS**, which dominates the remaining delay. No voice latency budget is configured; acoustic first-audible latency was not measured by loopback.

The report contains **15 attempts: 13 played, 12 played without an error code, two no-speech CAPTURE results, and one played safe response with an LLM/invalid-extraction error**. Earlier booking attempts stopped at a readback and are explicitly uncommitted. The two CAPTURE attempts never called ASR. Operator announcements must follow the actual `CAPTURE_STARTED` signal so short replies are not requested during tool/setup delay. A resumed checkpoint was exercised within its original time limit; the final successful booking used a fresh bounded demo call. No failed attempt was deleted or counted as a booking success.

English/Hindi/Gujarati ASR request times in the language checks were about **1.40 / 1.55 / 1.50 s**; mixed input **2.77 s**. Speech-end to first speaker write was about **14.16 / 14.65 / 13.63 / 15.25 s**, respectively, dominated by cold synthesis of new lines. Cached readbacks took tens of milliseconds. The mixed trial's observed router label was **ENGLISH**; owner relevance/clarity approval does not prove verbatim code-switch retention or route classification. These are prepared-state functional checks rather than a new full discovery journey or a representative corpus. Numeric native-speaker scores were not supplied, so the existing 40-case/native rubric remains pending.

Only TEST ONLY demo data was used: seven calls, 13 logical turns, 42 events, one appointment, zero stored transcripts. The working `roma` database, production Twilio/Sarvam, `.env`, Claude model/settings and TTS implementation remain unchanged. The newly authorized cache retirement and ASR padding fix are owned by [Level 4](level-04.md#waveform-padding-repair-and-model-retirement--2026-10-22). No new section or gate is marked passed.


## Section 21 — Structured logging

The existing stdlib logger now emits one JSON object per record, without a new dependency. Its optional logging-only settings read secret values for redaction without requiring provider credentials or constructing clients. `roma/core/logging.py` owns the event vocabulary, formatter, context and ASGI request middleware; `roma/main.py` installs the middleware and `scripts/voice_prototype.py` configures the same logger. `VoicePrototype` emits correlated operational events, retaining the existing stdout CLI reports and headphone instructions as user-facing output.

| Field | Meaning |
|---|---|
| `timestamp`, `level`, `event` | UTC ISO-8601 emission time, severity and a reviewed snake_case event name |
| `correlation_id` | Stable, server-generated ID for one prototype instance; request-scoped for HTTP/WebSocket |
| `request_id` | New server-generated ID per HTTP/WebSocket connection or prototype turn attempt |
| `call_id` | Existing provider/lab call identity; never a caller phone number |
| `turn_id` | Prototype attempt number, including rejected ASR attempts; accepted controller `turn_count` remains separate |
| `stage`, `language` | Code-owned conversation stage and normalized `gu`, `hi`, `en`, `gu-en` or `hi-en` route |
| `latency_ms` | Finite, nonnegative elapsed stage time; null for events without a measurement |
| `error_code`, `status`, `status_code` | Optional reviewed voice error enum, prototype outcome or HTTP status |

Events: `request_completed`, `request_failed`, `turn_started`, `asr_completed`, `controller_completed`, `llm_completed`, `llm_failed`, `safety_completed`, `tts_completed`, `playback_completed`, `turn_completed`, `turn_failed`. The LLM event measures aggregate extraction/generation time for that turn, when an inference was attempted; it is not model TTFT. A played safe fallback may have `turn_completed` plus an `error_code`; completion is not a claim that the provider or a quality gate passed. Safety timing includes the existing controller and final speech checks. ASR completion is logged from the received transcript's measured ASR duration; it does not invoke ASR again.

ContextVars isolate concurrent calls and restore outer context on failures/cancellation. Child tasks inherit the scope. HTTP and WebSocket accept responses include `X-Request-ID` and `X-Correlation-ID`; caller-supplied header values are not trusted or reflected. A call ID remains the join key across separate carrier requests. Request completion latency measures the request/connection lifetime, rather than model latency.

Only approved metadata is serialized. Caller/agent text, prompts, names, dates/slot values, phones, credentials, raw exceptions, paths, query strings and arbitrary extras are omitted. Existing redaction filters remain installed, including Uvicorn's independently handled logs. Unconverted free-form records become `legacy_log` with available context and severity; their messages/tracebacks are intentionally not emitted. Third-party SDKs with independent logging sinks are outside the stdlib formatter contract. No transcript retention, durable event schema, provider, model, booking, safety policy, half-duplex behavior or working database is changed.

Offline verification: **2,953 tests passed** in both the isolated CI invocation and full test suite, with 22 existing warnings; Ruff/mypy and 615 local Markdown links passed. The mock CLI emitted 38 valid JSON records for four synthetic paths. See [Section 21 verification](../12-verification.md#section-21-structured-logging). This increment does not pass the outstanding Level 6 native, representative-ASR or voice-latency gates, and does not implement Level 11 tracing/dashboards.

## Supervised acceptance follow-up — 2026-10-25

The owner resumed supervised microphone/Claude acceptance with an aggregate INR50 ceiling, fictional details and isolated TEST ONLY PostgreSQL/Redis. Each invocation's full configured INR2 allowance was reserved before launch; three launches retain INR6 conservatively reserved, including interruption, rather than claiming an invoice charge. No caller audio/transcripts were retained, no working booking data was seeded and no speech/provider/weight setting changed.

The interrupted first launch ended as failed with zero durable turns. Two subsequent English-route discovery turns played: speech end to first **software** speaker write was **11.555 s** and **10.324 s**, with local TTS **9.134 s** and **7.895 s**. Both successful replies were deterministic (`llm_ms=0`); this does not exercise the paid Claude conversation. The first run then failed immediately on a second native capture. A private exception-type/frame-only diagnostic did not reproduce that exception: it produced one response followed by two 30-second no-speech attempts. Exit zero therefore does not establish conversation success. The immediate capture cause remains unresolved; repeated fake captures cannot prove native restart reliability.

The owner explicitly scored **naturalness 2.5/5**. Correctness and reviewer initials were not supplied; do not fill them from the naturalness score. A single safe synthetic discovery phrase rendered at 8 kHz and 16 kHz from the same native waveform received **“No improvement”**. Playback configuration remains unchanged. This is a failed quality observation, not formal multilingual native acceptance. [Sanitized voice evidence](../../benchmarks/voice/acceptance-followup-20261025.json) and [TTS profiling](level-05.md#cold-start-profile-and-owner-review--2026-10-25) own these limits.

The CLI now emits flushed `CAPTURE_STARTED` only after stream start, and supports explicit pause/resume with the original SQL identity, attempt count and five-minute lifetime. Resume checks SQL/local tombstones before cache load, then rechecks tombstones and validates terminal/expiry/deletion metadata after acquiring ownership. It reconciles existing committed appointments without a new capture/commit. Maximum wall/monotonic elapsed time prevents sleep or backward wall-clock changes from extending a call; input/controller and speaker-write checks reject expired work. Stage timing remains monotonic. These mocked/disposable SQL controls are separate from the unfinished physical interruption/booking/quality gate.

<!-- BEGIN CURRENT LIVE ACCEPTANCE -->

## Current live acceptance

Recorded 2026-10-29T06:53:40.141326Z; source commit `8197ab494d1aed3eb854c99086c01e2aafcf0eb9`. Run `5c1c378b96d64bcf85bd01cdd463a699`; status `interrupted`. Local HMAC receipts establish integrity, not independent acceptance. No new human listening rating recorded. Historical scores remain below. Full multilingual/native acceptance stays open.

<!-- END CURRENT LIVE ACCEPTANCE -->


The 2026-10-28 follow-up run `54f0aac06e474b919985b7248baf0add` received the owner's actual **4/5 Hindi naturalness** score, with correctness unreported. Its eight played turns preceded the final Devanagari consent grammar fix; no booking was verified. The subsequent short retry recorded no new human score and again failed to verify booking because recognition changed the affirmative text. Preserve these separate source/run identities; [final verification](../12-verification.md#full-duplex-repair-and-native-retries--2026-10-28) records capture/provider limits and the INR30/50 reservation total.
