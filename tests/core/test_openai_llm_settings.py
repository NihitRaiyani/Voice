import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from roma.core.openai_llm_settings import OpenAILLMSettings
from roma.providers.ai import (
    MockLLMProvider,
    OpenAILLM,
    ProviderUnavailable,
    build_llm_provider,
)


def test_mock_defaults_construct_without_any_cloud_keys():
    s = OpenAILLMSettings(_env_file=None)
    assert s.llm_provider == "mock" and isinstance(build_llm_provider(s), MockLLMProvider)


def test_real_factory_requires_explicit_key_model_and_rejects_old_qwen_selector():
    with pytest.raises(ProviderUnavailable):
        build_llm_provider(OpenAILLMSettings(_env_file=None, llm_provider="openai"))
    with pytest.raises(ValidationError):
        OpenAILLMSettings(_env_file=None, llm_provider="qwen3_transformers")
    p = build_llm_provider(
        OpenAILLMSettings(
            _env_file=None,
            llm_provider="openai",
            openai_model="gpt-4o",
            openai_api_key="offline-key",
        )
    )
    assert isinstance(p, OpenAILLM) and p._client is None


@pytest.mark.parametrize(
    "values",
    [
        {"openai_max_output_tokens": 0},
        {"openai_temperature": float("nan")},
        {"openai_max_retries": 5},
        {"openai_call_spend_cap_inr": float("inf")},
        {"openai_turn_timeout_secs": 0},
    ],
)
def test_settings_reject_unbounded_limits(values):
    with pytest.raises(ValidationError):
        OpenAILLMSettings(_env_file=None, **values)


def test_console_refuses_implicit_paid_generation_before_any_client_is_constructed():
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            str(root / "scripts/text_console.py"),
            "--provider",
            "openai",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and "--allow-paid-api" in p.stderr


def test_mock_console_shows_the_requested_trace_without_paid_calls():
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [str(root / ".venv/bin/python"), str(root / "scripts/text_console.py")],
        cwd=root,
        input="haan\nMera naam Amit hai\n/quit\n",
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    for field in [
        "state_before",
        "extracted_slots",
        "prompt",
        "raw_model_output",
        "safety_result",
        "final_response",
    ]:
        assert field in p.stdout
    assert '"booking_committed": false' in p.stdout


def test_mock_lab_writes_blank_native_scores_and_owner_private_artifact(tmp_path):
    import json
    import stat

    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "review.json"
    p = subprocess.run(
        [
            str(root / ".venv/bin/python"),
            str(root / "scripts/openai_language_lab.py"),
            "--output",
            str(target),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(target.read_text())
    assert data["provider"] == "mock" and len(data["results"]) == 40
    assert all(row["correctness"] is None for row in data["results"])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
