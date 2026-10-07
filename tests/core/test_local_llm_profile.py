import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from roma.core.local_llm_config import load_local_llm_settings

ROOT = Path(__file__).resolve().parents[2]


def test_mac_profile_selects_trained_model_despite_makefile_mock_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = load_local_llm_settings(ROOT / "configs/local-llm-mac.json")
    assert settings.llm_provider == "qwen3_mlx"
    assert settings.local_llm_model == "mlx-community/Qwen3-1.7B-4bit"
    assert len(settings.local_llm_revision) == 40
    assert settings.local_llm_device == "mps"
    assert settings.local_llm_dtype == "int4"
    assert settings.local_llm_local_files_only
    assert (
        load_local_llm_settings(
            ROOT / "configs/local-llm-mac.json", provider="mock"
        ).llm_provider
        == "mock"
    )


def test_profile_keeps_hf_credentials_in_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "synthetic-secret")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"local_llm_model": "Qwen/Qwen3-0.6B"}))
    settings = load_local_llm_settings(profile)
    assert settings.hf_token.get_secret_value() == "synthetic-secret"
    assert "synthetic-secret" not in repr(settings)
    profile.write_text(json.dumps({"hf_token": "wrong-place"}))
    with pytest.raises(ValueError, match="non-secret"):
        load_local_llm_settings(profile)


def test_profile_values_are_validated(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"local_llm_dtype": "invalid"}))
    with pytest.raises(ValidationError):
        load_local_llm_settings(profile)
