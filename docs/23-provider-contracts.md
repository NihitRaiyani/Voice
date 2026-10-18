# Provider contracts

**Level 1 section 7.** STT, LLM, TTS, embeddings and telephony now have
transport-neutral provider interfaces. This connects the backend track to the
future local-AI track without wiring live model loading into the realtime path
yet.

## Interfaces

| Interface | Module | Method | Current adapters |
|---|---|---|---|
| `STTProvider` | `roma.providers.ai.contracts` | `transcribe(STTRequest) -> STTResult` | `MockSTTProvider`; section 16 cache-only `IndicConformerSTT`, faster-whisper `WhisperSTT`, opt-in custom-endpoint `HuggingFaceSTT` |
| `LLMProvider` | `roma.providers.ai.contracts` | `generate(LLMRequest) -> AsyncIterator[LLMChunk]` | `MockLLMProvider`; section 11 `OpenAILLM` with env-selected model/key and bounded SDK streaming |
| `TTSProvider` | `roma.providers.ai.contracts` | `synthesize(TTSRequest) -> TTSResult` | `MockTTSProvider`; `IndicTTSProvider` placeholder |
| `EmbeddingProvider` | `roma.providers.ai.contracts` | `embed(EmbeddingRequest) -> EmbeddingResult` | `MockEmbeddingProvider`; `LocalMultilingualEmbedding` placeholder |
| `TelephonyProvider` | `roma.providers.telephony.base` | `place_call(PlaceCallRequest) -> PlaceCallResult` | `MockTelephonyProvider`; `TwilioTelephonyProvider` |

TTS and embedding placeholders remain unavailable until their levels. Section 16 speech adapters are implemented but require an explicitly provisioned optional runtime/cache or compatible hosted endpoint. Their construction never loads/downloads weights. Missing prerequisites raise `ProviderUnavailable`; software interface tests do not imply native model quality or GPU admission.

## Config switching

Provider names live in settings and `.env.example`:

```bash
STT_PROVIDER=mock
LLM_PROVIDER=mock
# Select openai only for separately authorized runs with OPENAI_API_KEY and OPENAI_MODEL set
TTS_PROVIDER=mock
EMBEDDING_PROVIDER=mock
TELEPHONY_PROVIDER=twilio
```

Factories in `roma.providers.ai.registry` and
`roma.providers.telephony.registry` return the selected adapter. The AI defaults
are mocks so automated tests and offline development do not call Sarvam, OpenAI
or any other paid provider. Telephony defaults to Twilio to preserve current
runtime behavior; tests select `TELEPHONY_PROVIDER=mock` explicitly.

## Current integration boundary

The contracts are ready for services and later model levels. The existing
Pipecat media pipeline still uses its current OpenAI/Sarvam services directly;
Section 11 adds an SDK-level OpenAI text seam with mocks; the existing media processors remain until later audio/telephony integration has quality and latency evidence. Sections 15–17 compose a separate speech-input lab with STT adapters and persisted language metadata; actual speech-model inference/quality, live replacement and embeddings remain gated. Section 7 itself never installs model weights or adds RAG.

Students should be able to write one adapter that satisfies a provider protocol
and switch to it with one config value, while keeping tests on mocks by default.

Return to the [documentation index](README.md).

STT config supports `mock`, `indic_conformer`, `whisper` and `hf_indic_conformer`. Local loaders are cache-only; Indic custom code needs offline mode and a pinned reviewed revision. The hosted option needs an explicit compatible custom endpoint and token; a Hub model ID/token alone is insufficient. English is not supported by the selected Indic model; use the explicit comparison baseline rather than silently overriding a hint. [Runbook](runbook.md#sections-1517-speech-input-lab) owns commands; [Level 4](levels/level-04.md) owns evidence and remaining quality gates.
