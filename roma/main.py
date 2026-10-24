"""Production application composition root.

Only this module assembles concrete providers, repositories, routes, and the
realtime pipeline. Lower layers remain importable without starting the server.
"""

from contextlib import asynccontextmanager
from hashlib import sha256

from fastapi import FastAPI
from redis.asyncio import Redis

from roma.api.v1.calls import mount_web_api
from roma.core.auth_settings import AuthSettings
from roma.core.config import get_settings, require_reachable_base_url
from roma.core.database import Database
from roma.core.logging import RequestLogMiddleware, configure_logging
from roma.core.rate_limit import RequestRateLimitMiddleware
from roma.core.tokenizer import require_nltk_data
from roma.realtime.pipeline import build_media_app
from roma.repositories.postgres.booking import PostgresBookingStore
from roma.repositories.postgres.conversation_state import PostgresConversationStateStore
from roma.repositories.postgres.safety import PostgresSafetyEventStore
from roma.repositories.postgres.webhooks import WebhookRepository
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds
from roma.repositories.redis.auth_rate_limit import AuthRateLimiter
from roma.repositories.redis.call_lease import RedisCallLease
from roma.repositories.redis.conversation_state import RedisCallStateStore
from roma.repositories.redis.request_rate_limit import RequestRateLimiter
from roma.repositories.redis.webhook_window import RedisWebhookWindow
from roma.services.auth_service import AuthService
from roma.services.booking_service import BookingService
from roma.services.booking_session import BookingSession
from roma.services.live_call_state import LiveCallStateStore
from roma.services.safety_service import UnavailableSafetyEventStore
from roma.services.webhook_service import WebhookService


def create_app() -> FastAPI:
    """Build the production backend application."""
    require_nltk_data()
    configure_logging()
    require_reachable_base_url()
    settings = get_settings()
    database = (
        Database.from_settings(settings) if settings.database_url.get_secret_value() else None
    )

    redis = Redis.from_url(settings.redis_url.get_secret_value())
    auth_settings = AuthSettings()
    auth_secret = auth_settings.auth_jwt_secret
    auth = (
        AuthService(database.session_factory, auth_settings)
        if database is not None and auth_secret is not None
        else None
    )
    auth_limiter = (
        AuthRateLimiter(
            redis,
            auth_secret.get_secret_value().encode("utf-8"),
            limit=auth_settings.auth_rate_limit,
            window=auth_settings.auth_rate_window_secs,
        )
        if auth is not None and auth_secret is not None
        else None
    )

    booking = BookingService(
        PostgresBookingStore(database.session_factory) if database else None,
        RedisAppointmentHolds(redis, ttl_seconds=settings.booking_hold_ttl_secs),
        db_timeout=settings.booking_db_timeout_secs,
        redis_timeout=settings.booking_redis_timeout_secs,
    )

    def build_call_store(call_settings):
        hot = RedisCallStateStore(call_settings.redis_url.get_secret_value())
        if database is None:
            return hot
        return LiveCallStateStore(
            hot,
            PostgresConversationStateStore(database.session_factory),
            lease_factory=lambda sid: RedisCallLease(redis, sid),
        )

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            try:
                if auth is not None:
                    await auth.aclose()
            finally:
                try:
                    await redis.aclose()
                finally:
                    if database is not None:
                        await database.close()

    service = (
        WebhookService(
            WebhookRepository(database.session_factory), window=RedisWebhookWindow(redis)
        )
        if database
        else None
    )
    app = build_media_app(
        auto_hang_up=True,
        build_store_fn=build_call_store,
        webhook_service=service,
        lifespan=lifespan,
        booking_factory=lambda state: BookingSession(booking, state),
        safety_store=PostgresSafetyEventStore(database.session_factory, source="voice")
        if database
        else UnavailableSafetyEventStore(),
    )
    rate_secret = settings.pii_hash_key.get_secret_value().encode()
    app.state.request_rate_limiter = (
        RequestRateLimiter(redis, sha256(b"roma-rate-limit-v1:" + rate_secret).digest())
        if len(rate_secret) >= 32
        else None
    )
    app.add_middleware(RequestRateLimitMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.state.booking_service = booking
    app.state.auth_settings = auth_settings
    app.state.auth_service = auth
    app.state.auth_rate_limiter = auth_limiter
    mount_web_api(app, session_factory=database.session_factory if database else None)
    return app


__all__ = ["create_app"]
