# Level 4 — Audio and local ASR

**Roadmap sections:** 14–18. **Status:** Audio/input/router and section 18 benchmark/failure/SQL tooling are implemented. The same 15-case local comparison is complete: IndicConformer transcribed 12 supported cases and explicitly rejected three English cases; Whisper base transcribed all 15. Indic improves Gujarati/Hindi WER/CER, but stitched English loss, representative/native quality and the full Level 4 gate remain pending.

Current LLM policy changed to Claude Haiku 5.5 on 2026-10-20. OpenAI names in the dated speech checks below identify the former baseline; official OpenAI Whisper Space names remain ASR endpoint identities. No OpenAI LLM calls/fallback are allowed. Historical reports and counts are unchanged.

## Entry gate and authorized scope

Reuse Level 1 provider seams, Level 0 audio concepts and existing Twilio/codec/VAD foundations. The authorized increments now cover sections 14–18 and the separate section 19 TTS lab. Section 18 measures hosted adapters and a separately approved local Whisper base baseline; adaptive buffering/PLC remain outside this increment. Claude is now the main LLM; Hindi-base Hinglish output and the separate multilingual lab remain unchanged. The owner approved Whisper base on 2026-10-19 and the exact pinned IndicConformer snapshot/runtime on 2026-10-21. Completed benchmark evidence is explicitly written to existing PostgreSQL tables after inference; no schema changes are needed. Existing bundled Silero weights may run locally. Neither local install changes production speech nor approves paid HF capacity.

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
- [ ] Complete representative multilingual/noisy/code-mix WER/CER, RTF, endpoint failure cases and native review. The completed local 15-case comparison uses only three source recordings and does not pass this gate.
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

`hf_public_lab` explicitly selects the discovered [community IndicConformer Space](https://1219naveen-indicconformer-asr.hf.space) for Gujarati/Hindi with the 600M CTC decoder and the [official public Whisper Space](https://hf-audio-whisper-large-v3.hf.space) for English with Whisper large-v3. The composition uses Gradio upload/call/SSE, is credential-free by default, with the later explicit official Whisper authentication exception below, and requires request metadata `data_classification=public_benchmark`. `scripts/speech_lab.py` additionally requires both `--allow-network` and `--public-benchmark`. Private caller audio is not permitted on this route. Public-service queue time and availability are external conditions; this is a lab selection, never an automatic production fallback.

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

At the 2026-10-19 hosted checkpoint, retain **IndicConformer 600M CTC for Gujarati/Hindi and Whisper large-v3 for English as the hosted development lab routing**, with strict 16 kHz mono PCM16, 30-second audio bound and 90-second hosted deadline. This preserves section 17's router and existing production speech. Indic English-span deletion makes an all-language Indic choice unjustified. The failed Whisper comparison prevents choosing it for Gujarati/Hindi from this evidence. Broader data, native code-mix and matched-model success are required before a production choice.

`PostgresBenchmarkStore` validates detached non-PII evidence before opening a session, then writes registry/run/results atomically. Identical immutable run keys return the existing ID; changed evidence or model provenance rolls back. The save CLI accepts completed reports only and rechecks aggregates. Store audio/ref/hypothesis in the public failure artifacts, not SQL; SQL holds finite aggregate metrics, manifest/config/environment and report location/hash.

Configured `roma` was read-only confirmed at `20261004_0003 (head)` before explicit benchmark inserts. Run `section18-indic-20261019-v1` persisted 12 samples / 72 metric rows; `section18-whisper-20261019-v1` persisted 15 attempted samples / 30 metric rows with failure status and only valid aggregates/error rates. A repeated Indic save returned the same run ID with `created:false`. No migration or pre-existing business row was modified.

### Section 18 gate position

- [x] Scoring, reproducible variants, saved/readable failures and immutable SQL evidence are implemented and exercised.
- [x] Existing out-of-order sequence tests remain part of offline CI; no sequence-check bypass is introduced.
- [x] Same-corpus local comparison completed on 2026-10-21: 12 shared supported successes plus three explicitly unsupported Indic English cases. Earlier hosted service failures remain historical evidence.
- [ ] Representative native code-mix/noise corpus, native review and final model approval: three read recordings plus stitched variants are insufficient.
- [ ] Complete Level 4 and adaptive/PLC gates.

Final integrated checks for this increment: **2,574 offline tests**, **56 + 1,002** CI-selected tests, Ruff, **62-file mypy**, pre-commit and Markdown links/anchors pass. See [verification](../12-verification.md#sections-1819-verification--2026-10-19) for boundaries and remaining approval gates.


## Hosted Whisper failure diagnosis — 2026-10-19

The original report remains unchanged. A separate official [OpenAI Whisper Space](https://huggingface.co/spaces/openai/whisper) probe could transcribe Gujarati, but its [complete same-manifest attempt](../../benchmarks/asr/section18/whisper-alternate-report.json) succeeded only **2/15 cases**, with 13 explicit service errors. The endpoint uses API `predict`; the retained hf-audio endpoint uses `transcribe`. Switching endpoints alone therefore did not fix the comparison, and the existing default is preserved.

Detailed queue checks on **both** official Spaces subsequently reported **ZeroGPU runs limit exceeded**. Gradio's simplified `/call/{api}/{event}` stream forwards `output.data` and drops `output.error`, producing the earlier `error:null`. The corrected Whisper adapter submits one job with a unique session and reads `/gradio_api/queue/data` for that same session. It validates the completion event ID, preserves all byte/deadline/cancellation bounds, exposes only a fixed quota exception and records `asr-hosted-quota-exhausted` in new benchmark rows. Unknown provider text remains redacted; no retry or model/endpoint fallback occurs. [Sanitized check](../../benchmarks/asr/section18/whisper-quota-check.json) records real adapter verification without private audio or credentials.

Quota exhaustion is an observed current service prerequisite, not proof of the cause of every historical error or of model language quality. Complete matched-model comparison remains **blocked by hosted capacity**. Resume with the same public corpus after quota is available, or explicitly configure an approved compatible endpoint with its matching API name; a token does not provision compute. At that earlier anonymous diagnostic stage no HF token was sent to a public Space; no credits were purchased, endpoint deployed or ASR weights downloaded. Existing Indic routing, production speech and historical SQL benchmark evidence remain unchanged.


## Authenticated Whisper follow-up — 2026-10-19

The user explicitly authorized using the `.env` HF token for Whisper. A single authenticated Gujarati probe succeeded. Official Gradio-client edge authentication is now opt-in via `HF_WHISPER_SPACE_AUTHENTICATED`; credentials are restricted to the two reviewed official Whisper hosts, with no redirect/proxy following. Anonymous defaults and the Indic community route remain credential-free. Token values never enter reports or ordinary errors.

The [authenticated same-manifest report](../../benchmarks/asr/section18/whisper-authenticated-report.json) records **7 successful / 8 quota-blocked cases**: English clean/noise/telephone WER/CER 0%; Gujarati WER **37.5% / 33.3% / 29.2%**, CER **13.28% / 11.72% / 10.16%**; Hindi clean WER **40%**, CER **12.17%**. Remaining noisy Hindi and stitched paths have no invented accuracy scores. The account's authenticated ZeroGPU allowance then exhausted; the official authenticated inference router separately returned credits unavailable. This fixes authentication, not account capacity or model quality. Keep the existing model choice/routing; representative/native approval and full matched comparison remain pending.

Subsequent benchmark code stops immediately on the first recognized quota rejection and retains resumable evidence, rather than submitting the remaining cases into exhausted capacity. No credit purchase, service deployment, new ASR weights, production switch or hosted microphone upload occurred. The token cannot satisfy Level 6's all-local ASR requirement; see [Level 6](level-06.md).


## Local Whisper base baseline — 2026-10-19

The owner authorized only multilingual Whisper **base**. The optional `voice-lab` extra now installs faster-whisper 1.2.1 / CTranslate2 4.8.3; the core/default dependency set and production provider choices remain unchanged. The [artifact manifest](../../benchmarks/asr/whisper-base-artifacts.json) pins `Systran/faster-whisper-base` revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`. Four files total **147,882,941 bytes** (about 148 MB decimal); weights remain ignored under `var/roma/models/whisper-base`. No tiny, larger Whisper, IndicConformer or new TTS weights were downloaded. The runtime is CPU INT8, two threads and one worker; this is not Apple Metal acceleration or a RAM measurement.

The new explicit `whisper_local` benchmark option validates every artifact size/SHA-256, requires `HF_HUB_OFFLINE=1`, refuses `--allow-network` and reuses the cache-only adapter. The official converted base snapshot has no `preprocessor_config.json`; faster-whisper supports its standard feature defaults. The adapter now requires the model/config/local tokenizer rather than inventing a missing feature file or allowing an implicit tokenizer fetch. Regression tests cover the official layout and missing tokenizer.

The [separate report](../../benchmarks/asr/section18/whisper-base-local-report.json) ran **all 15** same-manifest cases offline: **15 execution successes, zero provider errors**. Existing hosted large-v3 reports are unchanged and are not merged into this run. The following are per-case WER values, not accepted quality thresholds:

| Language | Clean WER | Noise WER | Telephone WER |
|---|---:|---:|---:|
| English | 0% | 33.3% | 0% |
| Gujarati | 100% | 100% | 100% |
| Hindi | 100% | 100% | 100% |
| Gujarati–English, stitched | 100% | 100% | 100% |
| Hindi–English, stitched | 100% | 100% | 100% |

Failure inspection found Gujarati rendered largely in Latin script, Hindi largely in Urdu script, and omitted English spans in stitched cases. No transliteration, reference rewriting or failed-case exclusion was used to improve scores. CER and exact edit counts remain in the report; WER/CER may exceed 100%. **Base is an integration/benchmark baseline and does not pass multilingual recognition quality.** IndicConformer remains the roadmap primary candidate; downloading it or escalating Whisper size requires a separate owner decision.

The first English case took **16.507 s**, including initial model/runtime loading. The remaining 14 cases had median wall latency **0.887 s**; these public clips are 6.54–19.03 s long, and one warm case took 9.407 s. Request RTF includes native loading/features/decoding/worker overhead; isolated inference RTF remains null. These figures are not conversational p95 or end-of-speech-to-audible measurements.

Thirteen public high-WER failure WAVs and review JSONs are saved under ignored `var/roma/asr-benchmark/failures-whisper-base-local`; read them with the existing failure-review command. The completed run was saved to configured `roma` as `section18-whisper-base-local-20261019-v1`, run ID `34c31fd5-9847-4109-9f43-c1b4a30b2912`: **15 samples, 90 metric rows, one distinct local model identity**. SQL status `succeeded` describes execution, not a quality gate. Schema remains at `20261004_0003`; no migration or business records were changed by the benchmark. See the [runbook](../runbook.md#local-whisper-base-benchmark) and [Level 6](level-06.md) for microphone evidence and remaining gates.

## Local IndicConformer readiness — 2026-10-21

The owner authorized local AI4Bharat IndicConformer after the Whisper base multilingual failures. The exact cache is `ai4bharat/indic-conformer-600m-multilingual` revision `2a77d305038dfc502fd9464e7d028b90381976cb`: **404 files / 2,556,502,929 bytes**, or **2.557 GB decimal / 2.381 GiB**. The optional `indic-asr` runtime adds PyTorch and Transformers; the observed installed package directories consume **645,087,232 bytes** (**645 MB / 615 MiB**) on this Mac. Combined logical size is about **3.202 GB / 2.982 GiB**, excluding filesystem allocation differences and shared dependencies. [Machine-readable install evidence](../../benchmarks/asr/indic-conformer-local-install.json) records versions and counts without credentials or transcripts.

The adapter now resolves the immutable cache with `local_files_only=True`, loads configuration from that exact snapshot and constructs the custom model without calling the upstream unpinned `from_pretrained` path. `HF_HUB_OFFLINE=1`, a 40-character revision and explicit `INDIC_TRUST_REMOTE_CODE=true` are mandatory. A missing or mismatched snapshot fails before inference and never falls back to network or Whisper.

Cache-only public-sample smokes completed for Gujarati and Hindi with zero provider errors. Gujarati exercised one cold and one warm segment; Hindi exercised one cold segment. Those smoke runs proved installation and adapter compatibility only; the following complete local comparison supersedes the unmeasured-corpus status, while native/representative review remains required. English remains on Whisper because this model's project contract supports Indic languages. Production Twilio/Sarvam routing is unchanged.

## Local IndicConformer versus Whisper base — 2026-10-21

The [Indic local report](../../benchmarks/asr/section18/indic-conformer-local-report.json), [fresh base report](../../benchmarks/asr/section18/whisper-base-local-20261021-report.json) and [checked comparison](../../benchmarks/asr/section18/indic-vs-whisper-base-local.json) use the exact same 15-case manifest, audio hashes, references and normalization as the historical base report. All 15 cases are accounted for: **12 supported Indic inferences succeeded; three English cases are explicitly unsupported**, with null accuracy and no model/fallback call. Base completed 15/15. The benchmark exits nonzero and SQL records Indic as failed because its requested English scope is unsupported; that status does not mean the 12 supported inferences failed.

The following WER/CER values pool edit counts across clean, synthetic-noise and telephone variants. Each language has only one source recording with three variants, so these are small-corpus results.

| Language | Indic WER | Base WER | Indic CER | Base CER |
|---|---:|---:|---:|---:|
| Gujarati | 12.50% | 100.00% | 3.13% | 101.30% |
| Hindi | 17.78% | 100.00% | 4.93% | 99.71% |
| Gujarati–English, stitched | 46.15% | 100.00% | 35.42% | 92.53% |
| Hindi–English, stitched | 43.70% | 100.00% | 38.73% | 96.65% |
| English | Unsupported | 11.11% | Unsupported | 6.77% |
| **Shared 12 supported cases** | **33.33%** | **100.00%** | **23.94%** | **96.91%** |

CER above 100% is valid when insertions exceed reference length. Fresh base hypotheses match its historical run exactly; timings are newly measured and the old report is preserved. Indic's per-case Gujarati WER is 12.5% for all variants; Hindi is 20% / 13.33% / 20%. The strongest remaining failure is deletion of the appended English speech: all three Gujarati stitched variants omit 15 words; Hindi stitched variants omit 15–16 words. This follows the language-specific decoder vocabulary/mask and cannot be repaired by script normalization or selecting a monolingual hint. No reference-driven correction, transliteration, language hint override or fabricated transcript was applied.

### Latency and runtime interpretation

The comparison excludes each process's first successful inference from **both** providers when forming the shared warm cohort. Indic's first success is Gujarati clean (16.618 s); base's is English clean (1.579 s). These include process-local loading, features, decoding and worker overhead; the operating-system file cache was not cleared. Indic uses CPU float32/CTC with runtime thread defaults; base uses CPU INT8/two threads/beam one. Different compute/thread settings and the small sample prevent an equal-resource or p95 claim.

| Shared warm language | Paired cases | Indic median | Base median |
|---|---:|---:|---:|
| Gujarati | 2 | 0.849 s | 0.930 s |
| Hindi | 3 | 0.798 s | 0.911 s |
| Gujarati–English, stitched | 3 | 1.091 s | 1.276 s |
| Hindi–English, stitched | 3 | 1.249 s | 0.961 s |
| **All shared warm** | **11** | **1.020 s** | **0.961 s** |

Shared-warm wall RTF is **0.065 Indic / 0.119 base**. Base's noisy Hindi–English case took 9.570 s and raises its duration-weighted RTF despite a lower median. The numbers measure ASR requests only; microphone endpointing, Claude and speaker latency are excluded.

### Fixes, retained failures and SQL evidence

The CLI now supports explicit offline `indic_local`, pinned cache provenance and a reproducible `compare` command. Comparison rejects mismatched manifests/audio/references/normalization, edited scores, duplicate cases and invalid timings; it records unsupported cases separately and uses the same warm case IDs for both models. Local SQL metric metadata now correctly marks the model revision as known; historical immutable rows remain untouched.

Nine verified public failure WAV/review pairs were exported and read under ignored `var/roma/asr-benchmark/failures-indic-conformer-local`: six stitched high-WER cases and three unsupported English cases. The SQL importer stores aggregates and report identity, with no reference/hypothesis text. New immutable run keys are:

- `section18-indic-conformer-local-20261021-v1`: run `fdcc645b-8713-4cd5-8bca-9c404d548161`, 15 attempted samples, 75 metric rows, one pinned model identity, status `failed` due solely to unsupported English scope.
- `section18-whisper-base-local-20261021-v1`: run `0bddaf5e-a879-422a-9a56-9b2fe5251e19`, 15 samples, 90 metric rows, one pinned model identity, execution status `succeeded`.

Retain **local IndicConformer CTC as the stronger Gujarati/Hindi development candidate and base as the explicit English/comparison baseline**. Native conversational code-mix data and quality review are still needed; this benchmark cannot approve the full Level 4 or microphone gate. Production carrier/speech configuration, Claude/TTS/booking behavior, schema and existing reports are unchanged. No downloads, hosted inference, paid LLM or microphone requests ran. Commands belong in the [runbook](../runbook.md#local-indicconformer-comparison).

Verification for this local comparison: **138 focused tests / 2,768 full offline tests passed**; Ruff, **76-file mypy**, pre-commit and the 155-package dependency lock pass. Markdown validation checks **69 files / 474 local links and anchors**, with zero issues. Failure WAV hashes and report recomputation were checked separately; see [verification](../12-verification.md#local-indicconformer-matched-comparison--2026-10-21).

## IndicConformer systematic diagnosis — 2026-10-21

The owner questioned whether the apparent remaining weakness came from integration errors. The [controlled offline diagnosis](../../benchmarks/asr/section18/indic-systematic-diagnosis.json) compared raw cached-model output, adapter output, decoder variants, audio lengths, vocabularies and independent edit-distance scoring. No production settings, weights, paid inference or microphone behavior changed.

**Confirmed system mismatch:** `run_benchmark` maps `gu-en` to `gu` and `hi-en` to `hi` before ASR. The raw model then applies that single language's output vocabulary/mask to the entire stitched waveform. Each selected Gujarati/Hindi vocabulary contains 257 entries and no English alphabet letters (excluding the special `<unk>` marker). This prevents Latin-script English output. In these samples the complete appended English sentence is deleted; it is not a transport truncation. Mixed clips reach 234/238 encoded frames versus 149/154 for the Indic-only clips, consistent with their longer full waveforms. The downstream transcript-based language router runs too late to restore words the ASR never emitted.

**Integration/scoring checks:** all **12/12** supported local transcripts exactly match the corresponding earlier hosted Indic transcripts. Four raw-model CTC calls exactly match the adapter. Input is aligned mono 16 kHz float32 derived from PCM16 at the documented scale, and peak amplitudes are finite/non-clipped. A separate edit-distance implementation reproduces every WER/CER across all 12 supported cases. These checks found no evidence that this result is caused by local PCM conversion, our model wrapper or edit-count arithmetic.

**Decoder/config A/B:** on the four clean Indic/stitched clips, RNNT and CTC have the same WER. RNNT slightly improves Hindi CER (clean: 5.22% to 4.35%) but still omits the English sentence. Both the pinned config's SOS=256 and the upstream custom class's default SOS=5632 produced the same RNNT hypotheses in this probe. Therefore neither switching decoder nor guessing a different SOS value fixes the observed English-span loss.

**Interpretation correction:** the headline 33.33% WER includes six deliberately stitched bilingual cases. The six Gujarati/Hindi-only variants have **25 word edits / 162 reference words = 15.43% WER**, and **29 character edits / 729 characters = 3.98% CER**. Some word errors are spelling/spacing differences, which standard WER penalizes; the references and scoring are preserved. Fifteen cases still derive from just three source recordings, not fifteen independent speakers/recordings.

As a diagnostic using the benchmark's known source boundaries, joining the already-recognized Indic source with the already-recognized English source gives clean stitched Gujarati–English **7.69% WER** and Hindi–English **13.33% WER**. No reference words were injected, but the boundaries are known by construction. This is evidence for a separate future audio-language/span-routing experiment, not proof that the present router solves natural code-mix. A production change requires measured automatic span routing or an approved model that supports the required mixed-script output. Keep the existing production route and deferred native/microphone gates.
