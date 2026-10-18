# Level 4 — Audio and local ASR

**Roadmap sections:** 14–18. **Status:** Section 14 digital-audio foundation and offline fixed-buffer lab implemented and verified offline. Sections 15–18 local ASR/language/noise benchmarks and the full Level 4 gate remain pending.

## Entry gate and authorized scope

Reuse Level 1 provider seams, Level 0 audio concepts and existing Twilio/codec/VAD foundations. Only section 14 is authorized here. OpenAI remains the LLM; Hindi-base Hinglish output and the separate multilingual lab remain unchanged. No model installation, provider call, working-DB mutation or next-section implementation is needed.

## Section 14 design and implementation

| Boundary | Implementation and contract |
|---|---|
| G.711 | `roma/domain/audio/g711.py` owns the existing pure-Python codec; `roma/realtime/ulaw.py` preserves old imports/asset conversion. New streaming `encode()` rejects partial PCM samples. |
| PCM/frame contracts | `pcm.py` validates signed little-endian mono PCM16 and frames bounded input into exact 20 ms blocks: 160 mu-law bytes at 8 kHz, 320 PCM bytes at 8 kHz, 640 PCM bytes at 16 kHz. Final tails require explicit padding/discard. |
| Resampling | `resampling.py` implements a causal 63-tap Hamming-windowed sinc filter at a 16 kHz working clock, cutoff 3.6 kHz. Interpolate with zero insertion/gain two; filter before decimation. History/phase survive arbitrary aligned chunks; explicit finish drains the filter tail once. Each conversion adds 1.9375 ms group delay. |
| Fixed buffer | `jitter.py` buffers normalized 20 ms packets before decode. Default target delay 60 ms; allowed 40–60 ms; default capacity 8 packets. An injected monotonic clock/timer drives release, including the final frame without a later arrival. Duplicates/late frames are discarded; output sequence is strictly increasing. Missing runs produce counted discontinuities in bounded work. |
| Model boundary | `session.py` decodes accepted playout, checks PCM alignment and returns 16 kHz blocks. A gap resets filter history and marks the next block discontinuous; it does **not** invent PLC/silence. Egress resamples 16→8 kHz, frames/encodes carrier bytes, and explicitly resets old synthesis state on interruption. |
| Live Twilio validation | `roma/providers/telephony/twilio/audio.py` validates identity/track/counters/timestamp/base64/size before native decode, rejects stale media and malformed or odd/channel-invalid PCM, and keeps per-stream counts. `pipeline.py` injects this subclass and logs `carrier_audio_summary`; `CallHandles.audio_metrics` exposes active-call counters. Native output/clear/hang-up and the current 8 kHz cloud/VAD/recording rates stay intact. |
| Reproducible exercise | `roma/eval/audio.py`, `scripts/audio_lab.py`, `make audio-lab`: deterministic synthetic tone/arrival fixtures, no credentials/services/downloads. Reports each case's frames, gaps, late/duplicate/reordered counts, queue depth and wait. |

The pure-Python resampler is an inspectable learning/reference path. It has no audio-library resampling dependency and is not enabled on production calls before later performance/provider gates. Native Pipecat remains the live codec/serializer baseline. The fixed buffer is applied in the explicit 16 kHz lab; a new 60 ms live WebSocket delay is not silently introduced. Future local ASR can consume these contracts after its rate/quality gate.

## Correct interpretation of the source

Twilio's [official protocol](https://www.twilio.com/docs/voice/media-streams/websocket-messages) specifies 8 kHz mono mu-law/base64 and allows variable-size payloads. Twenty milliseconds is the lab's explicit frame unit, not a guaranteed WebSocket message size. The live adapter accepts bounded variable-size messages (up to 8,000 audio bytes / 16,384 envelope characters).

Global `sequenceNumber` includes control messages; gaps in inbound `media.chunk` count missing media messages. Neither count establishes lost TCP segments or a number of missing samples when payload sizes vary. Duplicate/reordered media is rejected before decoding. Metadata/format faults have separate counters. Counters/logs contain no audio, transcripts or credentials.

Reordering complete mu-law packets changes chronology, but each byte still decodes to two PCM bytes. Odd PCM fragments/wrong formats cause byte-alignment faults; ordering and alignment are independently tested. Upsampling doubles the sample clock, not telephone bandwidth. G.711 is lossy, so sample-exact original PCM round trips are not claimed.

TCP provides ordered reliable delivery with retransmission; WebSocket transport delay/backpressure differs from UDP-style late/missing/reordered media ([TCP RFC 9293](https://www.rfc-editor.org/info/rfc9293/), [RTP RFC 3550](https://www.rfc-editor.org/info/rfc3550/)). The lab intentionally injects those application-level impairments; it is not a SIP/RTP implementation, packet-capture result or tc-netem experiment.

## Section 14 acceptance and reference evidence

- [x] Known codec vectors/independent reference, quantization round trip, strict PCM alignment and bounded 20 ms framing.
- [x] Stateful 8↔16 kHz conversion, odd sample-count chunk boundaries, finish/reset, anti-alias/image rejection and delay-adjusted round-trip checks.
- [x] Fixed 40/50/60 ms playout, timer-driven final release, queue bounds, initial/middle reorder, duplicates, long missing run, discontinuity and call isolation.
- [x] Clean 16 kHz blocks drive the real VAD state machine using a deterministic energy stand-in; native neural ASR/VAD quality under impairment is not claimed.
- [x] Live pre-decode sequence validation, per-call media-gap metric and native output/clear regressions.
- [x] Final full offline/CI/type/lint/link and commit-hook checks passed.

Default 60 ms synthetic-clock reference (50 generated frames, 1 second; no ASR or native speech):

| Fixture | Output frames | Gap count | Other observation | Maximum queue wait |
|---|---:|---:|---|---:|
| Clean | 50 | 0 | Timer drains final frame | 60 ms |
| Loss | 48 | 2 | Discontinuity, no fabricated audio | 60 ms |
| Reordering | 50 | 0 | One reordered arrival corrected | 80 ms |
| Duplication | 50 | 0 | One duplicate rejected | 60 ms |
| Burst delay | 46 | 4 | Four late arrivals rejected | 60 ms |
| Variable jitter | 50 | 0 | Ordered, aligned playout | 60 ms |

The 60 ms setting is a playout target relative to source timestamps. An early/reordered arrival can wait longer (80 ms above). These virtual-clock numbers are reproducible scheduling evidence, not measured carrier/voice latency. Anti-alias tests require at least 40 dB rejection of a 6 kHz downsampling alias and a 7 kHz interpolation image; a quantized 1 kHz round trip has a delay-adjusted RMS-error bound of 150 against roughly 7,071 RMS input. These synthetic bounds do not prove speech quality or capacity.

Focused tests live in `tests/domain/audio`, `tests/eval/test_audio_lab.py`, `tests/providers/telephony/twilio/test_audio_validation.py` and `tests/realtime/test_audio_vad_boundary.py`; retained codec/native serializer tests also run in CI. `make audio-lab` reproduces the report with no `.env` access or disk artifacts. Verification on **2026-10-18**, using the locked environment, synthetic fixed-clock fixtures and mocked paid providers:

| Check | Result |
|---|---|
| Full offline pytest | **1,859 passed**, 23 existing dependency deprecation warnings, 77.38 seconds |
| `make test-ci` | **56 provider/config + 287 bounded API/unit/audio/native-persistence tests passed** |
| `make lint type` | Ruff clean; mypy **44 source files** clean |
| Documentation links | 64 non-archive Markdown files checked, zero broken local links/anchors |
| Pre-commit hooks | Ruff, mypy and mock provider contracts passed |
| Selected Word roadmap | SHA-256 unchanged (`89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`) |

Use `make test` / `make test-ci` to reproduce after the runbook prerequisites. No paid calls, carrier calls, new speech-model downloads, working-DB changes, migrations, secret edits or fixed audio-asset edits were needed. Test duration is not a latency/capacity result. Hosted CI runs the same bounded selection on each push.

## Remaining Level 4 gate and advanced boundary

- [ ] Sections 15–18: evaluate IndicConformer/Whisper, approved multilingual/noisy/code-mix WER/CER, RTF and failure cases; language smoothing/endpointing, hardware/license choice and native review.
- [ ] Pass the complete Level 4 gate before beginning adaptive jitter or PLC.
- [ ] Advanced track: real UDP/RTP parsing and sequence rollover, bounded adaptive delay, selected PLC policy and measured VAD/ASR under realistic impairment/network emulation.

Do not turn a gap into unmarked silence or label the fixed-buffer exercise an advanced gate pass. SIP signalling does not imply UDP media in every deployment; the advanced experiment must identify its actual transport. Teacher demonstration and end-to-end voice approval remain separate.

See [audio contract](../05-endpointing-vad.md), [operations](../runbook.md#section-14-audio-lab-and-stream-metrics), [verification](../12-verification.md) and [decisions](../decisions.md).

Return to the [documentation index](../README.md).
