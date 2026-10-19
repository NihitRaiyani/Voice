# Level 4 — Audio and local ASR

**Roadmap sections:** 14–18. **Status:** Sections 14–17 audio, deterministic endpointing, ASR adapters and persisted language routing are implemented and hardened. Real hosted IndicConformer Gujarati/Hindi and Whisper English inference succeeded on three public benchmark clips. Section 18 now adds a reproducible public-corpus benchmark, failure export/replay and PostgreSQL evidence storage. Its small-corpus hosted results do not pass representative/native quality or the complete Level 4 gate.

## Entry gate and authorized scope

Reuse Level 1 provider seams, Level 0 audio concepts and existing Twilio/codec/VAD foundations. The authorized increments now cover sections 14–18 and the separate section 19 TTS lab. Section 18 measures existing hosted adapters without local ASR downloads; adaptive buffering/PLC remain outside this increment. OpenAI remains the LLM; Hindi-base Hinglish output and the separate multilingual lab remain unchanged. No new ASR weight download is needed. Completed benchmark evidence is explicitly written to existing PostgreSQL tables after inference; no schema changes are needed. Existing bundled Silero weights may run locally. The owner permits opt-in Hugging Face ASR lab calls to avoid local ASR weights; this does not change the self-hosted speech target or approve purchasing credits or a paid deployment.

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
- [x] Execute real hosted IndicConformer Gujarati/Hindi and Whisper English inference on explicitly public benchmark audio; see the hosted smoke below.
- [ ] Complete section 18 representative multilingual/noisy/code-mix WER/CER, RTF, endpoint failure cases, hardware/license/access review and native review. A three-clip hosted smoke does not pass this gate.
- [ ] Pass the complete Level 4 gate before beginning adaptive jitter or PLC.
- [ ] Advanced track: real UDP/RTP parsing and sequence rollover, bounded adaptive delay, selected PLC policy and measured VAD/ASR under realistic impairment/network emulation.

Do not turn a gap into unmarked silence or label the fixed-buffer exercise an advanced gate pass. SIP signalling does not imply UDP media in every deployment; the advanced experiment must identify its actual transport. Teacher demonstration and end-to-end voice approval remain separate.

See [audio contract](../05-endpointing-vad.md), [operations](../runbook.md#section-14-audio-lab-and-stream-metrics), [verification](../12-verification.md) and [decisions](../decisions.md).

Return to the [documentation index](../README.md).

## Sections 15–17 design and acceptance

Section 15 keeps Silero as the frame classifier. `SileroFrameProbability` uses the ONNX weights already bundled in installed Pipecat, lazily, on CPU. The model needs **512 samples / 1,024 PCM bytes / 32 ms** at 16 kHz. Section 14 transport frames are **20 ms**; `SpeechInputService` reframes them with bounded carry instead of passing the wrong shape to the model.

`EndpointDetector` owns decisions, independent of the model/framework. Defaults: speech probability **0.60**, lower continuation threshold **0.35**, onset **64 ms**, pre-roll **192 ms**, retained hangover **128 ms**, silence completion **512 ms**, maximum utterance **30,000 ms**. Durations round up to frames except the maximum, which rounds down to **29,984 ms**. Resumed speech before the silence limit resets the wait. Lower-threshold continuation remains active across a hard ASR maximum split, so quiet continuing speech is not reclassified as a new onset solely because of that split. Short confirmed acknowledgements have no extra minimum-length veto; flush/gap also preserves a pending high-probability frame. Thresholds are development defaults, not a native-speaker quality or latency approval.

Endpoint waits use received sample duration. Network stalls cannot manufacture silence. Gaps split the old segment, discard partial framing and reset recurrent model state. Missing duration is not inferred from a variable-sized Twilio message. Explicit end-of-file tail padding adds less than 32 ms of zeros; normal operation never silently fills missing packets. Reset discards old audio. Per-call state, hard bounds, overlap rejection and cancellation cleanup prevent cross-call/audio contamination. The lab service is sequential, with at most one second per input batch; it is not the future live inference queue/serving system.

Section 16 implements `IndicConformerSTT` and `WhisperSTT` behind `STTProvider`, `HuggingFaceSTT` for a separately configured **custom** ASR endpoint, and explicit public-Space/HF-router lab adapters in `roma/providers/ai/hf_asr.py`. All require nonempty immutable aligned mono 16 kHz PCM16 bytes and strict format/language metadata before loading a model or admitting inference. They return normalized text with bounds checked before and after NFC, decimal-digit and whitespace normalization, preserving English course terms. IndicConformer follows its documented tensor/language/CTC-or-RNNT API; faster-whisper consumes its lazy segment iterator inside the worker, with transcription rather than translation and internal VAD disabled.

The 600M Indic model supports Gujarati/Hindi but **does not list English**. Explicit English therefore requires the Whisper comparison adapter or the retained cloud baseline; it is never silently forced into Hindi. No automatic quality fallback/model switch is authorized. The classifier/router cannot recover words that an ASR decoder lost.

Local model loading is cache-only, lazy and single-worker per adapter. Indic additionally requires reviewed custom-code opt-in, an immutable model revision and Hub offline mode set **before Python starts**. Optional ML runtimes/weights are not added to the default installation. Timeout/cancellation ends the await but cannot kill native inference: admission stays occupied until that worker exits, preventing overlapping access to the same model. Errors expose fixed operator messages without tokens, URLs, paths or caller text.

### Hosted ASR decision

The owner prefers avoiding local ASR weights/RAM. A Hugging Face token authenticates access; it does not create inference capacity. Public Hub metadata on **2026-10-18** returned an empty `inferenceProviderMapping` for the [selected model](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual). That absence is distinct from an independently running public Space.

`hf_public_lab` explicitly selects the discovered [community IndicConformer Space](https://1219naveen-indicconformer-asr.hf.space) for Gujarati/Hindi with the 600M CTC decoder and the [official public Whisper Space](https://hf-audio-whisper-large-v3.hf.space) for English with Whisper large-v3. The composition uses Gradio upload/call/SSE, sends no HF token to either public Space, and requires request metadata `data_classification=public_benchmark`. `scripts/speech_lab.py` additionally requires both `--allow-network` and `--public-benchmark`. Private caller audio is not permitted on this route. Public-service queue time and availability are external conditions; this is a lab selection, never an automatic production fallback.

`hf_whisper` is a separate explicit authenticated route to the official HF inference router. The existing `HUGGING_FACE_API_KEY` alias was valid for read access on **2026-10-18**, but inference returned **402, credits unavailable**. Do not retry a credit failure, switch routes automatically, purchase credits or deploy an endpoint as a fallback. No token value is recorded in evidence.

`hf_indic_conformer` retains the optional private custom-handler contract: explicit HTTPS `HF_ASR_ENDPOINT_URL`, token, JSON `inputs` containing base64 WAV/`language`/`decoder`, and bounded JSON `text`/`language`. A Hub page or generic ASR handler is not compatible by implication. Hosted clients reject redirects/compressed responses, bound responses, sanitize failures and apply a total timeout. The custom template was not deployed. Remote hosting still requires model RAM/compute and may incur charges. See [the custom endpoint guide](../../benchmarks/asr/hf_endpoint/README.md) and [runbook provider commands](../runbook.md#sections-1517-speech-input-lab).

### Persisted language contract

Section 17 adds `Language` states **GUJARATI, HINDI, ENGLISH, GUJARATI_ENGLISH, HINDI_ENGLISH**. Unicode-script ratios, bounded Latin-transliteration vocabulary, English tokens and recent classifications supply deterministic evidence. Ambiguous/one-word evidence abstains; the first informative turn initializes the route, while changing an established route requires two matching informative observations. Only the last five labels are saved, never transcript text. Course terms remain unchanged. This is a documented heuristic, not a trained language detector or an accuracy claim.

`advance_turn()` records language once for each accepted final caller turn, shared by text/live controllers. Rejected input does not update it. `CallState.to_dict/from_dict` persists the bounded router object in existing Redis/SQL JSON checkpoints; legacy/malformed router data safely defaults. No migration or new database table is needed. Caller language metadata/hints do **not** switch production speech away from Hindi-base Hinglish. The speech-input service returns final text to the existing controller; it does not mutate slots or bypass safety.

### Verification boundary

`make speech-lab` exercises synthetic short acknowledgement, resumed speech before the silence limit, sustained language switching and checkpoint round-trip. It uses scripted probabilities/transcripts and never reads `.env`, calls an ASR endpoint or downloads weights. `scripts/speech_lab.py --provider ... --wav ...` is a separate opt-in diagnostic with actual bundled Silero; hosted inference additionally needs `--allow-network`, and public Spaces also need `--public-benchmark`. Real transcript printing requires `--show-transcripts`.

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

The hosted CI workflow includes the same bounded speech/handler tests and runs on push. That original offline implementation verification made no HF/OpenAI inference or carrier call and made no new model/runtime installation, working-DB mutation, migration, real `.env` edit or fixed speech-asset edit. Test execution time is not ASR/voice latency. Section 18 and native/complete voice approval remain pending.

A saved English route with an Indic adapter returns `stt-unsupported-or-invalid-request`; it never relabels English as Hindi or silently switches models. Select the explicit Whisper comparison for that lab. The real-WAV CLI exits nonzero on segment errors and sanitizes malformed/truncated WAV failures.


## Implemented-boundary audit and hosted smoke

The audit on **2026-10-19** through section 17 hardened fee/salary punctuation and compact amount-label anchors (`fees25000`, `30000salary`, and Indic equivalents); caller/slot bounds after NFC normalization; safe rejection of non-string text; finite safety-audit deadlines; malformed/non-ASCII bearer rejection as 401; consistent 503 handling of SQL/native connection-capacity outages; immutable PCM/strict metadata preflight; egress rejection before FIR state mutation; quiet-speech continuation across hard ASR splits; and STT metadata validation before model loading with post-NFC output bounds. These are specific reviewed failure cases, not proof that every possible bug is absent. Reusable policy belongs to the safety, API, audio and provider topic contracts.

The initial hosted smoke on **2026-10-18** used public FLEURS validation row 0 for each language, clean read speech and one request per language. Local ASR weights were not downloaded. The reusable adapter-run evidence artifact is [hosted_smoke.json](../../benchmarks/asr/hosted_smoke.json); the initial prototype timings below remain historical. [The runbook](../runbook.md#sections-1517-speech-input-lab) owns the reproduction command.

| Language / remote model | Audio duration | Total observed request time | Space-reported compute time |
|---|---:|---:|---:|
| Gujarati / IndicConformer 600M CTC | 11.88 s | 6.232 s | 3.29 s |
| Hindi / IndicConformer 600M CTC | 12.24 s | 6.461 s | 2.65 s |
| English / Whisper large-v3 | 6.54 s | 61.589 s | Not separately reported |

RTF is **compute seconds / audio seconds**; the Indic compute values above correspond to approximately 0.277 and 0.217. A Space's inverse audio/compute display is throughput, not this RTF definition. Total observed time includes transfer/queue/service overhead and must remain separate from reported compute. English total/audio is approximately 9.42, not an isolated compute RTF.

These three successful transcriptions demonstrate accessible hosted inference and language selection. They do not establish statistical WER/CER, noisy telephone/code-mix quality, native acceptance, sustained capacity or production latency/SLOs. The production 8 kHz Sarvam/Pipecat path, OpenAI LLM and Hindi-base Hinglish replies remain unchanged. The preceding offline tables retain their historical dates and counts; current integrated audit verification is recorded separately.


### Actual configured-adapter smoke — 2026-10-19

The implementation's `hf_public_lab` provider successfully completed all three public samples again; this is a real adapter run, separate from the October 18 prototype. The [saved report](../../benchmarks/asr/hosted_smoke.json) records UTC timestamp, dataset/config/split/row, WAV identity hashes, public reference/hypothesis, scoring rules and endpoint identities. No caller/project asset, HF token or signed download URL is included.

| Language | Public audio | Total request time | Per-clip WER | Per-clip CER |
|---|---:|---:|---:|---:|
| Gujarati | 11.88 s | 6.583 s | 3/24 = 12.5% | 4/128 = 3.125% |
| Hindi | 12.24 s | 6.755 s | 6/30 = 20% | 6/115 = 5.217% |
| English | 6.54 s | 4.585 s | 0/15 = 0% | 0/64 = 0% |

These are one-clip scores, not language-wide estimates. Scoring applies NFC/casefold/decimal normalization, replaces punctuation/symbols with spaces, uses word edit distance for WER and Unicode-code-point edit distance without whitespace for CER. Reordering, noise, telephone impairment, code-mix, short acknowledgements and native conversational acceptance need the later representative evaluation. The earlier 61.589-second English request and this 4.585-second request illustrate public-service variability, not a p95 result. Remote model names come from inspected public Space source/configuration; runtime weights are not cryptographically attested.

The cloud clients bound upload/JSON/SSE bytes, enforce one request slot, close streams on cancellation and apply a total deadline through heartbeats/queueing. Adapter-owned safe errors preserve useful status codes; arbitrary transport exceptions are sanitized. Optional HTTP imports cannot break base/mock provider construction. Remote cancellation closes the client's stream but does not prove a queued remote model stopped; no automatic replay/fallback is made.

Hard ASR size splits are internal chunks, not new accepted caller turns. The speech-input service aggregates bounded transcripts until real silence, flush or discontinuity; an exact size boundary can finish via an empty control boundary without an empty ASR request. Retained text uses the existing 4,096-character caller limit; overflow/provider failures cannot accept an incomplete turn. Reset/cancellation discards pending text and audio.


### Integrated audit verification — 2026-10-19

| Check | Observed result |
|---|---|
| Full offline pytest | **2,412 passed**, 23 existing dependency warnings, 80.45 seconds |
| `make test-ci` | **56 provider/config + 840 bounded tests passed** |
| `make lint type` | Ruff clean; mypy **54 source files** clean |
| Markdown checks | **65** non-archive files; **355** local links/anchors; zero issues |
| Actual hosted adapters | Three public FLEURS samples transcribed successfully; per-clip scores in saved report |
| Real Silero + cloud + router | Public Gujarati WAV: two final speech segments, both route `GUJARATI`, zero ASR errors; transcript omitted from the diagnostic |
| Word roadmap identity | Existing SHA-256 unchanged (`89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`) |

Automated tests use mocks/disposable services; real public ASR checks are explicit separate runs. No working-DB mutation, migration, new model/runtime installation, private audio upload, token to a public Space, fixed audio edit, credit purchase or deployment occurred. Public speech samples are cached under ignored runtime storage; the tracked report contains public benchmark references/hypotheses only. Existing cloud speech, OpenAI and Hindi-base Hinglish production behavior remain. Native/representative section 18 evaluation and later complete voice approval are separate gates.

Pre-commit verification for this audit also passed: Ruff, mypy and mock provider contracts.

## Section 18 ASR benchmark

`roma/eval/asr_benchmark.py`, `scripts/asr_benchmark.py` and their tests implement deterministic S/D/I alignment, WER/CER, weighted corpus summaries, reproducible noise/telephone variants, network opt-in and atomic resumable reports. Scoring retains the smoke's NFC/casefold/digit/punctuation normalization; CER uses code points without whitespace. Equal-cost alignments choose a deterministic edit path. Scores may exceed one; empty-reference insertions have no defined rate.

The [manifest](../../benchmarks/asr/section18/manifest.json) identifies three CC-BY-4.0 public FLEURS validation row-0 recordings, source hashes, seed 18 and 15 dB synthetic uniform noise. Telephone simulation uses filtered 16→8 kHz, G.711 mu-law round trip and 8→16 kHz; duration is cropped to the original sample count. Each source is evaluated clean/noisy/telephone. Gujarati/Hindi followed by English with a 250 ms silent join form explicitly **stitched public code-mix** cases; these are not native code-switch conversations.

### Actual run — 2026-10-19

| Adapter / scope | Successful requests | WER observed (clean / noise / telephone) | CER observed (clean / noise / telephone) |
|---|---:|---|---|
| IndicConformer CTC / Gujarati | 3/3 | 12.5% / 12.5% / 12.5% | 3.125% / 3.125% / 3.125% |
| IndicConformer CTC / Hindi | 3/3 | 20% / 13.33% / 20% | 5.217% / 4.348% / 5.217% |
| IndicConformer CTC / stitched Gujarati–English | 3/3 | 46.15% / 46.15% / 46.15% | 35.42% / 35.42% / 35.42% |
| IndicConformer CTC / stitched Hindi–English | 3/3 | 46.67% / 42.22% / 42.22% | 39.11% / 38.55% / 38.55% |
| Whisper large-v3 / English | 3/3 | 0% / 0% / 0% | 0% / 0% / 0% |
| Whisper large-v3 / remaining same-corpus scopes | 0/12 | Unavailable: public service returned failed inference events | No invented scores |

The saved [Indic report](../../benchmarks/asr/section18/indic.json) has 12 successful cases; the [Whisper report](../../benchmarks/asr/section18/whisper.json) has three successful and 12 explicit error rows. A later diagnostic also received a Gradio `error` event with `null` detail; the cause is unreported and must not be guessed as quota or model accuracy. Successful hosted request RTF includes upload/queue/network/inference, not isolated compute. The service does not expose model timing here, so `inference_rtf` stays null. Remote source/config names are recorded, but deployed weights are not attested. No p95/capacity claim follows from these samples.

Failure export/read was exercised: six Indic high-WER cases and 12 Whisper service errors produce retained public WAV/metadata under ignored `var/roma/asr-benchmark/failures-*`. Reviewing the Indic hypotheses shows the appended English sentence missing; the three Gujarati variants each have 15 deletions. Failure WAVs are regenerated and hash-checked against the manifest; public references/hypotheses remain in the tracked reports. [Runbook](../runbook.md#section-18-asr-benchmark-and-failure-review) gives offline read/export and explicit network replay commands.

### Chosen development configuration and persistence

Retain **IndicConformer 600M CTC for Gujarati/Hindi and Whisper large-v3 for English as the development lab routing**, with strict 16 kHz mono PCM16, 30-second audio bound and 90-second hosted deadline. This preserves section 17's router and existing production speech. Indic English-span deletion makes an all-language Indic choice unjustified. The failed Whisper comparison prevents choosing it for Gujarati/Hindi from this evidence. Broader data, native code-mix and matched-model success are required before a production choice.

`PostgresBenchmarkStore` validates detached non-PII evidence before opening a session, then writes registry/run/results atomically. Identical immutable run keys return the existing ID; changed evidence or model provenance rolls back. The save CLI accepts completed reports only and rechecks aggregates. Store audio/ref/hypothesis in the public failure artifacts, not SQL; SQL holds finite aggregate metrics, manifest/config/environment and report location/hash.

Configured `roma` was read-only confirmed at `20261004_0003 (head)` before explicit benchmark inserts. Run `section18-indic-20261019-v1` persisted 12 samples / 72 metric rows; `section18-whisper-20261019-v1` persisted 15 attempted samples / 30 metric rows with failure status and only valid aggregates/error rates. A repeated Indic save returned the same run ID with `created:false`. No migration or pre-existing business row was modified.

### Section 18 gate position

- [x] Scoring, reproducible variants, saved/readable failures and immutable SQL evidence are implemented and exercised.
- [x] Existing out-of-order sequence tests remain part of offline CI; no sequence-check bypass is introduced.
- [ ] Both models successfully compared across the complete language/code-mix matrix: public Whisper inference failures prevent this.
- [ ] Representative native code-mix/noise corpus, native review and final model approval: three read recordings plus stitched variants are insufficient.
- [ ] Complete Level 4 and adaptive/PLC gates.

Final integrated checks for this increment: **2,574 offline tests**, **56 + 1,002** CI-selected tests, Ruff, **62-file mypy**, pre-commit and Markdown links/anchors pass. See [verification](../12-verification.md#sections-1819-verification--2026-10-19) for boundaries and remaining approval gates.


## Hosted Whisper failure diagnosis — 2026-10-19

The original report remains unchanged. A separate official [OpenAI Whisper Space](https://huggingface.co/spaces/openai/whisper) probe could transcribe Gujarati, but its [complete same-manifest attempt](../../benchmarks/asr/section18/whisper-alternate-report.json) succeeded only **2/15 cases**, with 13 explicit service errors. The endpoint uses API `predict`; the retained hf-audio endpoint uses `transcribe`. Switching endpoints alone therefore did not fix the comparison, and the existing default is preserved.

Detailed queue checks on **both** official Spaces subsequently reported **ZeroGPU runs limit exceeded**. Gradio's simplified `/call/{api}/{event}` stream forwards `output.data` and drops `output.error`, producing the earlier `error:null`. The corrected Whisper adapter submits one job with a unique session and reads `/gradio_api/queue/data` for that same session. It validates the completion event ID, preserves all byte/deadline/cancellation bounds, exposes only a fixed quota exception and records `asr-hosted-quota-exhausted` in new benchmark rows. Unknown provider text remains redacted; no retry or model/endpoint fallback occurs. [Sanitized check](../../benchmarks/asr/section18/whisper-quota-check.json) records real adapter verification without private audio or credentials.

Quota exhaustion is an observed current service prerequisite, not proof of the cause of every historical error or of model language quality. Complete matched-model comparison remains **blocked by hosted capacity**. Resume with the same public corpus after quota is available, or explicitly configure an approved compatible endpoint with its matching API name; a token does not provision compute. No HF token was sent to a public Space, no credits purchased, no endpoint deployed and no ASR weights downloaded. Existing Indic routing, production speech and historical SQL benchmark evidence remain unchanged.
