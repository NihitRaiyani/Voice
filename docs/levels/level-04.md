# Level 4 — Audio and local ASR

**Roadmap sections:** 14–18. **Status:** Sections 14–17 audio, deterministic endpointing, ASR adapters and persisted language routing are implemented with offline evidence. Native ASR inference/quality, section 18 noise benchmarks and the complete Level 4 gate remain pending.

## Entry gate and authorized scope

Reuse Level 1 provider seams, Level 0 audio concepts and existing Twilio/codec/VAD foundations. The authorized increments cover sections 14–17; section 18 and advanced adaptive buffering/PLC remain outside this increment. OpenAI remains the LLM; Hindi-base Hinglish output and the separate multilingual lab remain unchanged. No new ASR weight download, paid/provider inference, working-DB mutation or next-section implementation is needed. Existing bundled Silero weights may run locally. The owner permits an opt-in Hugging Face ASR lab endpoint to avoid local ASR weights; this does not change the self-hosted speech target or approve a paid deployment.

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

- [x] Sections 15–17 code: bounded endpointing, lazy/cache-only ASR adapters, explicit hosted lab seam and persisted five-state language router.
- [ ] Provision an approved ASR runtime or compatible hosted endpoint, execute real IndicConformer/Whisper inference, and complete section 18 approved multilingual/noisy/code-mix WER/CER, RTF, endpoint failure cases, hardware/license/access review and native review.
- [ ] Pass the complete Level 4 gate before beginning adaptive jitter or PLC.
- [ ] Advanced track: real UDP/RTP parsing and sequence rollover, bounded adaptive delay, selected PLC policy and measured VAD/ASR under realistic impairment/network emulation.

Do not turn a gap into unmarked silence or label the fixed-buffer exercise an advanced gate pass. SIP signalling does not imply UDP media in every deployment; the advanced experiment must identify its actual transport. Teacher demonstration and end-to-end voice approval remain separate.

See [audio contract](../05-endpointing-vad.md), [operations](../runbook.md#section-14-audio-lab-and-stream-metrics), [verification](../12-verification.md) and [decisions](../decisions.md).

Return to the [documentation index](../README.md).

## Sections 15–17 design and acceptance

Section 15 keeps Silero as the frame classifier. `SileroFrameProbability` uses the ONNX weights already bundled in installed Pipecat, lazily, on CPU. The model needs **512 samples / 1,024 PCM bytes / 32 ms** at 16 kHz. Section 14 transport frames are **20 ms**; `SpeechInputService` reframes them with bounded carry instead of passing the wrong shape to the model.

`EndpointDetector` owns decisions, independent of the model/framework. Defaults: speech probability **0.60**, lower continuation threshold **0.35**, onset **64 ms**, pre-roll **192 ms**, retained hangover **128 ms**, silence completion **512 ms**, maximum utterance **30,000 ms**. Durations round up to frames except the maximum, which rounds down to **29,984 ms**. Resumed speech before the silence limit resets the wait. Short confirmed acknowledgements have no extra minimum-length veto; flush/gap also preserves a pending high-probability frame. Thresholds are development defaults, not a native-speaker quality or latency approval.

Endpoint waits use received sample duration. Network stalls cannot manufacture silence. Gaps split the old segment, discard partial framing and reset recurrent model state. Missing duration is not inferred from a variable-sized Twilio message. Explicit end-of-file tail padding adds less than 32 ms of zeros; normal operation never silently fills missing packets. Reset discards old audio. Per-call state, hard bounds, overlap rejection and cancellation cleanup prevent cross-call/audio contamination. The lab service is sequential, with at most one second per input batch; it is not the future live inference queue/serving system.

Section 16 implements `IndicConformerSTT` and `WhisperSTT` behind `STTProvider`, plus `HuggingFaceSTT` for a separately configured **custom** ASR endpoint. All accept aligned mono 16 kHz PCM16 and return normalized text. NFC, decimal-digit and whitespace normalization preserve English course terms. IndicConformer follows its documented tensor/language/CTC-or-RNNT API; faster-whisper consumes its lazy segment iterator inside the worker, with transcription rather than translation and internal VAD disabled.

The 600M Indic model supports Gujarati/Hindi but **does not list English**. Explicit English therefore requires the Whisper comparison adapter or the retained cloud baseline; it is never silently forced into Hindi. No automatic quality fallback/model switch is authorized. The classifier/router cannot recover words that an ASR decoder lost.

Local model loading is cache-only, lazy and single-worker per adapter. Indic additionally requires reviewed custom-code opt-in, an immutable model revision and Hub offline mode set **before Python starts**. Optional ML runtimes/weights are not added to the default installation. Timeout/cancellation ends the await but cannot kill native inference: admission stays occupied until that worker exits, preventing overlapping access to the same model. Errors expose fixed operator messages without tokens, URLs, paths or caller text.

### Hosted ASR decision

The owner prefers avoiding local ASR weights/RAM. A Hugging Face token authenticates access; it does not create a running model or guarantee serverless availability. Public Hub metadata on **2026-10-18** returned an empty `inferenceProviderMapping` for the [selected model](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual). Therefore no public endpoint, free service, zero remote RAM or successful transcription is claimed.

The opt-in adapter requires an explicit HTTPS `HF_ASR_ENDPOINT_URL`, `HF_TOKEN` and compatible custom handler. Its wire format is JSON `inputs` containing base64 WAV, `language` and `decoder`; response is bounded JSON `text`/`language`. Generic hosted ASR endpoints are not assumed compatible. It rejects redirects/compressed responses, bounds requests/responses, sanitizes failures and applies a total timeout. No endpoint was deployed or called. Hosting consumes remote memory and may incur charges; local ASR model weights stay absent. [Hugging Face custom handlers](https://huggingface.co/docs/inference-endpoints/guides/custom_handler) explain the deployment boundary. The template and exact contract are in [the endpoint guide](../../benchmarks/asr/hf_endpoint/README.md).

### Persisted language contract

Section 17 adds `Language` states **GUJARATI, HINDI, ENGLISH, GUJARATI_ENGLISH, HINDI_ENGLISH**. Unicode-script ratios, bounded Latin-transliteration vocabulary, English tokens and recent classifications supply deterministic evidence. Ambiguous/one-word evidence abstains; the first informative turn initializes the route, while changing an established route requires two matching informative observations. Only the last five labels are saved, never transcript text. Course terms remain unchanged. This is a documented heuristic, not a trained language detector or an accuracy claim.

`advance_turn()` records language once for each accepted final caller turn, shared by text/live controllers. Rejected input does not update it. `CallState.to_dict/from_dict` persists the bounded router object in existing Redis/SQL JSON checkpoints; legacy/malformed router data safely defaults. No migration or new database table is needed. Caller language metadata/hints do **not** switch production speech away from Hindi-base Hinglish. The speech-input service returns final text to the existing controller; it does not mutate slots or bypass safety.

### Verification boundary

`make speech-lab` exercises synthetic short acknowledgement, resumed speech before the silence limit, sustained language switching and checkpoint round-trip. It uses scripted probabilities/transcripts and never reads `.env`, calls an ASR endpoint or downloads weights. `scripts/speech_lab.py --provider ... --wav ...` is a separate opt-in diagnostic with actual bundled Silero; hosted inference additionally needs `--allow-network`. Real transcript printing requires `--show-transcripts`.

Offline tests cover threshold boundaries, trailing speech, short acknowledgements, flush/reset/discontinuity/hard bounds, actual bundled Silero silence/reset, lazy/cache-only SDK mapping, normalization, language restrictions, endpoint response/error/privacy limits, worker timeout/cancellation, 20→32 ms reframing, accepted-controller/checkpoint integration and language smoothing/restore. They prove mechanics; they do not establish ASR WER/CER, native conversational quality, real-time factor or end-to-end voice latency. The production 8 kHz Sarvam/Pipecat route remains the tested baseline until later measured integration. OpenAI remains the main LLM.

Integrated verification on **2026-10-18**, using the locked development environment and offline/mock provider paths:

| Check | Result |
|---|---|
| Full offline pytest | **2,057 passed**, 23 existing dependency deprecation warnings, 78.44 seconds |
| `make test-ci` | **56 provider/config + 485 bounded tests passed** |
| `make lint type` | Ruff clean; mypy **52 source files** clean |
| Documentation links | **65** non-archive Markdown files, **347** local links/anchors, zero issues |
| Pre-commit | Ruff, mypy and mock provider contract hooks passed |
| Speech exercise | Four synthetic completed segments, pause retained, smoothed switch and checkpoint restore passed |
| Silero | Existing bundled ONNX silence/reset smoke passed; no new model download |
| Selected Word roadmap | SHA-256 unchanged (`89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`) |

The hosted CI workflow includes the same bounded speech/handler tests and runs on push. No HF/OpenAI inference, carrier call, new model/runtime installation, working-DB mutation, migration, real `.env` edit or fixed speech-asset edit occurred. Test execution time is not ASR/voice latency. Section 18 and native/complete voice approval remain pending.

A saved English route with an Indic adapter returns `stt-unsupported-or-invalid-request`; it never relabels English as Hindi or silently switches models. Select the explicit Whisper comparison for that lab. The real-WAV CLI exits nonzero on segment errors and sanitizes malformed/truncated WAV failures.
