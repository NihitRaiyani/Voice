import pytest
from pydantic import ValidationError
from roma.core.speech_settings import SpeechSettings
from roma.providers.ai import build_stt_provider


def test_settings_are_mock_by_default_and_build_lazily():
    cfg = SpeechSettings(_env_file=None)
    assert cfg.stt_provider == "mock"
    for name in ("indic_conformer", "whisper"):
        cfg.stt_provider = name
        assert callable(build_stt_provider(cfg).transcribe)


@pytest.mark.parametrize(
    "key", ["HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_API_KEY", "HUGGING_FACE_API_KEY"]
)
def test_token_alias_is_env_only_and_redacted(monkeypatch, key):
    for alias in (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HUGGINGFACE_API_KEY",
        "HUGGING_FACE_API_KEY",
    ):
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(key, "test-private-token")
    cfg = SpeechSettings(_env_file=None)
    assert cfg.hf_token.get_secret_value() == "test-private-token"
    assert "test-private-token" not in repr(cfg)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stt_timeout_secs": float("nan")},
        {"stt_timeout_secs": 0},
        {"stt_max_audio_secs": 31},
        {"indic_model_revision": "main"},
        {"stt_language": "xyz"},
        {"hf_whisper_api_name": "../../secret"},
    ],
)
def test_invalid_speech_configuration_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        SpeechSettings(_env_file=None, **kwargs)


def test_hosted_factory_requires_explicit_endpoint_and_token():
    cfg = SpeechSettings(_env_file=None, stt_provider="hf_indic_conformer", hf_token="")
    with pytest.raises((ValueError, RuntimeError)):
        build_stt_provider(cfg)


def test_public_lab_factory_has_discovered_defaults_and_never_needs_a_token():
    cfg = SpeechSettings(_env_file=None, stt_provider="hf_public_lab", hf_token="")
    provider = build_stt_provider(cfg)
    assert callable(provider.transcribe)
    assert cfg.hf_indic_space_url == "https://1219naveen-indicconformer-asr.hf.space"
    assert cfg.hf_whisper_space_url == "https://hf-audio-whisper-large-v3.hf.space"
    assert cfg.hf_whisper_api_name == "transcribe"
    assert provider._whisper.api_name == "transcribe"


@pytest.mark.parametrize("timeout", [0, 121, float("nan"), float("inf")])
def test_public_lab_deadline_is_finite_and_bounded(timeout):
    with pytest.raises(ValidationError):
        SpeechSettings(_env_file=None, hf_public_timeout_secs=timeout)


def test_public_lab_factory_propagates_selected_whisper_api():
    cfg = SpeechSettings(
        _env_file=None,
        stt_provider="hf_public_lab",
        hf_whisper_space_url="https://hf-audio-whisper-large-v3.hf.space",
        hf_whisper_api_name="transcribe",
    )
    assert build_stt_provider(cfg)._whisper.api_name == "transcribe"


def test_whisper_authentication_requires_explicit_opt_in_even_with_key():
    cfg = SpeechSettings(_env_file=None, hf_token="private-test-key")
    assert cfg.hf_whisper_space_authenticated is False


def test_whisper_official_authentication_is_propagated_without_changing_indic():
    cfg = SpeechSettings(
        _env_file=None,
        stt_provider="hf_public_lab",
        hf_token="private-test-key",
        hf_whisper_space_authenticated=True,
    )
    provider = build_stt_provider(cfg)
    assert provider._whisper._auth_headers == {"X-HF-Authorization": "Bearer private-test-key"}
    assert not getattr(provider._indic, "_auth_headers", {})
