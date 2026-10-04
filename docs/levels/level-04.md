# Level 4 — Audio and local ASR

**v4 sections:** 14–18. **Status:** Cloud audio baseline present; local ASR lab planned.

## Entry gate

Level 1 provider boundary; Level 0 audio prerequisites. May be developed as an isolated lab alongside booking after prerequisites pass.

## Scope when implementation is requested

Build offline 20 ms 8 kHz μ-law ingestion, PCM16 conversion, 16 kHz resampling and sample/byte/sequence validation. Add bounded jitter handling and VAD/endpointing; measure delay rather than blindly copying timing examples.

Benchmark candidate IndicConformer against a Whisper baseline using approved multilingual/noisy/code-mix audio. Track language with confidence/smoothing. Record normalized WER/CER, real-time factor, endpoint/provider final delay and failure examples.

## Existing reuse in Voice_Agent

Twilio native codec/media serializer, Silero/contextual endpointing, shared Indic normalization and audio tests.

## Acceptance gate

- [ ] Codec/resampling/sample-alignment and sequence tests pass on deterministic fixtures.
- [ ] Per-language/noise/code-mix WER/CER and RTF report includes native review and failure cases.
- [ ] Language smoothing and endpointing work on representative audio with bounded buffering.
- [ ] Candidate model/license/hardware choice is explained from evidence.

## Boundaries and advanced work

Advanced UDP impairment, adaptive jitter and packet-loss concealment follow the core gate. WebSocket support does not establish SIP/RTP readiness.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
