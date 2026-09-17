from pathlib import Path

import pytest
from pydantic import ValidationError

from roma.config import Settings

REQUIRED = [
    "VOBIZ_FROM_NUMBER",
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
    "REDIS_URL",
]

# Needed ONLY to place an outbound call through the REST Call API. Answering, streaming and
# the whole conversation need none of them: Vobiz authenticates its own SIP trunk and
# connects to us. Requiring them would stop the media server booting for want of a credential
# it never uses, and would block the entire offline verification path.
OPTIONAL_API_CREDENTIALS = ["VOBIZ_AUTH_ID", "VOBIZ_AUTH_TOKEN"]


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
    repo_root = Path(__file__).resolve().parents[1]
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


@pytest.mark.parametrize("key", OPTIONAL_API_CREDENTIALS)
def test_the_call_api_credentials_are_optional_at_startup(monkeypatch, key):
    """The media server must boot without them. Vobiz authenticates its own SIP trunk and
    connects to us, so answering, streaming and the whole conversation work with these blank
    — only outbound dialing does not."""
    _set_full_env(monkeypatch)
    monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.vobiz_auth_id.get_secret_value() == ""


def test_dialing_without_the_call_api_credentials_fails_with_a_useful_message(monkeypatch):
    """Optional at startup is only safe if the failure at the point of use names the fix. A
    401 from the REST call would mention neither variable."""
    from roma.config import Settings, get_settings
    from roma.telephony.dialer import build_vobiz_client

    _set_full_env(monkeypatch)
    monkeypatch.delenv("VOBIZ_AUTH_ID", raising=False)
    monkeypatch.delenv("VOBIZ_AUTH_TOKEN", raising=False)

    # `_env_file=None` is load-bearing, not tidiness. `Settings` reads `.env`, so deleting the
    # variables from the environment does NOT make them absent — the file still supplies them.
    # This test passed for weeks only because `.env` happened to have them blank; the day real
    # credentials landed it started asserting nothing and failed. A test about missing
    # credentials must not depend on the developer's own machine not having any.
    monkeypatch.setattr("roma.config.Settings", lambda **kw: Settings(_env_file=None, **kw))
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="VOBIZ_AUTH_ID"):
            build_vobiz_client()
    finally:
        get_settings.cache_clear()


# --- the base URL a carrier has to reach --------------------------------------------------


@pytest.mark.parametrize(
    "base", ["https://localhost:8020", "https://127.0.0.1:8020", "http://localhost"]
)
def test_an_unreachable_base_url_is_refused(monkeypatch, base):
    """`/answer` mints `wss://<PUBLIC_BASE_URL>/ws` and hands it to Vobiz, which dials it from
    ITS network. Left on the config default the XML is served happily with a clean 200, Vobiz
    connects to its own loopback, and the call rings and goes nowhere — with nothing in our
    log saying why, because from this side it answered correctly. Cheaper to refuse at boot
    than to spend one of ~1.5 remaining calls discovering it."""
    from roma.config import get_settings, require_reachable_base_url

    _set_full_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
            require_reachable_base_url()
    finally:
        get_settings.cache_clear()


def test_a_real_public_host_is_accepted(monkeypatch):
    from roma.config import get_settings, require_reachable_base_url

    _set_full_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sure-temperatures.trycloudflare.com")
    get_settings.cache_clear()
    try:
        require_reachable_base_url()
    finally:
        get_settings.cache_clear()
