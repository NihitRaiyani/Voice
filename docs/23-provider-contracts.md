# Provider contracts

**Level 1 section 7.** STT, LLM, TTS, embeddings and telephony now have
transport-neutral provider interfaces. This connects the backend track to the
future local-AI track without wiring live model loading into the realtime path
yet.

## Interfaces

| Interface | Module | Method | Current adapters |
|---|---|---|---|
| `STTProvider` | `roma.providers.ai.contracts` | `transcribe(STTRequest) -> STTResult` | `MockSTTProvider`; `IndicConformerSTT` and `WhisperSTT` placeholders |
| `LLMProvider` | `roma.providers.ai.contracts` | `generate(LLMRequest) -> AsyncIterator[LLMChunk]` | `MockLLMProvider`; `Qwen3TransformersLLM` direct adapter (L2 section 11); `Qwen3MLXLLM` optional Apple INT4 adapter; `Qwen3VLLMClient` placeholder |
| `TTSProvider` | `roma.providers.ai.contracts` | `synthesize(TTSRequest) -> TTSResult` | `MockTTSProvider`; `IndicTTSProvider` placeholder |
| `EmbeddingProvider` | `roma.providers.ai.contracts` | `embed(EmbeddingRequest) -> EmbeddingResult` | `MockEmbeddingProvider`; `LocalMultilingualEmbedding` placeholder |
| `TelephonyProvider` | `roma.providers.telephony.base` | `place_call(PlaceCallRequest) -> PlaceCallResult` | `MockTelephonyProvider`; `TwilioTelephonyProvider` |

The remaining placeholder local/open-source classes are deliberately present but
unavailable. These placeholders raise `ProviderUnavailable` with the level that must complete
before production promotion. `Qwen3MLXLLM` adds pinned 1.7B and explicit 4B INT4 Apple development profiles, immutable-prefix caching and cache/memory metrics. Text comparison does not approve native quality or voice latency. `Qwen3TransformersLLM` now loads directly with Transformers in the
[section 11 lab](25-local-transformers-llm.md); it remains a candidate pending
suitable-host/native quality evidence. Remaining placeholders keep config names stable without claiming their later-level runtimes or benchmark approval.

## Config switching

Provider names live in settings and `.env.example`:

```bash
STT_PROVIDER=mock
LLM_PROVIDER=mock
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
replacing those processors requires Level 2/4/5/10 work with quality and
latency evidence. Section 7 does not load local models, start vLLM, add
embeddings/pgvector or remove the cloud comparison path.

Students should be able to write one adapter that satisfies a provider protocol
and switch to it with one config value, while keeping tests on mocks by default.

Return to the [documentation index](README.md).
