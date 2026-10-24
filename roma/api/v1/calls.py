"""Authenticated HTTP routes for outbound calls.

This adapter owns HTTP parsing, authentication, and error translation only. The
call gates and provider/repository orchestration live in ``roma.services``.
"""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError

from roma.api.v1.auth import resource_principal
from roma.api.v1.auth import router as auth_router
from roma.api.v1.privacy import router as privacy_router
from roma.api.v1.responses import ApiError, api_error_handler, validation_error_handler
from roma.api.v1.rest import router as rest_router
from roma.core.auth_settings import AuthSettings
from roma.core.config import get_settings
from roma.providers.telephony.twilio.reachability import (
    base_url_reachable as _real_reachable,
)
from roma.services.auth_service import AuthError
from roma.services.call_service import (
    CallServiceError,
    _put_dialing_guarded,
    get_call_status,
    place_outbound_call,
)


def mount_web_api(app, *, reachable_fn=None, session_factory=None) -> None:
    """Mount protected legacy commands and versioned REST resources."""
    settings = get_settings()
    reachability_check = reachable_fn or _real_reachable
    if session_factory is not None:
        app.state.session_factory = session_factory
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.state.auth_settings = getattr(app.state, "auth_settings", None) or AuthSettings()
    app.include_router(auth_router)
    app.include_router(rest_router)
    app.include_router(privacy_router)

    async def require_auth(request: Request, permission) -> None:
        try:
            principal = await resource_principal(request)
            principal.require(permission)
        except (ApiError, AuthError) as error:
            raise HTTPException(status_code=error.status_code, detail=error.message) from None

    @app.post("/api/call")
    async def place_call_route(request: Request):
        await require_auth(request, "calls.create")
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
        await require_auth(request, "calls.view")
        try:
            return await get_call_status(request_uuid, settings=settings)
        except CallServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


__all__ = ["mount_web_api", "_put_dialing_guarded"]
