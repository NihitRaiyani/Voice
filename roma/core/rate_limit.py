"""Bound request admission before parsing bodies; trusted user limits after auth."""

from redis.exceptions import RedisError

from roma.api.v1.responses import ApiError, error_response
from roma.core.logging import LogEvent, log_event

# Category separation keeps callback bursts from consuming user/login budgets.
POLICIES = {
    "login": (10, 300),
    "administrative": (30, 60),
    "analytics": (60, 60),
    "callbacks": (120, 60),
    "ai_tests": (5, 60),
    "api_peer": (1000, 60),
    "authenticated": (100, 60),
}


def category_for(path: str) -> str | None:
    if path in {"/api/v1/auth/login", "/api/v1/auth/refresh"}:
        return "login"
    if path == "/answer" or path.startswith("/callbacks/"):
        return "callbacks"
    if path.startswith(("/api/v1/ai-tests", "/api/v1/labs")):
        return "ai_tests"
    if (
        path.startswith(("/api/v1/users", "/api/v1/knowledge/documents", "/api/v1/privacy"))
        or path == "/api/call"
    ):
        return "administrative"
    if path.startswith(("/api/v1/analytics", "/api/v1/safety/stats")):
        return "analytics"
    if path.startswith(("/api/v1/", "/api/call/")):
        return "api_peer"
    return None


async def admit(limiter, category: str, identity: str) -> bool:
    limit, window = POLICIES[category]
    return await limiter.take(category, identity, limit=limit, window=window)


async def limit_authenticated(request, identity: str) -> None:
    limiter = getattr(request.app.state, "request_rate_limiter", None)
    # Independently mounted test/embedding routers may supply their own admission.
    # Production composition always installs middleware + the shared limiter.
    if limiter is None:
        return
    try:
        allowed = await admit(limiter, "authenticated", identity)
    except (RedisError, OSError, TimeoutError, ValueError):
        raise ApiError(
            503, "RATE_LIMIT_UNAVAILABLE", "Request admission is unavailable."
        ) from None
    if not allowed:
        log_event(LogEvent.RATE_LIMITED, status_code=429)
        raise ApiError(429, "RATE_LIMITED", "Too many authenticated requests.")


class RequestRateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        category = category_for(scope.get("path", "")) if scope["type"] == "http" else None
        if scope["type"] == "websocket" and scope.get("path") == "/ws":
            category = "callbacks"
        if category is None:
            return await self.app(scope, receive, send)
        app = scope.get("app")
        limiter = getattr(getattr(app, "state", None), "request_rate_limiter", None)
        allowed = None
        if limiter is not None:
            peer = scope.get("client")
            # Do not trust forwarded headers or caller-provided principal IDs.
            identity = "peer:" + (str(peer[0]) if peer else "unknown")
            try:
                allowed = await admit(limiter, category, identity)
            except (RedisError, OSError, TimeoutError, ValueError):
                pass
        if allowed is True:
            return await self.app(scope, receive, send)
        log_event(LogEvent.RATE_LIMITED, status_code=429 if allowed is False else 503)
        if scope["type"] == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": 1013,
                    "reason": "Request admission unavailable.",
                }
            )
            return
        response = error_response(
            429 if allowed is False else 503,
            "RATE_LIMITED" if allowed is False else "RATE_LIMIT_UNAVAILABLE",
            "Too many requests." if allowed is False else "Request admission is unavailable.",
        )
        response.headers["Cache-Control"] = "no-store"
        if allowed is False:
            response.headers["Retry-After"] = str(POLICIES[category][1])
        await response(scope, receive, send)
