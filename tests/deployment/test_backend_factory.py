from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


def test_container_backend_refuses_nonmock_or_unapproved_factory(monkeypatch):
    from roma.backend_app import create_app

    monkeypatch.delenv("ROMA_CONTAINER_MOCK", raising=False)
    with pytest.raises(ValueError, match="mock"):
        create_app()
    monkeypatch.setenv("ROMA_CONTAINER_MOCK", "1")
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    with pytest.raises(ValueError, match="mock"):
        create_app()


def test_mock_backend_health_and_readiness_use_dependency_contracts(monkeypatch):
    from roma.backend_app import create_app

    monkeypatch.setenv("ROMA_CONTAINER_MOCK", "1")
    for name in (
        "STT_PROVIDER",
        "LLM_PROVIDER",
        "TTS_PROVIDER",
        "EMBEDDING_PROVIDER",
        "TELEPHONY_PROVIDER",
    ):
        monkeypatch.setenv(name, "mock")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-container-secret-" + "a" * 32)
    monkeypatch.setenv("PII_HASH_KEY", "test-only-container-hash-" + "b" * 32)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args):
            return None

        async def scalar(self, *args):
            return "20261026_0007"

    redis = type("Redis", (), {"ping": AsyncMock(return_value=True), "aclose": AsyncMock()})()
    app = create_app(session_factory=Session, redis_client=redis)
    app.state.request_rate_limiter = type(
        "Limiter", (), {"take": AsyncMock(return_value=True)}
    )()
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "mock"
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/analytics/overview").status_code in {401, 503}
        assert client.post("/api/call", json={}).status_code == 404
        redis.ping.side_effect = OSError("private")
        result = client.get("/ready")
        assert result.status_code == 503 and "private" not in result.text


def test_mock_backend_honors_explicit_database_pool_limits(monkeypatch):
    import roma.backend_app as backend

    monkeypatch.setenv("ROMA_CONTAINER_MOCK", "1")
    for name in (
        "STT_PROVIDER",
        "LLM_PROVIDER",
        "TTS_PROVIDER",
        "EMBEDDING_PROVIDER",
        "TELEPHONY_PROVIDER",
    ):
        monkeypatch.setenv(name, "mock")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-container-secret-" + "a" * 32)
    monkeypatch.setenv("PII_HASH_KEY", "test-only-container-hash-" + "b" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://fixture@localhost/fixture")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "2")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")

    def capture(url, **kwargs):
        assert kwargs["pool_size"] == 2 and kwargs["max_overflow"] == 0
        raise RuntimeError("pool inspected without connecting")

    monkeypatch.setattr(backend, "create_async_engine", capture)
    with pytest.raises(RuntimeError, match="pool inspected"):
        backend.create_app()


def test_mock_backend_rejects_combined_process_pool_over_budget(monkeypatch):
    from roma.backend_app import create_app

    monkeypatch.setenv("ROMA_CONTAINER_MOCK", "1")
    for name in (
        "STT_PROVIDER",
        "LLM_PROVIDER",
        "TTS_PROVIDER",
        "EMBEDDING_PROVIDER",
        "TELEPHONY_PROVIDER",
    ):
        monkeypatch.setenv(name, "mock")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-only-container-secret-" + "a" * 32)
    monkeypatch.setenv("PII_HASH_KEY", "test-only-container-hash-" + "b" * 32)
    monkeypatch.setenv("DATABASE_POOL_SIZE", "6")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DATABASE_POOL_PROCESSES", "2")
    monkeypatch.setenv("DATABASE_CONNECTION_BUDGET", "10")
    with pytest.raises(ValueError, match="pool budget"):
        create_app()
