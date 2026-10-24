import pytest
from pydantic import SecretStr, ValidationError
from roma.core.field_encryption import FieldCipher, PrivacyError
from roma.core.privacy_settings import PrivacySettings


def test_encrypted_fields_are_authenticated_and_configuration_fails_closed():
    settings = PrivacySettings(
        _env_file=None, pii_encryption_enabled=True, pii_encryption_key=SecretStr("11" * 32)
    )
    cipher = FieldCipher(settings)
    encrypted = cipher.encrypt("Synthetic Caller", context="callers.name")
    assert "Synthetic Caller" not in encrypted
    assert cipher.decrypt(encrypted, context="callers.name") == "Synthetic Caller"
    with pytest.raises(PrivacyError):
        cipher.decrypt(encrypted, context="call_turns.transcript")
    with pytest.raises(PrivacyError):
        FieldCipher(PrivacySettings(_env_file=None, pii_encryption_enabled=True))


@pytest.mark.parametrize("signing_key", ["aa" * 32, "AA" * 32, " AA \t" * 32])
def test_privacy_keys_must_have_distinct_decoded_bytes(signing_key):
    with pytest.raises(ValidationError, match="must be distinct"):
        PrivacySettings(
            _env_file=None,
            pii_encryption_enabled=True,
            pii_encryption_key=SecretStr("aa" * 32),
            recording_url_signing_key=SecretStr(signing_key),
        )


@pytest.mark.parametrize("field", ["pii_encryption_key", "recording_url_signing_key"])
@pytest.mark.parametrize("invalid_key", ["not-a-hex-private-key", "ab" * 31, "ab" * 33])
def test_invalid_privacy_keys_fail_with_sanitized_configuration_error(field, invalid_key):
    keys = {
        "pii_encryption_key": SecretStr("aa" * 32),
        "recording_url_signing_key": SecretStr("bb" * 32),
    }
    keys[field] = SecretStr(invalid_key)
    with pytest.raises(ValidationError, match="32-byte hexadecimal privacy keys") as error:
        PrivacySettings(_env_file=None, pii_encryption_enabled=True, **keys)
    assert invalid_key not in str(error.value)
    assert "aa" * 32 not in str(error.value)
    assert "bb" * 32 not in str(error.value)


def test_distinct_privacy_keys_accept_hex_case_and_whitespace():
    settings = PrivacySettings(
        _env_file=None,
        pii_encryption_enabled=True,
        pii_encryption_key=SecretStr(" AA \t" * 32),
        recording_url_signing_key=SecretStr("BB" * 32),
    )
    cipher = FieldCipher(settings)
    assert (
        cipher.decrypt(
            cipher.encrypt("Synthetic Caller", context="callers.name"), context="callers.name"
        )
        == "Synthetic Caller"
    )


@pytest.mark.parametrize("key", ["aa" * 32, "not-a-hex-private-key"])
def test_disabled_field_encryption_does_not_require_distinct_valid_keys(key):
    settings = PrivacySettings(
        _env_file=None,
        pii_encryption_enabled=False,
        pii_encryption_key=SecretStr(key),
        recording_url_signing_key=SecretStr(key),
    )
    assert FieldCipher(settings).encrypt("Synthetic Caller", context="callers.name") == (
        "Synthetic Caller"
    )


def test_privacy_defaults_remain_disabled_without_generated_keys(monkeypatch):
    for name in ("PII_ENCRYPTION_ENABLED", "PII_ENCRYPTION_KEY", "RECORDING_URL_SIGNING_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = PrivacySettings(_env_file=None)
    assert settings.pii_encryption_enabled is False
    assert settings.pii_encryption_key.get_secret_value() == ""
    assert settings.recording_url_signing_key.get_secret_value() == ""
