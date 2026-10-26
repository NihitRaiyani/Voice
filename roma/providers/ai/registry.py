"""Config-driven AI provider selection."""

from __future__ import annotations

from roma.providers.ai.claude_llm import ClaudeLLM
from roma.providers.ai.contracts import (
    EmbeddingProvider,
    LLMProvider,
    ProviderUnavailable,
    STTProvider,
    TTSProvider,
)
from roma.providers.ai.hf_asr import HFHostedSTT, HFWhisperSTT
from roma.providers.ai.local import (
    IndicTTSProvider,
    LocalMultilingualEmbedding,
)
from roma.providers.ai.mocks import (
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSTTProvider,
    MockTTSProvider,
)
from roma.providers.ai.stt import HuggingFaceSTT, IndicConformerSTT, WhisperSTT
from roma.providers.ai.whisper_mlx import WhisperMLXSTT


def build_stt_provider(settings) -> STTProvider:
    match settings.stt_provider:
        case "mock":
            return MockSTTProvider()
        case "indic_conformer":
            return IndicConformerSTT(
                model_id=settings.indic_model_id,
                revision=settings.indic_model_revision,
                language=settings.stt_language,
                decoder=settings.indic_decoder,
                trust_remote_code=settings.indic_trust_remote_code,
                timeout_secs=settings.stt_timeout_secs,
                max_audio_secs=settings.stt_max_audio_secs,
            )
        case "whisper":
            return WhisperSTT(
                model_id=settings.whisper_model_id,
                timeout_secs=settings.stt_timeout_secs,
                max_audio_secs=settings.stt_max_audio_secs,
            )
        case "whisper_mlx":
            return WhisperMLXSTT(rejection=settings.batch_rejection(), policy=settings.turn_policy(), model_id=settings.whisper_mlx_model_dir, timeout_secs=settings.stt_timeout_secs, max_audio_secs=settings.stt_max_audio_secs, booking_vocabulary=settings.whisper_booking_vocabulary)
        case "hf_public_lab":
            return HFHostedSTT(
                endpoint_url=settings.hf_indic_space_url,
                whisper_endpoint_url=settings.hf_whisper_space_url,
                whisper_api_name=settings.hf_whisper_api_name,
                whisper_token=(
                    settings.hf_token.get_secret_value()
                    if settings.hf_whisper_space_authenticated
                    else None
                ),
                language=settings.stt_language,
                decoder=settings.indic_decoder,
                timeout_secs=settings.hf_public_timeout_secs,
                max_audio_secs=settings.stt_max_audio_secs,
            )
        case "hf_whisper":
            return HFWhisperSTT(
                token=settings.hf_token.get_secret_value(),
                model_id=settings.hf_whisper_model,
                timeout_secs=settings.stt_timeout_secs,
                max_audio_secs=settings.stt_max_audio_secs,
            )
        case "hf_indic_conformer":
            return HuggingFaceSTT(
                endpoint_url=settings.hf_asr_endpoint_url,
                token=settings.hf_token.get_secret_value(),
                language=settings.stt_language,
                decoder=settings.indic_decoder,
                timeout_secs=settings.stt_timeout_secs,
                max_audio_secs=settings.stt_max_audio_secs,
            )
    raise ValueError(f"unknown STT provider {settings.stt_provider!r}")


def build_llm_provider(settings) -> LLMProvider:
    match settings.llm_provider:
        case "mock":
            return MockLLMProvider()
        case "claude":
            return ClaudeLLM(settings)
        case "openai":
            raise ProviderUnavailable(
                "OpenAI runtime selection is disabled; select claude or mock"
            )
    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")


def build_tts_provider(settings) -> TTSProvider:
    match settings.tts_provider:
        case "mock":
            return MockTTSProvider()
        case "indic_tts":
            return IndicTTSProvider(
                manifest_path=settings.indic_tts_manifest,
                python_path=settings.indic_tts_python,
                voice=settings.tts_voice,
                timeout_secs=settings.tts_timeout_secs,
                max_text_bytes=settings.tts_max_text_bytes,
                max_audio_secs=settings.tts_max_audio_secs,
            )
    raise ValueError(f"unknown TTS provider {settings.tts_provider!r}")


def build_embedding_provider(settings) -> EmbeddingProvider:
    match settings.embedding_provider:
        case "mock":
            return MockEmbeddingProvider()
        case "local_multilingual":
            return LocalMultilingualEmbedding(
                manifest_path=getattr(settings, "embedding_manifest", ""),
                timeout_secs=getattr(settings, "embedding_timeout_secs", 10.0),
                max_text_bytes=getattr(settings, "embedding_max_text_bytes", 8192),
                max_batch_size=getattr(settings, "embedding_max_batch_size", 4),
                cpu_threads=getattr(settings, "embedding_cpu_threads", 2),
            )
    raise ValueError(f"unknown embedding provider {settings.embedding_provider!r}")


__all__ = [
    "build_embedding_provider",
    "build_llm_provider",
    "build_stt_provider",
    "build_tts_provider",
]
