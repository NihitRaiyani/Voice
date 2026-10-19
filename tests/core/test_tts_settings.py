import asyncio

import pytest
from pydantic import ValidationError
from roma.core.tts_settings import TTSSettings
from roma.providers.ai.contracts import ProviderUnavailable, TTSRequest
from roma.providers.ai.indic_tts import IndicTTSProvider
from roma.providers.ai.registry import build_tts_provider


def test_tts_config_defaults_are_offline_and_local_construction_is_lazy():
    cfg = TTSSettings(_env_file=None)
    assert cfg.tts_provider == "mock" and cfg.indic_tts_manifest == cfg.indic_tts_python == ""
    local = build_tts_provider(TTSSettings(_env_file=None, tts_provider="indic_tts"))
    assert isinstance(local, IndicTTSProvider)

    async def run():
        with pytest.raises(ProviderUnavailable):
            await local.synthesize(TTSRequest("नमस्ते"))

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("tts_timeout_secs", float("inf")),
        ("tts_timeout_secs", 181),
        ("tts_max_audio_secs", 61),
        ("tts_max_concurrency", 0),
        ("tts_cache_bytes", -1),
        ("tts_voice", "unknown"),
    ],
)
def test_tts_settings_reject_unbounded_or_unknown_values(field, value):
    with pytest.raises(ValidationError):
        TTSSettings(_env_file=None, **{field: value})
