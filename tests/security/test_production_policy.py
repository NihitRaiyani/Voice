"""Offline validation of the explicit target; never opens a private dotenv."""

from pathlib import Path

import pytest


def test_secure_target_profile_is_explicit_and_cloud_substitution_is_rejected():
    from scripts.check_production_policy import read_profile, validate_profile

    root = Path(__file__).resolve().parents[2]
    profile = read_profile(root / "configs/production.local.env")
    assert validate_profile(profile) == []
    for key, value in (("LLM_PROVIDER", "openai"), ("VOICE_SPEECH_MODE", "legacy_cloud"), ("EMBEDDING_PROVIDER", "mock"), ("AUTH_ALLOW_LEGACY_API_TOKEN", "true"), ("HF_HUB_OFFLINE", "0")):
        assert validate_profile({**profile, key: value}), key
    assert "ANTHROPIC_API_KEY" not in profile


def test_target_validation_rejects_missing_fields_and_never_echoes_values(tmp_path):
    from scripts.check_production_policy import read_profile, validate_profile

    path = tmp_path / "target.env"
    path.write_text("LLM_PROVIDER=claude\nSPEECH_SERVICE_KEY=private-test-marker\n")
    errors = validate_profile(read_profile(path))
    assert errors
    assert "private-test-marker" not in repr(errors)
    path.write_text("LLM_PROVIDER=claude\nLLM_PROVIDER=openai\n")
    with pytest.raises(ValueError):
        read_profile(path)


def test_target_profile_never_returns_untrusted_field_names():
    from scripts.check_production_policy import validate_profile
    marker = "private" + "-field-marker"
    assert marker not in repr(validate_profile({marker: "anything"}))
