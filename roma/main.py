"""Production application composition root.

Only this module assembles concrete providers, repositories, routes, and the
realtime pipeline. Lower layers remain importable without starting the server.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from roma.api.v1.calls import mount_web_api
from roma.core.config import get_settings, require_reachable_base_url
from roma.core.database import Database
from roma.core.logging import configure_logging
from roma.realtime.pipeline import build_media_app
from roma.repositories.postgres.booking import PostgresBookingStore
from roma.repositories.postgres.safety import PostgresSafetyEventStore
from roma.repositories.postgres.webhooks import WebhookRepository
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds
from roma.services.booking_service import BookingService
from roma.services.booking_session import BookingSession
from roma.services.safety_service import UnavailableSafetyEventStore
from roma.services.webhook_service import WebhookService


def create_app() -> FastAPI:
    """Build the production backend application."""
    configure_logging()
    require_reachable_base_url()
    settings = get_settings()
    database = (
        Database.from_settings(settings) if settings.database_url.get_secret_value() else None
    )

    redis = Redis.from_url(settings.redis_url.get_secret_value())
    booking = BookingService(
        PostgresBookingStore(database.session_factory) if database else None,
        RedisAppointmentHolds(redis, ttl_seconds=settings.booking_hold_ttl_secs),
        db_timeout=settings.booking_db_timeout_secs,
        redis_timeout=settings.booking_redis_timeout_secs,
    )

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            try:
                await redis.aclose()
            finally:
                if database is not None:
                    await database.close()

    service = WebhookService(WebhookRepository(database.session_factory)) if database else None
    app = build_media_app(
        auto_hang_up=True,
        webhook_service=service,
        lifespan=lifespan,
        booking_factory=lambda state: BookingSession(booking, state),
        safety_store=PostgresSafetyEventStore(database.session_factory, source="voice")
        if database
        else UnavailableSafetyEventStore(),
    )
    app.state.booking_service = booking
    mount_web_api(app, session_factory=database.session_factory if database else None)
    return app


__all__ = ["create_app"]
