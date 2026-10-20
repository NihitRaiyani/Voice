# Provider contracts

**Level 1 section 7.** STT, LLM, TTS, embeddings and telephony now have
transport-neutral provider interfaces. This connects the backend track to the
future local-AI track without wiring live model loading into the realtime path
yet.

## Interfaces

| Interface | Module | Method | Current adapters |
|---|---|---|---|
| `STTProvider` | `roma.providers.ai.contracts` | `transcribe(STTRequest) -> STTResult` | `MockSTTProvider`; section 16 cache-only `IndicConformerSTT`, faster-whisper `WhisperSTT`, opt-in custom-endpoint `HuggingFaceSTT`, public-lab `HFHostedSTT` and separate router `HFWhisperSTT` |
| `LLMProvider` | `roma.providers.ai.contracts` | `generate(LLMRequest) -> AsyncIterator[LLMChunk]` | `MockLLMProvider`; section 11 `ClaudeLLM` with env-selected model/key and bounded SDK streaming |
| `TTSProvider` | `roma.providers.ai.contracts` | `synthesize(TTSRequest) -> TTSResult` | `MockTTSProvider`; section 19 manifest-pinned local `IndicTTSProvider` (isolated CPU worker) |
| `EmbeddingProvider` | `roma.providers.ai.contracts` | `embed(EmbeddingRequest) -> EmbeddingResult` | `MockEmbeddingProvider`; `LocalMultilingualEmbedding` placeholder |
| `TelephonyProvider` | `roma.providers.telephony.base` | `place_call(PlaceCallRequest) -> PlaceCallResult` | `MockTelephonyProvider`; `TwilioTelephonyProvider` |

The embedding placeholder remains unavailable until its level. Local TTS requires explicitly provisioned runtime/artifacts and validates all five checkpoint/config/speaker hashes; construction performs no download or model load. Section 16 speech adapters are implemented but require an explicitly provisioned optional runtime/cache or an explicitly selected compatible hosted route. Their construction never loads/downloads weights. Missing prerequisites raise `ProviderUnavailable`; software interface tests do not imply native model quality or GPU admission.

## Config switching

Provider names live in settings and `.env.example`:

```bash
STT_PROVIDER=mock
LLM_PROVIDER=mock
# Select claude for authorized runs with ANTHROPIC_API_KEY and ANTHROPIC_MODEL set
TTS_PROVIDER=mock
EMBEDDING_PROVIDER=mock
TELEPHONY_PROVIDER=twilio
```

Factories in `roma.providers.ai.registry` and
`roma.providers.telephony.registry` return the selected adapter. Claude is the main production LLM. The example above and automated test/CLI configurations explicitly select mocks, so offline development does not call Sarvam, Claude or another paid provider. Telephony defaults to Twilio to preserve current
runtime behavior; tests select `TELEPHONY_PROVIDER=mock` explicitly.

## Current integration boundary

The contracts are ready for services and later model levels. The Pipecat media pipeline retains its speech services and uses a Claude wording/extraction adapter.
Section 11 provides an SDK-level Claude text seam with mocks; the existing media processors remain until later audio/telephony integration has quality and latency evidence. Sections 15–17 compose a separate speech-input lab with STT adapters and persisted language metadata; three public benchmark clips now have hosted inference evidence; representative speech quality, live replacement and embeddings remain gated. Section 7 itself never installs model weights or adds RAG.

Students should be able to write one adapter that satisfies a provider protocol
and switch to it with one config value, while keeping tests on mocks by default.

STT config supports `mock`, `indic_conformer`, `whisper`, `hf_indic_conformer`, `hf_public_lab` and `hf_whisper`. Local loaders are cache-only; Indic custom code needs offline mode and a pinned reviewed revision. Real STT adapters validate immutable nonempty aligned mono 16 kHz PCM16 bytes, exact format metadata and supported language before model loading/inference. Returned text is bounded before and after NFC/digit/whitespace normalization.

`hf_public_lab` explicitly composes the selected public Gujarati/Hindi IndicConformer 600M CTC Space and English Whisper large-v3 Space. It accepts only `metadata["data_classification"] == "public_benchmark"`; private caller audio is forbidden. `scripts/speech_lab.py` requires both public-benchmark and network flags; the dedicated FLEURS smoke declares public metadata itself. No HF token is sent to public Spaces. Calls have single-request admission, bounded Gradio upload/call/SSE responses and a total public timeout (default 90 seconds, maximum 120). Availability/queues are external lab conditions.

`hf_whisper` is the separate explicitly selected official HF inference-router adapter and sends its token only there. A 402 credit failure is not retried or routed elsewhere. `hf_indic_conformer` retains the optional compatible private custom endpoint/token contract; a Hub model ID/token alone is insufficient. English is not supported by the selected Indic model; use an explicit Whisper route rather than silently overriding a hint. [Runbook](runbook.md#sections-1517-speech-input-lab) owns commands; [Level 4](levels/level-04.md) owns evidence and remaining quality gates.

Return to the [documentation index](README.md).

ASR maximum-size chunks are aggregated inside `SpeechInputService` until true end-of-turn (silence/flush/discontinuity). Exact-cap silence is a control boundary, never an empty inference request or a second caller turn. Pending text is bounded by the existing caller limit; errors/overflow fail the whole turn safely.

## Section 19 local synthesis boundary

`roma/providers/ai/indic_tts.py` verifies a local five-file acoustic/vocoder/speaker manifest and invokes a pinned isolated CPU runtime through `TTSProvider`. Worker cancellation kills/reaps the process; no model-name downloader or cloud fallback is invoked. `roma/services/tts_service.py` owns normalization, mandatory safety audits, bounded cache/admission and carrier framing. The new lab is separate from the existing Sarvam media integration. [Level 5](levels/level-05.md) records actual compatibility, measurements and limits; [runbook](runbook.md#section-19-local-tts-and-phrase-cache) owns setup.

Local TTS inference now has actual three-language evidence. The worker rebinds legacy speaker paths only in a temporary config, rejects unsupported lexical characters, and preserves hashed source artifacts. The service rejects invisible format controls before spelling digits. Default audio bound stays 30 seconds; the long Gujarati lab uses an explicit 60-second bound. Exact approved safety lines now have hash-bound Devanagari pronunciation via the same safety service, with Hindi synthesis and distinct canonical/spoken metadata; actual 24-case local evidence is in Level 5. Native listening approval and live integration remain gated.


Whisper public-Space configuration pairs `HF_WHISPER_SPACE_URL` with `HF_WHISPER_API_NAME`: the retained hf-audio default uses `transcribe`; the separately selected official OpenAI Whisper Space uses `predict`. Both names are closed validated choices. Whisper submits once with an isolated session and reads that same job's detailed queue events, correlating event identity; the simplified SSE endpoint discards the service's error detail. Recognized ZeroGPU exhaustion becomes `HFASRQuotaExceeded` and benchmark code `asr-hosted-quota-exhausted`, never a transcript/accuracy score. Other remote errors are sanitized. No automatic retry, endpoint switch or token disclosure is introduced. Available hosted capacity remains an external prerequisite; see [current comparison evidence](levels/level-04.md#hosted-whisper-failure-diagnosis--2026-10-19).


### Explicit official Whisper authentication and local voice composition

The owner's later instruction authorizes the existing HF token for the reviewed official Whisper Spaces. `HF_WHISPER_SPACE_AUTHENTICATED` defaults false; when explicitly enabled, only those fixed hosts receive Gradio's `X-HF-Authorization` edge header. The Indic community host and arbitrary configured hosts never receive it; authenticated arbitrary hosts are rejected before network access. Token presence alone does not enable authentication, and reports retain only an authenticated boolean. Quota failures stop a benchmark run; authenticated free allowance and router credits remain external prerequisites.

Section 20 `LocalLanguageSTT` composes local Indic Gujarati/Hindi and explicit Whisper English without provider-error fallback. Local microphone mode rejects all hosted provider configurations. `TimedLLMProvider` measures extraction/generation duration while preserving the same LLMProvider interface and Claude budget. The local-only `voice_hi` output policy requests Hindi-base Hinglish in Devanagari for Indic-TTS; the existing default remains Roman Hinglish. Exact discovery/booking pronunciations are separately checked, and the final TTS result now includes safety/normalization duration. [Level 6](levels/level-06.md) owns the integration and remaining gate evidence.

Claude Messages usage extends `LLMChunk` with separate uncached input, cache-creation input and cache-read input accounting. Exact model pricing and conservative cache-write admission belong to `ClaudeLLM`; service/voice wrappers preserve usage-known state and terminal metrics. Haiku 5.5 uses low effort, no temperature/top_p/top_k. See [the active Claude contract](25-claude-llm.md). OpenAI is historical only and never a runtime fallback.
