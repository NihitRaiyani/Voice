"""Authenticated HTTP routes for outbound calls.

This adapter owns HTTP parsing, authentication, and error translation only. The
call gates and provider/repository orchestration live in ``roma.services``.
"""

import secrets

from fastapi import HTTPException, Request

from roma.core.config import get_settings
from roma.providers.telephony.twilio.reachability import (
    base_url_reachable as _real_reachable,
)
from roma.services.call_service import (
    CallServiceError,
    _put_dialing_guarded,
    get_call_status,
    place_outbound_call,
)


def mount_web_api(app, *, reachable_fn=None) -> None:
    """Mount the protected call command and query endpoints."""
    settings = get_settings()
    reachability_check = reachable_fn or _real_reachable

    def require_auth(request: Request) -> None:
        if settings.api_token is None or not settings.api_token.get_secret_value():
            raise HTTPException(
                status_code=503,
                detail="API_TOKEN is not configured; the dial endpoint is disabled",
            )
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            presented.strip(), settings.api_token.get_secret_value()
        ):
            raise HTTPException(status_code=401, detail="bad or missing bearer token")

    @app.post("/api/call")
    async def place_call_route(request: Request):
        require_auth(request)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — malformed input is a client error
            raise HTTPException(status_code=400, detail="expected a JSON body") from None

        # Deliberately ignore every other key so callers cannot widen a safety gate.
        to_number = str((payload or {}).get("to_number", "")).strip()
        try:
            return await place_outbound_call(
                to_number,
                settings=settings,
                reachable_fn=reachability_check,
            )
        except CallServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @app.get("/api/call/{request_uuid}")
    async def call_status_route(request_uuid: str, request: Request):
        require_auth(request)
        try:
            return await get_call_status(request_uuid, settings=settings)
        except CallServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


__all__ = ["mount_web_api", "_put_dialing_guarded"]
