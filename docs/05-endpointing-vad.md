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

`roma/domain/conversation/language.py` tracks five explicit caller routes from scripts, Latin vocabulary and recent evidence. `advance_turn` observes accepted final turns once; existing JSON checkpoints save only a bounded label history. One English word cannot switch the route. This metadata does not change Hindi-base Hinglish production output. The three-public-clip hosted smoke proves inference access; representative ASR/noise/language/endpoint quality and future live serving remain gated. See [operations](runbook.md#sections-1517-speech-input-lab).

ASR maximum-size chunks are aggregated inside `SpeechInputService` until true end-of-turn (silence/flush/discontinuity). Exact-cap silence is a control boundary, never an empty inference request or a second caller turn. Pending text is bounded by the existing caller limit; errors/overflow fail the whole turn safely.

## Section 18 measurement contract

`roma/eval/asr_benchmark.py` owns edit alignment/counts, deterministic public-corpus noise and telephone variants, source hashes, weighted summaries and saved failures. `scripts/asr_benchmark.py` makes network use explicit and can resume or replay failed/high-WER cases. Direct Indic and Whisper adapters are compared without changing the production router. Stitched public language pairs are marked synthetic; they do not establish native code-mix quality. Hosted request RTF includes network/queue overhead; inference RTF remains null without compute timing. The [Level 4 brief](levels/level-04.md#section-18-asr-benchmark) owns actual results and the chosen development configuration.
