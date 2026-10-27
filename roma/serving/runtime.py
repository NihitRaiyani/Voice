"""Explicit pinned local-runtime composition; never selected by production implicitly."""

from __future__ import annotations

import os

from fastapi import FastAPI

from roma.core.ai_contracts import AudioFormat, STTRequest, TTSRequest
from roma.core.serving_settings import SpeechServingSettings
from roma.core.speech_settings import SpeechSettings
from roma.core.tts_settings import TTSSettings
from roma.serving.app import create_speech_app


def build_local_speech_app(settings: SpeechServingSettings | None = None) -> FastAPI:
    settings = settings or SpeechServingSettings()
    settings.require_key()
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise ValueError("Local serving requires HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")
    # These imports construct lightweight adapters; model imports/hash/load happen in warmup.
    from roma.providers.ai.indic_tts import IndicTTSProvider
    from roma.providers.ai.whisper_mlx import WhisperMLXSTT

    speech = SpeechSettings()
    voice = TTSSettings()
    stt = WhisperMLXSTT(
        model_id=speech.whisper_mlx_model_dir,
        policy=speech.turn_policy(),
        rejection=speech.batch_rejection(),
        booking_vocabulary=speech.whisper_booking_vocabulary,
        timeout_secs=speech.stt_timeout_secs,
        max_audio_secs=min(speech.stt_max_audio_secs, settings.speech_service_max_audio_secs),
    )
    tts = IndicTTSProvider(
        manifest_path=voice.indic_tts_manifest,
        python_path=voice.indic_tts_python,
        voice=voice.tts_voice,
        timeout_secs=voice.tts_timeout_secs,
        max_text_bytes=min(voice.tts_max_text_bytes, settings.speech_service_max_text_bytes),
        max_audio_secs=min(voice.tts_max_audio_secs, settings.speech_service_max_audio_secs),
    )

    async def warmup() -> None:
        await stt.transcribe(STTRequest(b"\0\0" * 16000, AudioFormat(16000), "hi"))
        # Same finite warmup phrase as the retained local TTS adapter; discard PCM.
        await tts.warmup(TTSRequest("नमस्ते।", language="hi", voice=voice.tts_voice))

    return create_speech_app(settings, stt=stt, tts=tts, warmup=warmup)
