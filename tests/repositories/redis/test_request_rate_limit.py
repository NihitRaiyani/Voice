import asyncio

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.core.rate_limit import RequestRateLimitMiddleware
from roma.repositories.redis.request_rate_limit import RequestRateLimiter


def test_atomic_shared_limits_expire_and_hide_identity():
    async def run():
        redis = fakeredis.aioredis.FakeRedis()
        limiter = RequestRateLimiter(redis, b"r" * 32)
        results = await asyncio.gather(
            *(limiter.take("admin", "private-user", limit=3, window=300) for _ in range(8))
        )
        assert sum(results) == 3
        keys = await redis.keys("*")
        assert len(keys) == 1
        assert b"private-user" not in keys[0]
        assert 0 < await redis.ttl(keys[0]) <= 300
        assert await limiter.take("analytics", "private-user", limit=1, window=60)
        await redis.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("limit,window", [(0, 60), (True, 60), (1, 0), (1, True)])
def test_invalid_bounds(limit, window):
    async def run():
        with pytest.raises(ValueError):
            await RequestRateLimiter(None, b"r" * 32).take(
                "admin", "x", limit=limit, window=window
            )

    asyncio.run(run())


def test_middleware_limits_before_body_and_fails_closed():
    class Limiter:
        def __init__(self):
            self.calls = []

        async def take(self, category, identity, **policy):
            self.calls.append((category, identity, policy))
            return False

    app = FastAPI()
    app.state.request_rate_limiter = Limiter()
    app.add_middleware(RequestRateLimitMiddleware)

    @app.post("/api/v1/users")
    async def users():
        raise AssertionError("handler executed")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/users", content="private-body", headers={"x-forwarded-for": "spoof"}
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert app.state.request_rate_limiter.calls[0][0] == "administrative"
        assert app.state.request_rate_limiter.calls[0][1] == "peer:testclient"
    app.state.request_rate_limiter = None
    with TestClient(app) as client:
        assert client.post("/api/v1/users").status_code == 503


def test_callback_and_expensive_paths_and_health_exemption():
    class Limiter:
        def __init__(self):
            self.categories = []

        async def take(self, category, identity, **policy):
            self.categories.append(category)
            return True

    app = FastAPI()
    app.state.request_rate_limiter = Limiter()
    app.add_middleware(RequestRateLimitMiddleware)
    with TestClient(app) as client:
        client.post("/answer")
        client.post("/api/v1/ai-tests/run")
        client.get("/health")
        client.post("/api/v1/auth/login", content="malformed")
    assert app.state.request_rate_limiter.categories == ["callbacks", "ai_tests", "login"]


def test_authenticated_budget_uses_verified_user_across_tokens():
    from collections import Counter
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from roma.api.v1.auth import router
    from roma.api.v1.responses import ApiError, api_error_handler
    from roma.services.auth_service import Principal

    class Limiter:
        def __init__(self):
            self.counts = Counter()

        async def take(self, category, identity, *, limit, window):
            self.counts[(category, identity)] += 1
            return self.counts[(category, identity)] <= limit

    user = Principal(uuid4(), "person@example.invalid", "Synthetic", frozenset({"viewer"}))
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.state.auth_service = SimpleNamespace(authenticate=AsyncMock(return_value=user))
    app.state.request_rate_limiter = Limiter()
    app.include_router(router)
    app.add_middleware(RequestRateLimitMiddleware)
    with TestClient(app) as client:
        for index in range(100):
            response = client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer synthetic-{index}"}
            )
            assert response.status_code == 200
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer different-token", "x-user-id": "spoofed"},
        )
        assert response.status_code == 429
        assert str(user.user_id) not in response.text
    assert (
        app.state.request_rate_limiter.counts[("authenticated", "user:" + str(user.user_id))]
        == 101
    )


def test_redis_error_is_sanitized_and_never_reaches_handler():
    from redis.exceptions import ConnectionError

    class Limiter:
        async def take(self, *args, **kwargs):
            raise ConnectionError("private-redis-secret")

    app = FastAPI()
    app.state.request_rate_limiter = Limiter()
    app.add_middleware(RequestRateLimitMiddleware)

    @app.get("/api/v1/analytics/overview")
    async def analytics():
        raise AssertionError("handler executed")

    with TestClient(app) as client:
        response = client.get("/api/v1/analytics/overview")
        assert response.status_code == 503
        assert "private-redis-secret" not in response.text


def test_websocket_admission_is_limited_before_accept():
    from fastapi import WebSocket
    from starlette.websockets import WebSocketDisconnect

    class Limiter:
        def __init__(self):
            self.calls = []

        async def take(self, category, identity, **kwargs):
            self.calls.append((category, identity))
            return False

    app = FastAPI()
    app.state.request_rate_limiter = Limiter()
    app.add_middleware(RequestRateLimitMiddleware)

    @app.websocket("/ws")
    async def stream(socket: WebSocket):
        await socket.accept()
        raise AssertionError("unlimited handler reached")

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws"):
                pass
        assert error.value.code == 1013
    assert app.state.request_rate_limiter.calls == [("callbacks", "peer:testclient")]
