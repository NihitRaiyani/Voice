# Level 6 — Local voice prototype

**Roadmap sections:** 20–21. **Status:** Section 20 integration implemented and verified offline; actual all-local microphone gate remains pending. Section 21 is not implemented by this increment.

## Entry gate

Levels 2, 3, 4 and 5 core gates remain prerequisites for a passed microphone demonstration. Existing controller/booking/safety and local TTS evidence is reusable, but native speech review and representative ASR evidence remain pending. This Mac has no local ASR runtime/weights; the owner prohibits new model downloads. Authenticated HF Whisper is a separate cloud benchmark, so it cannot satisfy the requirement that OpenAI be the only cloud inference call. No hosted microphone workaround or private-audio upload is added.

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
- [ ] Local ASR runtimes and already-provisioned model caches available on the demonstration machine.
- [ ] Real microphone/headphone conversation with OpenAI in Gujarati, Hindi, English and code-mix, including booking and native review.

Local startup first verifies cached ASR inference for all three language hints, short safe TTS and bundled Silero on synthetic silence before opening the microphone or creating the lab call. No failed model setup can capture participant audio. Actual local startup preflight was exercised: it refuses the missing local-ASR prerequisites before microphone capture, paid OpenAI calls or configured-database writes. OpenAI quota remains a separate prerequisite; automated tests never consume it. The local microphone gate is **not passed**. No ASR weights were downloaded and the production Twilio/Sarvam path remains unchanged.

Return to the [documentation index](../README.md).
