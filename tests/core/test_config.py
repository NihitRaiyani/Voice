from pathlib import Path

import pytest
from pydantic import ValidationError
from roma.core.config import Settings

REQUIRED = [
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
    "REDIS_URL",
]

# Needed only for carrier operations. Requiring them would stop the media server booting and
# block the offline verification path when no outbound call is being placed.
OPTIONAL_TWILIO_CREDENTIALS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
]


def _set_full_env(monkeypatch):
    """Populate every required secret with a dummy value."""
    for key in REQUIRED:
        monkeypatch.setenv(key, f"test-{key.lower()}")


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_key_fails_fast(monkeypatch, missing):
    _set_full_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_all_required_present_constructs(monkeypatch):
    _set_full_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.openai_api_key.get_secret_value() == "test-openai_api_key"
    assert settings.app_env == "dev"


def test_secret_never_appears_in_repr(monkeypatch):
    secret = "super-secret-openai-value"
    _set_full_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    settings = Settings(_env_file=None)

    assert secret not in repr(settings)
    assert secret not in str(settings.openai_api_key)
    assert settings.openai_api_key.get_secret_value() == secret


def test_env_example_matches_settings_fields():
    repo_root = Path(__file__).resolve().parents[2]
    example = repo_root / ".env.example"
    keys_in_example = set()
    for raw in example.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys_in_example.add(line.split("=", 1)[0].strip())

    expected = {name.upper() for name in Settings.model_fields}
    assert keys_in_example == expected


@pytest.mark.parametrize("blank", REQUIRED)
def test_blank_required_key_fails_fast(monkeypatch, blank):
    _set_full_env(monkeypatch)
    monkeypatch.setenv(blank, "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("key", OPTIONAL_TWILIO_CREDENTIALS)
def test_twilio_credentials_are_optional_at_server_startup(monkeypatch, key):
    _set_full_env(monkeypatch)
    monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert getattr(settings, key.lower()).get_secret_value() == ""


def test_dialing_without_twilio_credentials_fails_with_a_useful_message(monkeypatch):
    from roma.core.config import Settings, get_settings
    from roma.providers.telephony.twilio.client import build_twilio_client

    _set_full_env(monkeypatch)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)

    # `_env_file=None` is load-bearing, not tidiness. `Settings` reads `.env`, so deleting the
    # variables from the environment does NOT make them absent — the file still supplies them.
    # This test passed for weeks only because `.env` happened to have them blank; the day real
    # credentials landed it started asserting nothing and failed. A test about missing
    # credentials must not depend on the developer's own machine not having any.
    monkeypatch.setattr("roma.core.config.Settings", lambda **kw: Settings(_env_file=None, **kw))
    get_settings.cache_clear()
    try:
        with pytest.raises(
            RuntimeError,
            match=r"TWILIO_ACCOUNT_SID.*TWILIO_AUTH_TOKEN.*TWILIO_FROM_NUMBER",
        ):
            build_twilio_client()
    finally:
        get_settings.cache_clear()


def test_twilio_client_uses_the_bounded_dial_timeout(monkeypatch):
    from roma.core.config import Settings, get_settings
    from roma.providers.telephony.twilio.client import DIAL_TIMEOUT_SECS, build_twilio_client

    _set_full_env(monkeypatch)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "test-twilio-account-sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+919876543210")
    monkeypatch.setattr("roma.core.config.Settings", lambda **kw: Settings(_env_file=None, **kw))
    get_settings.cache_clear()
    try:
        client = build_twilio_client()
        assert client.http_client.timeout == DIAL_TIMEOUT_SECS
    finally:
        get_settings.cache_clear()


# --- the base URL a carrier has to reach --------------------------------------------------


@pytest.mark.parametrize(
    "base", ["https://localhost:8020", "https://127.0.0.1:8020", "http://localhost"]
)
def test_an_unreachable_base_url_is_refused(monkeypatch, base):
    """Twilio must be able to reach both `/answer` and `/ws` from its own network."""
    from roma.core.config import get_settings, require_reachable_base_url

    _set_full_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
            require_reachable_base_url()
    finally:
        get_settings.cache_clear()


def test_a_real_public_host_is_accepted(monkeypatch):
    from roma.core.config import get_settings, require_reachable_base_url

    _set_full_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sure-temperatures.trycloudflare.com")
    get_settings.cache_clear()
    try:
        require_reachable_base_url()
    finally:
        get_settings.cache_clear()
