# Level 5 — Local TTS and phrase cache

**Roadmap section:** 19. **Status:** Local provider, safety-audited bounded phrase cache, normalization, carrier framing and measurement lab implemented. Actual local inference passed all 18 cases in Gujarati, Hindi and English, with nine synthesis misses and nine cache hits. Native quality approval remains separate.

## Scope and prerequisites

Reuse Level 1 `TTSProvider`, section 12 deterministic safety/audit and section 14 audio contracts. The owner explicitly approved required local TTS runtime/weight downloads on 2026-10-19. Provision these separately from the application's locked environment. Preserve the live Sarvam/Pipecat voice path and fixed phrase assets; multilingual output remains an isolated lab, while live Roma replies Hindi-base Hinglish.

## Section 19 implementation

| Boundary | Implementation and invariant |
|---|---|
| Provider/config | `roma/providers/ai/indic_tts.py`, `roma/core/tts_settings.py`, registry: explicit `indic_tts` selection, no construction/startup downloads. `TTSProvider` returns bounded mono PCM16 at 8/16 kHz. |
| Acoustic/vocoder | AI4Bharat Indic-TTS FastPitch predicts acoustic features; HiFi-GAN generates waveform. A reviewed manifest pins the isolated runtime distribution/version, both checkpoints, both configs and speaker file, with per-file SHA-256 identities. Gujarati, Hindi and English are explicit languages; female/male voice must exist in the selected manifest. |
| Worker | `_indic_tts_worker.py` calls the upstream Coqui `Synthesizer` API on CPU. Optional heavy imports stay outside the app process. Finite timeout/cancellation kills and reaps the subprocess; no cloud fallback. Each cache miss cold-loads the model: startup/load time belongs in the measured synthesis duration. |
| Normalization | `roma/services/tts_service.py`: NFC/spacing, digit names in the selected language, calendar-validated ISO `YYYY-MM-DD` / Indian `DD/MM/YYYY`, reviewed pronunciation for SEO/SEM/PPC/CRM/Digital Marketing/Google Ads. Years/numbers are deliberately digit-by-digit; native cardinal pronunciation/naturalness are not claimed. |
| Safety | Check/audit raw text before spelling digits can hide amount signals; check/audit the actual normalized final line before any cache lookup or inference. Cache hits have the same checks. Blocked/error wording uses existing approved replacements; no new safety rules or fixed audio changes. |
| Phrase cache | Process-local LRU defaults 64 entries / 8 MiB. Identity includes exact safe normalized text, language, voice, model/artifact version, normalization version and format. Singleflight shares simultaneous identical misses. Admission, memory and inference concurrency are bounded; failures/cancellation do not create cache entries. Process restart discards the cache. |
| Carrier output | Worker resamples its waveform using polyphase filtering to the requested PCM rate. Reference 16→8 kHz conversion, G.711 encoding and exact 20 ms frames follow; only the final tail is explicitly zero-padded. Odd PCM/oversized/invalid output is rejected. |
| Measurement | `roma/eval/tts.py`, `scripts/tts_lab.py`: short/medium/long lines in Gujarati/Hindi/English, each twice; report synthesis time, total elapsed, duration, synthesis RTF and cache status. Atomic reports retain sanitized case failures. Optional WAVs stay under ignored `var/roma`. |

Settings default to mock providers. Bounds: 4 KiB text, 30 seconds audio, one synthesis worker, 16 pending requests, 60-second total deadline; explicit local labs may use a 180-second deadline. Cache validation includes current artifact identity so changed files cannot reuse stale audio. New local cache state is independent of the existing live fixed-phrase loader/Redis mirror; no bulk paid rendering occurs.

## Runtime and artifact provisioning

The [official inference guide](https://github.com/AI4Bharat/Indic-TTS/blob/master/inference/README.md) lists Gujarati, Hindi and Indian English. Its [sample](https://github.com/AI4Bharat/Indic-TTS/blob/master/inference/sample.py) supplies the acoustic/config/speaker/vocoder paths used by this adapter. Runtime baseline: Python 3.11, Coqui TTS 0.22.0, Torch/Torchaudio 2.5.1, Transformers 4.46.3 and NumPy 1.26.4, isolated under ignored `var/roma/tts-runtime`. The [runtime snapshot](../../benchmarks/tts/runtime-macos-arm64-py311.txt) records exact installed distributions on this Mac ARM64; it does not change app dependencies.

`scripts/setup_indic_tts.py` performs explicit approved official-release downloads, bounded extraction and identity recording. No publisher checksums are available; downloaded hashes are provenance/consistency identities, not independent publisher attestation. Archive transfer failures retain verified segments for explicit resume. Model files/runtime remain ignored runtime artifacts, never committed. [Runbook](../runbook.md#section-19-local-tts-and-phrase-cache) owns setup/reproduction commands.

## Actual evidence and acceptance gate

On **2026-10-19**, all three official archives completed and all 15 extracted file hashes were independently verified: **4,975,192,844 bytes** of required artifacts under ignored runtime storage. The [manifest](../../benchmarks/tts/section19-manifest.json) records release URLs, whole-archive SHA-256 and per-file identities. Split 5-second connection / 60-second read deadlines resolve measured NAT64 address stalls without weakening range/content verification. The worker rebinds legacy speaker paths in a temporary config, leaving original verified files unchanged.

The fresh [actual report](../../benchmarks/tts/section19-report.json) records **18/18 successful cases**, zero errors, nine misses/stores and nine hits; wall time **91.712 seconds**. All nine WAVs were independently checked as nonempty mono PCM16 / 8 kHz, with waveform hashes matching both the report and repeated cache hits. This run used the local CPU runtime, female voice, explicit 180-second deadline and **60-second lab audio bound**. Inference made no network calls or automatic downloads; separately authorized setup had already downloaded the models.

| Language / length | Audio duration | Cold synthesis | Synthesis RTF | Audited cache-hit elapsed |
|---|---:|---:|---:|---:|
| hi / short | 2.125 s | 8.960 s | 4.216 | 12.760 ms |
| hi / medium | 7.327 s | 9.492 s | 1.296 | 38.518 ms |
| hi / long | 15.070 s | 11.006 s | 0.730 | 69.826 ms |
| gu / short | 3.646 s | 7.471 s | 2.049 | 17.191 ms |
| gu / medium | 14.769 s | 10.395 s | 0.704 | 64.832 ms |
| gu / long | 32.279 s | 14.014 s | 0.434 | 134.224 ms |
| en / short | 2.323 s | 7.372 s | 3.174 | 11.126 ms |
| en / medium | 8.500 s | 8.424 s | 0.991 | 36.984 ms |
| en / long | 16.861 s | 10.235 s | 0.607 | 69.198 ms |

The default synthetic lab completed 18 cases: three lengths × three languages × two repetitions, with nine misses/stores and nine hits. It uses deterministic tones, so these measurements prove transport/cache instrumentation only. Offline tests cover multilingual normalization, invalid dates, amount-before-normalization safety, mandatory audit on hits, artifact tampering/path escape, format/size bounds, cache isolation/LRU, singleflight, timeout and process cancellation. PostgreSQL evidence persistence reuses section 18's short transactional store, outside inference.

- [x] Actual local Indic-TTS synthesis in Gujarati, Hindi and English with measured latency/RTF.
- [x] Repeated phrase hit/miss and language/voice/model/text invalidation behavior tested.
- [x] Safety, cancellation, bounded cache and carrier-frame contracts tested with PCM fakes.
- [ ] Native intelligibility/code-mix/naturalness review and final voice/model approval.
- [x] Exact approved-line Hindi phonetic bindings synthesize locally and retain canonical safety/audit identity.
- [ ] Native listening approval of these pronunciations before live integration.

The first default-30-second run correctly rejected both Gujarati-long attempts; direct bounded diagnosis measured **32.279 seconds** of output. The final lab explicitly used 60 seconds and retained all original prompts. Default provider/service limits remain 30 seconds. Preserve the initial failure report under ignored `var/roma/tts-lab/report-default30.json`; it predates the benchmark-provenance correction and is not imported as authoritative SQL evidence. [Runbook](../runbook.md#section-19-local-tts-and-phrase-cache) reproduces the passing configuration.

Native Indic alphabets do not necessarily contain Roman Hinglish. The installed tokenizer silently discards unsupported characters, so the worker now rejects unsupported letters/marks/numbers **before synthesis**, rather than caching corrupted speech. The initial Gujarati unsafe-fee test correctly failed without caching its unsupported Roman replacement. The scoped follow-up below resolves that local rendering failure; existing live approved assets remain intact. Invisible format controls are also rejected at the new normalization boundary before digit spelling can hide a fee claim.

Failed inference retains its resolved model identity and contributes to that model's error rate. Unresolved artifact/setup reports remain readable on disk but cannot fabricate model-registry evidence. The completed actual report was saved in configured `roma` as `section19-indic-20261019-v1`: **18 samples / 81 metric rows**. Readback succeeded; repeating the save returned the same run ID with `created:false`. It uses the same short benchmark transaction as section 18; no SQL session spans inference.

A cache hit removes model inference but still incurs safety/identity/framing work. Report that total, not a claimed zero-latency hit. The adapter returns complete waveforms: elapsed time is not streaming TTFT/first-audio latency or end-of-speech-to-audible response. Those complete voice measurements remain deferred to the later requested phases. No local-model telephony integration or serving framework is introduced here.

Return to the [documentation index](../README.md).

Final integrated checks for this increment: **2,574 offline tests**, **56 + 1,002** CI-selected tests, Ruff, **62-file mypy**, pre-commit and Markdown links/anchors pass. See [verification](../12-verification.md#sections-1819-verification--2026-10-19) for boundaries and remaining approval gates.


## Approved safety pronunciation follow-up — 2026-10-19

`roma/domain/safety/pronunciation.py` binds the six distinct existing canonical lines to literal Devanagari pronunciations of the same Hindi-base Hinglish words. Fees/discounts share one line. Exact canonical membership and pinned canonical/spoken SHA-256 identities are mandatory: changed wording requires an explicit new binding. This is not general transliteration or multilingual translation. All three requested language routes use the actual Hindi model for these production-policy fallbacks.

The original canonical rule activation is retained. `SafetyService.check_pronunciation()` independently screens the actual spoken text and records a rendering-identity event before model lookup/cache access. Only the exact pinned certificate deferral may adjudicate its known generic-CERT false positive; additional categories, filter failures, stale/forged bindings and missing mappings cannot use that exception. Audit outage selects the pinned hard-fail pronunciation with `audit_ok=false`; cancellation releases no audio. Global rules and `SafetyService.check()` are unchanged. See [guardrails](../04-guardrails.md#local-approved-line-pronunciation).

The [actual safety report](../../benchmarks/tts/section19-safety-report.json) records **24/24 successful cases**, six cold misses/stores and **18 audited hits** across requested Hindi/Gujarati/English, in **54.594 seconds**. Six mono PCM16/8 kHz WAVs and their hashes were independently verified; repeat results retain matching waveform identity. This reused installed runtime/weights with no network or downloads. Audit sink is explicitly synthetic in-memory; this is not a claim of durable call-event persistence or human listening approval. [Runbook](../runbook.md#section-19-local-tts-and-phrase-cache) reproduces it.

| Approved line | Audio | Cold synthesis |
|---|---:|---:|
| Fees / discount | 6.015 s | 10.776 s |
| Salary | 5.516 s | 9.193 s |
| Placement support | 8.221 s | 9.228 s |
| Certificate | 5.817 s | 8.353 s |
| Hard fail | 2.648 s | 7.207 s |
| Permitted EMI | 2.718 s | 8.007 s |

Result metadata distinguishes pre-render canonical text/hash, emitted canonical text, actual spoken text/hash, requested/actual language and voice. On rendering-audit outage the emitted canonical can differ from the initial canonical; callers must inspect `audit_ok`. Cache identity includes actual spoken text/language/voice/model and pronunciation version/hash. Native intelligibility review and complete voice integration remain later gates.
