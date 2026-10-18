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

`roma/domain/audio` owns raw mono little-endian PCM16, the preserved G.711 codec, strict streaming encoding, a directly implemented 63-tap filtered 8↔16 kHz converter, bounded 20 ms framing and fixed jitter playout. A 20 ms unit is 160 mu-law bytes, 320 PCM16 bytes at 8 kHz or 640 at 16 kHz. Half-samples fail validation; final tails require an explicit policy. Upsampling does not recover telephone bandwidth.

Reorder complete packets before decoding; duplicates and already-played packets are discarded. Missing runs are counted in bounded work, marked discontinuous and reset filter history; no PLC is synthesized. A whole mu-law byte always decodes to two PCM bytes, so temporal disorder and byte misalignment are separate faults. Native TTS odd-byte carry/header sanitation remains in `roma/realtime/tts.py`.

The live `sequence_gaps` metric counts missing inbound **media messages** from `media.chunk`. Twilio's global `sequenceNumber` also includes controls; neither counter measures missing PCM samples or lost TCP segments. Bounds: 8,000 decoded media bytes and 16,384 envelope characters; counters contain no payload. See [Twilio's protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages).

TCP retransmission/order presents delay or backpressure; UDP-style reordering/loss needs its own explicit experiment ([TCP](https://www.rfc-editor.org/info/rfc9293/), [RTP](https://www.rfc-editor.org/info/rfc3550/)). The lab injects loss/reorder/duplicates/burst delay/variable jitter using virtual time. Its clean energy-based VAD check is not native ASR/VAD quality under loss. Adaptive jitter, PLC and the advanced gate wait for the complete Level 4 gate. [Level 4](levels/level-04.md) owns settings, evidence, limitations and the reference table.
