# Audio, VAD, endpointing and language

Twilio bidirectional media uses 8 kHz μ-law; the native Pipecat serializer owns transport messages. The offline L4 lab explicitly verifies 20 ms frames, PCM16 decoding, 16 kHz ASR resampling, sample/byte alignment and sequence handling. Do not infer SIP/RTP support from WebSocket media.

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

Keep the configured barge-in behavior and Twilio `clear` ordering. Test backchannels, partial words, long silence, noisy tails, double interruption, overtalk and abrupt disconnect. Bounded jitter (40–60 ms is an example), overflow and resampling policies require measured evidence.

Core L7 cancellation/pressure precedes advanced turn epochs/stale-audio rejection. UDP impairment, adaptive jitter and packet-loss concealment are optional advanced work when transport requirements justify them. See [pipeline](02-pipeline.md), [L4](levels/level-04.md) and [L7](levels/level-07.md).
