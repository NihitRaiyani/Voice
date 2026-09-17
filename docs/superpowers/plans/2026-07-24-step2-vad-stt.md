# Step 2 — STT + VAD in (draft plan, for approval)

## Context / goal
docs/10 Step 2: wire **Silero VAD** (docs/05 Layer 1) + **Sarvam Saaras STT streaming**
into the existing Step-1 media spine so that **real transcripts arrive** on a live 8kHz
μ-law call, and **measure Saaras behavior on 8kHz** (feeds the docs/decisions WER test).
This is the "transcripts + measurement" slice — NOT the full endpointing/barge-in design.

Step-1 gave us `transport.input() → InboundAudioCounter → transport.output()` in
[media.py](src/roma/telephony/media.py). Step 2 inserts VAD + STT into that path.

## Current APIs (confirmed via context7 — not memory)
- **VAD:** `pipecat.audio.vad.silero.SileroVADAnalyzer(sample_rate=8000, params=VADParams(...))`.
  `VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.2, min_volume=0.6)` are defaults;
  Silero supports 8000 Hz natively (our μ-law rate). Model-based, per docs/05 (never energy gate).
- **STT:** Pipecat ships a **built-in** `pipecat.services.sarvam.stt.SarvamSTTService` — no
  hand-rolled websocket client needed. Example shape:
  ```python
  SarvamSTTService(
      api_key=...,
      mode="codemix",  # ← see decision D2
      settings=SarvamSTTService.Settings(model="saaras:v3", language=Language.GU_IN),
  )
  ```
  Emits `TranscriptionFrame` (final, has `finalized` flag) + `InterimTranscriptionFrame`
  (partial); event handlers `on_speech_started` / `on_utterance_end`.
- **Sarvam Saaras v3 modes** (raw API): `transcribe | translate | verbatim | translit | codemix`.
  **`codemix`** = "transcribe code-mixed speech (e.g. Hindi-English) naturally, code-mixed
  output" — the right mode for Roma. `saarika:v2.5` is the legacy single-language model.
- **Smart-turn (semantic endpointing), current API:** `LocalSmartTurnAnalyzerV3` +
  `TurnAnalyzerUserTurnStopStrategy` wired via `LLMContextAggregatorPair` / `UserTurnStrategies`.
  This is the modern replacement for hand-tuned silence timers — but it lives on the LLM context
  aggregator, which doesn't exist until Step 3/4. **Deferred (see scope).**

## Scope — what Step 2 IS and ISN'T
IN (this step):
1. `SileroVADAnalyzer` on the transport input (8kHz).
2. `SarvamSTTService` (saaras:v3, codemix) in the pipeline after `transport.input()`.
3. A small `TranscriptionLogger` processor that logs interim + final transcripts (the
   audio-IN proof evolves into a transcript-arrives proof). Keep `InboundAudioCounter` too.
4. **Measurement:** on a live/recorded 8kHz call, record STT finalize latency (~200–300ms
   target, docs/02) and eyeball WER on code-mix Gujarati/Hindi/English → docs/decisions WER test.

OUT (deferred — do NOT build here):
- Adaptive endpointing (500/850/1300ms), continuation-marker list, **backchannel guard** →
  docs/05 Layer 2 nuance, lands with the phase machine / barge-in (Steps 4–5).
- **Barge-in / interruption** (docs/05 Layer 3) → Step 5.
- LLM, sentence-chunk, filter-in-path, TTS → Step 3.
- Smart-turn analyzer → arrives with the context aggregator in Step 3/4.

## Wiring changes (media.py) — pattern, not full code
Insert VAD on the transport params and STT into the pipeline:
```
FastAPIWebsocketParams(
    audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
    audio_in_sample_rate=8000, audio_out_sample_rate=8000,
    vad_analyzer=SileroVADAnalyzer(sample_rate=8000,
                                   params=VADParams(stop_secs=0.85)),   # D3
    serializer=serializer,
)
...
stt = SarvamSTTService(api_key=settings.sarvam_api_key.get_secret_value(),
                       mode="codemix",
                       settings=SarvamSTTService.Settings(model="saaras:v3",
                                                          language=Language.GU_IN))
Pipeline([transport.input(), counter, stt, TranscriptionLogger(), transport.output()])
```
- Secret read stays at the boundary (`.get_secret_value()`), per docs/07.
- `SarvamSTTService` needs the `sarvam` extra — add `pipecat-ai[sarvam]` (or the sarvam SDK)
  to `pyproject.toml` `[telephony]` and `uv sync`. Verify the exact extra name at implementation.

## Open decisions to resolve at implementation
- **D1 — endpoint ownership.** Who declares "user turn done": Silero `stop_secs` (simple,
  fixed ~850ms), Sarvam's own streaming endpointing (`high_vad_sensitivity`), or smart-turn?
  **Recommend:** Step 2 uses Silero `stop_secs≈0.85` as the single endpoint timer (simplest
  thing that meets "endpoint-on-final at 850ms"); avoid double-VAD by not also enabling Sarvam
  high-sensitivity endpointing unless measurement shows we need it. Smart-turn is the Step-4/5
  upgrade. Confirm this is the intended reading of docs/05 before wiring.
- **D2 — Pipecat exposes `mode="codemix"`?** The Sarvam raw API supports it; confirm
  `SarvamSTTService` passes `mode` through (context7 example used `mode="transcribe"`). If the
  wrapper doesn't expose codemix, either pass it via settings or a thin subclass.
- **D3 — primary `language_code`.** `gu-IN` (Gujarati-primary) vs `hi-IN`. codemix handles the
  English mixing; the primary matters for the Gujarati/Hindi split. The WER test picks the winner.
- **D4 — cost during test.** Saaras is the real limiter at **₹4.50/call (v2)** (docs/02) — verify
  v3 pricing. Prefer feeding **recorded 8kHz audio** through the pipeline over live Twilio calls
  to stretch credits (docs/02 "skip live Twilio early").

## Verification
- Unit: a `TranscriptionLogger` test + STT wiring test that a scripted media sequence yields a
  `TranscriptionFrame` (mock/stub the Sarvam service; keep the 106 green).
- Live/recorded: on an 8kHz call, interim frames stream while speaking, a final
  `TranscriptionFrame` lands after ~850ms silence; log finalize latency + transcript text.
- Record the median finalize latency + a rough WER read into docs/decisions (WER test).

## Landmines
- Test numbers only; placeholder consent still in force. Sarvam credits burn per call — favor
  recorded audio. Don't let Step 2 quietly pull in barge-in or LLM — those are later steps and
  the concurrency bugs live in the barge-in path (code-review gate, docs/08).
- RTT gate still owed (no production origin yet) — independent of Step 2.
