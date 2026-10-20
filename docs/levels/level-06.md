# Level 6 — Local voice prototype

**Roadmap sections:** 20–21. **Status:** Section 20 integration verified offline and one physical English microphone/local-TTS turn passed with a mock LLM; the complete Claude/native microphone gate remains pending. Section 21 is not implemented by this increment.

## Entry gate

Levels 2, 3, 4 and 5 core gates remain prerequisites for a passed microphone demonstration. Existing controller/booking/safety and local TTS evidence is reusable, but native speech review and representative ASR evidence remain pending. The owner subsequently approved only multilingual Whisper base; its optional runtime and pinned local weights are installed. The separate 15-case run completed, but Gujarati/Hindi/stitched quality failed, so the model is an integration baseline. Authenticated HF Whisper is a separate cloud benchmark, so it cannot satisfy the requirement that Claude be the only cloud inference call. No hosted microphone workaround or private-audio upload is added.

## Section 20 implementation

`make voice-prototype` runs a bounded synthetic microphone-to-speaker integration without credentials, network, audio hardware, model downloads or working-database writes. [The report](../../benchmarks/voice/section20-mock-report.json) exercises Gujarati, Hindi, English and Latin code-mix unsafe-fee inputs through endpointing, scripted ASR, the real language/controller/safety flow, PCM synthesis and a fake speaker. Four paths pass; this is not a native conversation or audio-quality benchmark.

| Boundary | Implementation and invariant |
|---|---|
| Microphone/speaker | `roma/providers/audio/local_device.py`, optional `voice-lab` dependency: bounded 20 ms mono PCM16/16 kHz input, then speaker output at the validated TTS PCM rate. Half duplex: the input stream is closed before controller/LLM/TTS/playback, avoiding self-hearing. Native reads/writes are joined during cancellation; overflow rejects incomplete input, underflow is reported. No retained audio files. |
| VAD/ASR | Reuse bundled Silero, `SpeechInputService` and code-owned endpointing. Local mode accepts only cache-only IndicConformer/Whisper adapters. Indic Gujarati/Hindi routes explicitly use Whisper for the saved English route; no failure-driven or cloud fallback. `STT_LANGUAGE` initializes the first ASR hint before any transcript exists; subsequent language decisions remain smoothed controller state. Input is bounded to 30 seconds and the session to five minutes. |
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
- [ ] Levels 2–5 native/representative prerequisite gates approved.
- [x] Local Whisper base runtime and pinned model cache available on this machine; local readiness verified with installed TTS and bundled Silero.
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
