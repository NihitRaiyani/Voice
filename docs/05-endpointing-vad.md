# Audio, VAD, endpointing and language

Twilio bidirectional media uses 8 kHz mono μ-law/base64 with variable-size messages. `ValidatedTwilioSerializer` checks identity/track/sequence/timestamp/payload before native Pipecat decoding and checks PCM alignment afterward. Native output/clear/hang-up and existing cloud sample rates stay intact. Section 14 adds a separate pure-Python audio boundary/lab; it prepares 16 kHz PCM for later local ASR without claiming SIP/RTP support.

## Source baseline

| Setting | Source default |
|---|---|
| Silero confidence / minimum volume | 0.8 / 0.7 |
| VAD stop | 0.45 seconds |
| Terminal / default / continuation endpoint | 0.50 / 0.85 / 1.30 seconds |
| Backchannel maximum | 0.60 seconds |

Effective environment values may differ; record resolved non-secret configuration with benchmarks. VAD end, endpoint delay and ASR final delivery are separate events. Do not replace contextual endpointing with an old global 850 ms rule. Verify existing carrier noise/echo behavior before adding processing.

## Local ASR and language

At L4 benchmark candidate IndicConformer against Whisper on approved Gujarati/Hindi/English, code-mix and noisy phone audio. Report normalized WER/CER, RTF, finalization latency and failure cases. Language confidence/smoothing prevents switching on each fragment. Average accuracy cannot hide weak language/noise performance.

Live Roma still replies Hindi-base Hinglish. Multilingual speech is a separate lab with native review. Use headphones for the L6 microphone experiment; do not assume separate transport legs eliminate all acoustic echo.

## Interruption and buffering

Keep the configured barge-in behavior and Twilio `clear` ordering. Test backchannels, partial words, long silence, noisy tails, double interruption, overtalk and abrupt disconnect. The section 14 lab uses a fixed 60 ms target (40–60 allowed), eight-packet default bound and explicit gap/discontinuity/overflow rules. Release is timer-driven, not dependent on another arrival. Causal FIR history and decimation phase persist across chunks; interruption/reset discards old synthesis state. The live TCP path does not acquire a new jitter delay from this lab.

Core L7 cancellation/pressure precedes advanced turn epochs/stale-audio rejection. UDP impairment, adaptive jitter and packet-loss concealment are optional advanced work when transport requirements justify them. See [pipeline](02-pipeline.md), [L4](levels/level-04.md) and [L7](levels/level-07.md).

## Section 14 reusable audio contract

`roma/domain/audio` owns raw mono little-endian PCM16, the preserved G.711 codec, strict streaming encoding, a directly implemented 63-tap filtered 8↔16 kHz converter, bounded 20 ms framing and fixed jitter playout. A 20 ms unit is 160 mu-law bytes, 320 PCM16 bytes at 8 kHz or 640 at 16 kHz. PCM payloads must be immutable bytes; sample-rate/channel/counter metadata uses strict integer validation rather than accepting booleans or numerically equal floats. Half-samples fail validation; final tails require an explicit policy. Egress validates payload/format/alignment before mutating FIR/framing state, so a rejected input cannot poison the next valid chunk. Upsampling does not recover telephone bandwidth.

Reorder complete packets before decoding; duplicates and already-played packets are discarded. Missing runs are counted in bounded work, marked discontinuous and reset filter history; no PLC is synthesized. A whole mu-law byte always decodes to two PCM bytes, so temporal disorder and byte misalignment are separate faults. Native TTS odd-byte carry/header sanitation remains in `roma/realtime/tts.py`.

The live `sequence_gaps` metric counts missing inbound **media messages** from `media.chunk`. Twilio's global `sequenceNumber` also includes controls; neither counter measures missing PCM samples or lost TCP segments. Bounds: 8,000 decoded media bytes and 16,384 envelope characters; counters contain no payload. See [Twilio's protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages).

TCP retransmission/order presents delay or backpressure; UDP-style reordering/loss needs its own explicit experiment ([TCP](https://www.rfc-editor.org/info/rfc9293/), [RTP](https://www.rfc-editor.org/info/rfc3550/)). The lab injects loss/reorder/duplicates/burst delay/variable jitter using virtual time. Its clean energy-based VAD check is not native ASR/VAD quality under loss. Adaptive jitter, PLC and the advanced gate wait for the complete Level 4 gate. [Level 4](levels/level-04.md) owns settings, evidence, limitations and the reference table.

## Sections 15–17 speech-input contract

`roma/domain/audio/endpointing.py` owns the finite endpoint decisions; `roma/providers/ai/silero.py` supplies probabilities from installed bundled ONNX weights. `roma/services/speech_input.py` reframes bounded 20 ms PCM blocks to 512-sample/32 ms model frames, then submits only completed segments to `STTProvider`. A hard maximum segment split preserves lower-threshold speech continuation for a quiet ongoing utterance; it does not restart onset detection mid-speech. Gaps reset recurrent/framing history; wall-clock/network delay is not fabricated silence. Defaults, failure policy and measured evidence belong to [Level 4](levels/level-04.md#sections-1517-design-and-acceptance).

`roma/providers/ai/stt.py` supplies cache-only IndicConformer, faster-whisper comparison and an opt-in compatible HF custom endpoint adapter; `hf_asr.py` adds explicit public-benchmark Spaces and the separate authenticated HF Whisper router. None downloads weights automatically. Public Spaces accept only declared public benchmark audio and never receive HF tokens. Indic supports Gujarati/Hindi; explicit English uses a selected Whisper route or the retained baseline. Strict audio/format/language preflight precedes model loading. `normalize_transcript` preserves course terms while normalizing NFC/digits/spacing and rechecks the output bound after normalization. ASR acoustic features and CTC/RNNT decoding stay inside the model adapter; business stages remain in code.

Current local development ASR is Whisper Turbo 4-bit MLX with the existing bounded single-model mixed-turn batch. IndicConformer ASR weights were removed. See [current Level 4 readiness](levels/level-04.md#current-readiness); the historical route and comparison below are preserved as evidence, not current configuration.

**Historical pre-Turbo routing:** The cache-only local composition performs exactly one decode per bounded segment: explicit English uses Whisper base; Gujarati/Hindi and saved mixed states use IndicConformer with their base-language hint. The complete waveform is preserved; no phrase splitting, secondary decode or provider-error fallback occurs. `LocalLanguageSTT` normalizes exact emitted phonetic booking words before `SpeechInputService` hands the transcript to the controller. Unicode-aware token matching preserves compounds, names, existing Latin terms, digits and native dates such as `કાલે`/`कल`. It cannot recover speech omitted by ASR. [Historical Level 4 evidence](levels/level-04.md#single-pass-local-routing--2026-10-21) supersedes the historical phrase-span experiment.

**Language-router contract and historical Indic comparison:** `roma/domain/conversation/language.py` tracks five explicit caller routes from scripts, Latin vocabulary and recent evidence. `advance_turn` observes accepted final turns once; existing JSON checkpoints save only a bounded label history. One English word cannot switch the route. This metadata does not change Hindi-base Hinglish production output. The [local 15-case comparison](levels/level-04.md#local-indicconformer-versus-whisper-base--2026-10-21) improves Gujarati/Hindi recognition over base but still loses stitched English spans. The selected Indic decoder's Gujarati/Hindi vocabularies contain no English alphabet, so routing/normalization alone cannot establish English word preservation. Representative ASR/noise/language/endpoint quality and future live serving remain gated. See [operations](runbook.md#sections-1517-speech-input-lab).

ASR maximum-size chunks are aggregated inside `SpeechInputService` until true end-of-turn (silence/flush/discontinuity). Exact-cap silence is a control boundary, never an empty inference request or a second caller turn. Pending text is bounded by the existing caller limit; errors/overflow fail the whole turn safely.

## Section 18 measurement contract

`roma/eval/asr_benchmark.py` owns edit alignment/counts, deterministic public-corpus noise and telephone variants, source hashes, weighted summaries and saved failures. `scripts/asr_benchmark.py` makes network use explicit and can resume or replay failed/high-WER cases. Direct Indic and Whisper adapters are compared without changing the production router. Stitched public language pairs are marked synthetic; they do not establish native code-mix quality. Hosted request RTF includes network/queue overhead; inference RTF remains null without compute timing. The [Level 4 brief](levels/level-04.md#section-18-asr-benchmark) owns actual results and the chosen development configuration.


The local microphone prototype uses a shared [TurnPolicy](../roma/domain/audio/turn_policy.py), including a provisional 300 ms continuation grace after the base 512 ms endpoint. Legacy standalone lab settings remain explicit. Capture deadlines flush source audio rather than silently resetting it; [Level 6](levels/level-06.md#offline-turn-boundary-repair--phase-2) owns the pause matrix and quantized latency. No live carrier or gate change is implied.


## Persistent capture and advanced audio lab

The Phase 5A half-duplex path, retained as the explicit `--half-duplex` diagnostic after Phase 5C, uses one callback input stream per local call lifecycle. `start()` opens muted; `listen()` admits a fresh bounded capture generation/turn epoch, and final ASR processing mutes input in software. Playback never restarts the input driver. Cleanup closes input once. The non-blocking bounded ring copies immutable PCM; active overflow or lock contention fails capture explicitly rather than joining incomplete audio. Muted audio is discarded, never retained. Stalled callbacks raise `DEVICE-CAPTURE-STALLED`. At the capture deadline, already-received bounded frames are drained; post-deadline callbacks are excluded, preserving the exact input cap and partial tail. This removes per-turn driver reopen races without proving the earlier intermittent failure had that cause.

Monotonic epochs follow captured PCM, ASR requests/results, LLM requests/chunks and TTS requests/results. New captures, interruptions, timeouts and disconnects invalidate old work. Await boundaries recheck ownership before controller changes, booking admission, checkpoints and playback. Output clear starts before waiting on cancellation-resistant providers. Fake-stream tests establish software ordering; audible sub-60 ms cancellation requires physical measurement. An SQL commit already underway remains authoritative and must be reconciled.

`AdaptiveJitterBuffer` is a separate PCM16/16 kHz/20 ms lab. It validates sequence/timestamp/epoch/hash envelopes and adapts a 40–120 ms media-schedule offset using the RFC 3550 estimator `J += (abs(D) - J) / 16`, an EWMA rather than statistical variance. Timer drain and final flush handle absent arrivals. Up to three missing frames use labelled attenuated/interpolated concealment, followed by an explicit discontinuity. Concealed PCM is excluded from observed speech in replay endpointing. Fixed production jitter and Twilio remain unchanged.

Strict bounded journals contain packet hashes and epoch/drain/flush controls. Hashes cannot reconstruct audio: waveform replay requires an explicitly authorized, hash-checked payload resolver. Optional lab Redis uses HMAC-derived isolated keys, indexed idempotent appends, bounded capacity and a non-renewing ten-minute TTL; no PCM, transcripts or business effects are stored. This is not a durable business event log or recovery-point guarantee.

The [synthetic evidence](../benchmarks/voice/phase5a-adaptive-replay.json) uses seed 3550, 500 frames, ±50 ms arrival jitter and exactly 25 drops. Both adaptive and fixed-60 ms buffers admit 475 received frames without late/overflow loss; adaptive adds 25 labelled concealment frames. Maximum receive-to-playout wait is 185 ms adaptive versus 125 ms fixed, with 5 ms maximum timer lateness. The 120 ms schedule offset is not a per-packet wait ceiling. A four-loss burst yields three concealed frames and one discontinuity. Repeated waveform/endpoint/scripted-controller decisions match; scripted stop prevents further turns and booking effects. This proves synthetic determinism, not native quality or latency superiority.

## Local full-duplex capture and echo reference

Phase 5C makes full duplex the local CLI default. Capture remains active through playback and cancellation: mono PCM16, 16 kHz, 320 samples/20 ms per callback. One delivered-sample clock spans valid and missing ADC timestamps. ADC metadata establishes alignment and bounded drift calibration; separately counted clock-domain rebases require corroborating hardware/monotonic clock evidence. Repeated subframe phase offsets are reported as uncertainty without resetting that offset or discarding PCM. An uncompensated cumulative phase disagreement of at least 20 ms, native overflow, invalid blocks, callback contention or bounded-ring overflow still fails capture. Timestamp metadata cannot exclude an unflagged hardware loss. The callback copies frames only; the serial consumer runs pinned optional WebRTC AEC3 before downstream Silero/VAD. Full duplex fails closed if `pywebrtc-audio==0.2.0` is unavailable or DSP fails. No raw-capture fallback is permitted.

A persistent 16 kHz/20 ms output callback consumes at most 80 ms of queued PCM; 8→16 kHz resampling stays outside the callback. Actual handoff receipts feed the bounded playback-reference ring. Its first admitted DAC estimate anchors one monotonic reference clock; every subsequent native callback advances that clock, including idle/silent callbacks, so real silence gaps remain visible. Raw wall-clock estimate differences are uncertainty metadata rather than instructions to retime PCM. Native AEC processes each 20 ms capture as two 10 ms blocks. Default noise suppression and automatic gain control are off. Output clear removes unplayed queued PCM; already-handed DAC-tail reference and adaptation remain available. These software timestamps require physical calibration; the FIR delay, device-clock alignment and acoustic path remain measurement limits.

`DuplexVoiceSession` separates continuous endpointing from bounded ASR/final queues and response work. Near-end VAD can claim a new epoch while the previous reply is active; clear begins without waiting for old provider cleanup. Existing controller/safety/SQL checks remain at their ownership boundaries. Eligible captured onset frames are preserved; stale or discontinuous active speech and queue pressure fail rather than silently lose PCM. `--half-duplex` selects software muting for diagnosis; `--buffered-speech` changes reply aggregation independently. [Level 7](levels/level-07.md#phase-5c-local-full-duplex-and-echo-cancellation) owns scope and acceptance limits.
