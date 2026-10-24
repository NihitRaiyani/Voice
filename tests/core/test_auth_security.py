from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr, ValidationError
from roma.core.auth_settings import AuthSettings
from roma.core.security import AccessTokens, Passwords


def settings():
    return AuthSettings(_env_file=None, auth_jwt_secret=SecretStr("test-only-signing-key-" * 3))


def test_passwords_are_salted_argon2id_and_wrong_or_malformed_hashes_fail():
    passwords = Passwords()
    first = passwords.hash("synthetic password 123!")
    second = passwords.hash("synthetic password 123!")
    assert first.startswith("$argon2id$") and first != second
    assert passwords.verify(first, "synthetic password 123!")
    assert not passwords.verify(first, "incorrect password")
    assert not passwords.verify("legacy-invalid-hash", "synthetic password 123!")


def test_access_tokens_require_fixed_algorithm_identity_and_expiry():
    cfg = settings()
    tokens = AccessTokens(cfg)
    token = tokens.issue(uuid4(), uuid4())
    claims = tokens.decode(token)
    assert {"sub", "sid", "jti", "iat", "nbf", "exp", "iss", "aud"} <= claims.keys()
    key = cfg.auth_jwt_secret.get_secret_value()
    for override in [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"exp": 1},
        {"sub": 123},
        {"sid": "bad"},
    ]:
        forged = jwt.encode({**claims, **override}, key, algorithm="HS256")
        with pytest.raises(ValueError):
            tokens.decode(forged)
    with pytest.raises(ValueError):
        tokens.decode(jwt.encode(claims, key, algorithm="HS384"))
    with pytest.raises(ValueError):
        tokens.decode(jwt.encode(claims, key="", algorithm="none"))


@pytest.mark.parametrize("secret", ["", "short"])
def test_auth_has_no_default_signing_secret(secret):
    with pytest.raises((ValueError, ValidationError)):
        AccessTokens(AuthSettings(_env_file=None, auth_jwt_secret=SecretStr(secret)))


def test_access_tokens_reject_wrong_type_header():
    cfg = settings()
    tokens = AccessTokens(cfg)
    claims = tokens.decode(tokens.issue(uuid4(), uuid4()))
    wrong_type = jwt.encode(
        claims,
        cfg.auth_jwt_secret.get_secret_value(),
        algorithm="HS256",
        headers={"typ": "refresh"},
    )
    with pytest.raises(ValueError):
        tokens.decode(wrong_type)


def test_all_roles_follow_the_exact_permission_matrix():
    from roma.services.auth_service import Principal

    expected = {
        "viewer": {"calls.view", "analytics.view", "safety.view"},
        "counsellor": {
            "calls.view",
            "analytics.view",
            "safety.view",
            "recordings.view",
            "appointments.view",
            "appointments.manage",
            "leads.view",
            "rag.debug",
        },
        "admin": {
            "calls.view",
            "calls.create",
            "analytics.view",
            "safety.view",
            "recordings.view",
            "appointments.view",
            "appointments.manage",
            "leads.view",
            "rag.debug",
            "rag.upload",
            "rag.approve",
            "safety.manage",
            "users.manage",
        },
    }
    for role, permissions in expected.items():
        assert (
            Principal(
                uuid4(), "test@example.invalid", "Synthetic", frozenset({role})
            ).permissions
            == permissions
        )
    assert not Principal(
        uuid4(), "test@example.invalid", "Synthetic", frozenset({"unknown"})
    ).permissions


def test_invalid_unicode_password_is_rejected_without_a_runtime_failure():
    passwords = Passwords()
    encoded = passwords.hash('synthetic password 123!')
    assert not passwords.verify(encoded, '\ud800' * 12)


def test_blank_auth_secret_from_environment_disables_human_auth(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "")
    cfg = AuthSettings(_env_file=None)
    assert cfg.auth_jwt_secret is None
    with pytest.raises(ValueError, match="not configured"):
        AccessTokens(cfg)
