"""Claude configuration tests never read the developer's .env or cloud keys."""

import pytest
from pydantic import SecretStr, ValidationError
from roma.core.claude_llm_settings import ClaudeLLMSettings
from roma.providers.ai.claude_llm import ClaudeLLM
from roma.providers.ai.contracts import ProviderUnavailable


def settings(**values):
    return ClaudeLLMSettings(_env_file=None, llm_provider="claude", **values)


def test_claude_defaults_are_bounded_and_secret_is_hidden():
    cfg = settings(anthropic_api_key="offline-secret")
    assert isinstance(cfg.anthropic_api_key, SecretStr)
    assert "offline-secret" not in repr(cfg)
    assert cfg.anthropic_model == "claude-haiku-5-5"
    assert cfg.anthropic_max_output_tokens == 256
    assert cfg.anthropic_effort == "low"
    assert cfg.anthropic_max_prompt_bytes == 16384
    assert cfg.anthropic_attempt_timeout_secs == 4
    assert cfg.anthropic_turn_timeout_secs == 10
    assert cfg.anthropic_max_retries == 1
    assert cfg.anthropic_retry_backoff_secs == 0.25
    assert cfg.anthropic_call_token_budget == 20000
    assert cfg.anthropic_call_spend_cap_inr == 2
    assert cfg.anthropic_prompt_cache is True
    provider = ClaudeLLM(cfg)
    assert provider._client is None


def test_claude_requires_key_and_explicitly_priced_model_before_client_construction():
    with pytest.raises(ProviderUnavailable):
        ClaudeLLM(settings(anthropic_api_key=""))
    with pytest.raises(ProviderUnavailable):
        ClaudeLLM(settings(anthropic_api_key="offline", anthropic_model="unpriced-model"))


@pytest.mark.parametrize(
    "values",
    [
        {"anthropic_max_output_tokens": 0},
        {"anthropic_max_prompt_bytes": 65537},
        {"anthropic_effort": "high"},
        {"anthropic_max_retries": 5},
        {"anthropic_call_spend_cap_inr": float("inf")},
        {"anthropic_call_spend_cap_inr": 0},
        {"anthropic_call_token_budget": 0},
        {"anthropic_turn_timeout_secs": 0},
        {"anthropic_attempt_timeout_secs": float("nan")},
        {"anthropic_retry_backoff_secs": -1},
    ],
)
def test_claude_settings_reject_invalid_or_unbounded_limits(values):
    with pytest.raises(ValidationError):
        settings(**values)
