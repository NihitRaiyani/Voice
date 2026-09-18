"""Production application composition root.

Only this module assembles concrete providers, repositories, routes, and the
realtime pipeline. Lower layers remain importable without starting the server.
"""

from fastapi import FastAPI

from roma.api.v1.calls import mount_web_api
from roma.core.config import require_reachable_base_url
from roma.core.logging import configure_logging
from roma.realtime.pipeline import build_media_app


def create_app() -> FastAPI:
    """Build the production backend application."""
    configure_logging()
    require_reachable_base_url()
    app = build_media_app(auto_hang_up=True)
    mount_web_api(app)
    return app


__all__ = ["create_app"]
