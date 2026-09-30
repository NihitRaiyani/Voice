"""Production application composition root.

Only this module assembles concrete providers, repositories, routes, and the
realtime pipeline. Lower layers remain importable without starting the server.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from roma.api.v1.calls import mount_web_api
from roma.core.config import get_settings, require_reachable_base_url
from roma.core.database import Database
from roma.core.logging import configure_logging
from roma.realtime.pipeline import build_media_app
from roma.repositories.postgres.webhooks import WebhookRepository
from roma.services.webhook_service import WebhookService


def create_app() -> FastAPI:
    """Build the production backend application."""
    configure_logging()
    require_reachable_base_url()
    settings = get_settings()
    database = (
        Database.from_settings(settings) if settings.database_url.get_secret_value() else None
    )

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if database is not None:
                await database.close()

    service = WebhookService(WebhookRepository(database.session_factory)) if database else None
    app = build_media_app(auto_hang_up=True, webhook_service=service, lifespan=lifespan)
    mount_web_api(app)
    return app


__all__ = ["create_app"]
