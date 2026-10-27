import pytest
from pydantic import ValidationError
from roma.core.config import Settings


def test_local_voice_profile_requires_only_claude_and_explicit_local_speech_identity():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_url='redis://localhost/0', voice_speech_mode='local_http')
    settings = Settings(_env_file=None, redis_url='redis://localhost/0', voice_speech_mode='local_http',
        llm_provider='claude',anthropic_api_key='synthetic-key',stt_provider='whisper_mlx',tts_provider='indic_tts',
        speech_service_key='synthetic-local-speech-key-'*2)
    assert settings.sarvam_api_key.get_secret_value() == ''
    assert settings.voice_speech_mode == 'local_http'


def test_legacy_voice_profile_keeps_existing_required_sarvam_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, redis_url='redis://localhost/0')
