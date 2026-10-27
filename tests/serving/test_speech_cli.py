import asyncio

import pytest
from pydantic import SecretStr
from roma.core.ai_contracts import STTResult
from roma.core.serving_settings import SpeechServingSettings


def test_cli_requires_explicit_model_authorization_before_loading(monkeypatch):
    from roma.serving.__main__ import main

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(SystemExit) as failure:
        main([])
    assert failure.value.code == 2


def test_local_service_defers_cache_only_model_warmup_to_lifespan(monkeypatch):
    from roma.core.speech_settings import SpeechSettings
    from roma.core.tts_settings import TTSSettings
    from roma.providers.ai.indic_tts import IndicTTSProvider
    from roma.providers.ai.whisper_mlx import WhisperMLXSTT
    from roma.serving.runtime import build_local_speech_app

    monkeypatch.setattr(
        "roma.serving.runtime.SpeechSettings", lambda: SpeechSettings(_env_file=None)
    )
    monkeypatch.setattr("roma.serving.runtime.TTSSettings", lambda: TTSSettings(_env_file=None))
    seen = []

    async def transcribe(self, request):
        seen.append(("asr", request.language_hint, len(request.audio)))
        return STTResult("", "hi")

    async def warmup(self, request):
        seen.append(("tts", request.language, request.voice))

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setattr(WhisperMLXSTT, "transcribe", transcribe)
    monkeypatch.setattr(IndicTTSProvider, "warmup", warmup)
    app = build_local_speech_app(
        SpeechServingSettings(
            _env_file=None, speech_service_key=SecretStr("speech-private-test-key-0123456789")
        )
    )
    assert seen == []

    async def run():
        async with app.router.lifespan_context(app):
            assert seen == [("asr", "hi", 32000), ("tts", "hi", "female")]

    asyncio.run(run())
