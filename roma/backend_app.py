"""Explicit mock-only container API, independent of speech/carrier imports."""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from roma.api.v1.analytics import router as analytics_router
from roma.api.v1.auth import current_principal
from roma.api.v1.auth import router as auth_router
from roma.api.v1.costs import router as costs_router
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.api.v1.rest import router as rest_router
from roma.api.v1.supervisor import router as supervisor_router
from roma.core.auth_settings import AuthSettings
from roma.core.field_encryption import configure_field_encryption
from roma.core.logging import RequestLogMiddleware
from roma.core.privacy_settings import PrivacySettings
from roma.core.rate_limit import RequestRateLimitMiddleware
from roma.core.telemetry import get_telemetry, monitor_event_loop, sample_process_resources
from roma.repositories.redis.auth_rate_limit import AuthRateLimiter
from roma.repositories.redis.request_rate_limit import RequestRateLimiter
from roma.services.auth_service import AuthService
from roma.services.supervisor import SupervisorHub


def _scrape_token(path):
    with Path(path).open() as source:
        value = source.read(513).strip()
    if len(value) > 512:
        raise ValueError("Invalid scrape credential")
    return value


def create_app(*, session_factory=None, redis_client=None):
    """Container smoke/load profile only; never an alternate production voice factory."""
    if os.environ.get("ROMA_CONTAINER_MOCK") != "1" or any(
        os.environ.get(name, "mock") != "mock"
        for name in (
            "STT_PROVIDER",
            "LLM_PROVIDER",
            "TTS_PROVIDER",
            "EMBEDDING_PROVIDER",
            "TELEPHONY_PROVIDER",
        )
    ):
        raise ValueError("Explicit mock container profile required")
    auth_settings = AuthSettings(_env_file=None, auth_allow_legacy_api_token=False)
    key = auth_settings.auth_jwt_secret
    hash_key = os.environ.get("PII_HASH_KEY", "")
    if key is None or len(hash_key.encode()) < 32:
        raise ValueError("Private container authentication/hash keys required")
    configure_field_encryption(PrivacySettings(_env_file=None))
    engine = None
    if session_factory is None:
        pool_size = int(os.environ.get("DATABASE_POOL_SIZE", "2"))
        overflow = int(os.environ.get("DATABASE_MAX_OVERFLOW", "0"))
        budget = int(os.environ.get("DATABASE_CONNECTION_BUDGET", "10"))
        processes = int(os.environ.get("DATABASE_POOL_PROCESSES", "2"))
        if (
            not 1 <= pool_size <= budget <= 100
            or not 0 <= overflow <= budget - pool_size
            or not 1 <= processes <= 100
            or (pool_size + overflow) * processes > budget
        ):
            raise ValueError("Invalid database pool budget")
        engine = create_async_engine(
            os.environ["DATABASE_URL"],
            hide_parameters=True,
            pool_size=pool_size,
            max_overflow=overflow,
            pool_timeout=2,
            pool_pre_ping=True,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_client or Redis.from_url(
        os.environ["REDIS_URL"],
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    auth = AuthService(session_factory, auth_settings)
    supervisor = SupervisorHub(session_factory, redis)

    @asynccontextmanager
    async def lifespan(app):
        monitor = asyncio.create_task(monitor_event_loop())
        supervisor.start()
        try:
            yield
        finally:
            await supervisor.aclose()
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
            await auth.aclose()
            if redis_client is None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.supervisor_hub = supervisor
    app.state.session_factory = session_factory
    app.state.auth_settings, app.state.auth_service = auth_settings, auth
    app.state.auth_rate_limiter = AuthRateLimiter(
        redis,
        key.get_secret_value().encode(),
        limit=auth_settings.auth_rate_limit,
        window=auth_settings.auth_rate_window_secs,
    )
    app.state.request_rate_limiter = RequestRateLimiter(
        redis, sha256(hash_key.encode()).digest()
    )
    app.add_middleware(RequestRateLimitMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    for router in (auth_router, rest_router, costs_router, analytics_router, supervisor_router):
        app.include_router(router)

    @app.get("/health")
    async def health():
        return {"alive": True, "mode": "mock", "voice_enabled": False}

    @app.get("/ready")
    async def ready():
        try:
            async with asyncio.timeout(2):
                async with session_factory() as session:
                    await session.execute(text("SELECT 1"))
                    head = await session.scalar(text("SELECT version_num FROM alembic_version"))
                if head != "20261026_0007" or not await redis.ping():
                    raise ValueError()
            return {"ready": True, "mode": "mock"}
        except Exception:  # noqa: BLE001 — dependency identities/credentials stay private.
            return JSONResponse({"ready": False, "mode": "mock"}, status_code=503)

    @app.get("/metrics")
    async def metrics(request: Request):
        allowed = request.client is not None and request.client.host in {"127.0.0.1", "::1"}
        path = os.environ.get("METRICS_SCRAPE_TOKEN_FILE")
        if not allowed and path:
            try:
                token = await asyncio.to_thread(_scrape_token, path)
                allowed = len(token) >= 32 and secrets.compare_digest(
                    request.headers.get("authorization", "").encode(),
                    ("Bearer " + token).encode(),
                )
            except (OSError, ValueError):
                allowed = False
        if not allowed:
            principal = await current_principal(request)
            if not principal.is_active or "admin" not in principal.roles:
                raise ApiError(403, "FORBIDDEN", "Administrator access required.")
        sample_process_resources()
        return PlainTextResponse(
            get_telemetry().render(),
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        )

    return app
