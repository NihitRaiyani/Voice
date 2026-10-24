from roma.repositories.postgres.models import Base


def test_auth_sessions_and_refresh_tokens_have_durable_revoke_and_rotation_identity():
    sessions = Base.metadata.tables["auth_sessions"]
    refresh = Base.metadata.tables["auth_refresh_tokens"]
    assert {"user_id", "expires_at", "revoked_at"} <= set(sessions.c.keys())
    assert {"session_id", "token_hash", "consumed_at", "expires_at"} <= set(refresh.c.keys())
    assert any(set(c.columns.keys()) == {"token_hash"} for c in refresh.constraints)
    assert next(iter(sessions.c.user_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(refresh.c.session_id.foreign_keys)).ondelete == "CASCADE"
