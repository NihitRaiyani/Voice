"""Human login/refresh/logout and live permission dependencies; carrier auth is separate."""

import secrets
from dataclasses import asdict, dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from redis.exceptions import RedisError

from roma.api.v1.responses import ApiError, success_response
from roma.core.auth_settings import AuthSettings
from roma.core.config import get_settings
from roma.core.rate_limit import limit_authenticated
from roma.schemas.auth import CreateUserRequest, LoginRequest, RefreshRequest, UpdateUserRequest
from roma.services.auth_service import AuthError, Permission, Principal

router = APIRouter(prefix="/api/v1")


def auth_service(request):
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "Human authentication is not configured.")
    return service


def _error(error):
    return ApiError(error.status_code, error.code, error.message)


async def current_principal(request: Request) -> Principal:
    service = auth_service(request)
    header = request.headers.get("authorization", "")
    if not header.isascii() or len(header) > 4200:
        raise ApiError(401, "UNAUTHORIZED", "Bad or missing bearer token.")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "UNAUTHORIZED", "Bad or missing bearer token.")
    try:
        principal = await service.authenticate(token.strip())
    except AuthError as error:
        raise _error(error) from None
    await limit_authenticated(request, "user:" + str(principal.user_id))
    request.state.principal = principal
    return principal


def require_permission(permission: str):
    required = Permission(permission)

    async def authorize(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        try:
            return principal.require(required)
        except AuthError as error:
            raise _error(error) from None

    return authorize


@dataclass(frozen=True)
class MachinePrincipal:
    """Explicit compatibility identity; never a SQL user or account administrator."""

    permissions = frozenset(
        {
            Permission.CALLS_VIEW,
            Permission.CALLS_CREATE,
            Permission.ANALYTICS_VIEW,
            Permission.RECORDINGS_VIEW,
            Permission.APPOINTMENTS_VIEW,
            Permission.APPOINTMENTS_MANAGE,
            Permission.LEADS_VIEW,
            Permission.SAFETY_VIEW,
        }
    )

    def require(self, permission):
        if permission not in self.permissions:
            raise AuthError(403, "FORBIDDEN", "Permission denied.")
        return self


async def resource_principal(request: Request):
    header = request.headers.get("authorization", "")
    if not header.isascii() or len(header) > 4200:
        raise ApiError(401, "UNAUTHORIZED", "Bad or missing bearer token.")
    settings = getattr(request.app.state, "auth_settings", None) or AuthSettings()
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if settings.auth_allow_legacy_api_token:
        static = get_settings().api_token
        # JWT-shaped credentials always take the JWT path. No verification failure
        # can downgrade to the independent, explicitly enabled machine credential.
        if (
            scheme.lower() == "bearer"
            and token
            and "." not in token
            and static is not None
            and secrets.compare_digest(
                token.encode("ascii"), static.get_secret_value().encode("utf-8")
            )
        ):
            await limit_authenticated(request, "machine:" + token)
            return MachinePrincipal()
        if getattr(request.app.state, "auth_service", None) is None:
            if static is None or not static.get_secret_value():
                raise ApiError(503, "API_TOKEN_NOT_CONFIGURED", "API_TOKEN is not configured.")
            raise ApiError(401, "UNAUTHORIZED", "Bad or missing bearer token.")
    return await current_principal(request)


def require_resource_permission(permission: str):
    required = Permission(permission)

    async def authorize(
        request: Request,
        principal: Annotated[Principal | MachinePrincipal, Depends(resource_principal)],
    ):
        try:
            principal.require(required)
        except AuthError as error:
            raise _error(error) from None
        request.state.resource_principal = principal
        return principal

    return authorize


async def _rate_limit(request):
    limiter = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        raise ApiError(
            503, "AUTH_RATE_LIMIT_UNAVAILABLE", "Authentication rate limiting is unavailable."
        )
    try:
        allowed = await limiter.take(request.client.host if request.client else "unknown")
    except (RedisError, OSError, TimeoutError, ValueError):
        raise ApiError(
            503, "AUTH_RATE_LIMIT_UNAVAILABLE", "Authentication rate limiting is unavailable."
        ) from None
    if not allowed:
        raise ApiError(429, "AUTH_RATE_LIMITED", "Too many authentication attempts.")


def _tokens(pair):
    response = success_response(asdict(pair))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _user(principal):
    return {
        "id": str(principal.user_id),
        "email": principal.email,
        "display_name": principal.display_name,
        "roles": sorted(principal.roles),
        "is_active": principal.is_active,
    }


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request):
    service = auth_service(request)
    await _rate_limit(request)
    try:
        return _tokens(await service.login(body.email, body.password.get_secret_value()))
    except AuthError as error:
        raise _error(error) from None


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, request: Request):
    service = auth_service(request)
    await _rate_limit(request)
    try:
        return _tokens(await service.refresh(body.refresh_token.get_secret_value()))
    except AuthError as error:
        raise _error(error) from None


@router.post("/auth/logout")
async def logout(body: RefreshRequest, request: Request):
    try:
        await auth_service(request).logout(body.refresh_token.get_secret_value())
    except AuthError as error:
        raise _error(error) from None
    response = success_response({"revoked": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/auth/me")
async def me(principal: Annotated[Principal, Depends(current_principal)]):
    return success_response(_user(principal))


@router.post(
    "/users", status_code=201, dependencies=[Depends(require_permission("users.manage"))]
)
async def create_user(body: CreateUserRequest, request: Request):
    try:
        principal = await auth_service(request).create_user(
            body.email, body.display_name, body.password.get_secret_value(), body.roles
        )
    except AuthError as error:
        raise _error(error) from None
    return success_response(_user(principal), status_code=201)


@router.patch("/users/{user_id}", dependencies=[Depends(require_permission("users.manage"))])
async def update_user(user_id: UUID, body: UpdateUserRequest, request: Request):
    try:
        principal = await auth_service(request).update_user(
            user_id, roles=body.roles, is_active=body.is_active
        )
    except AuthError as error:
        raise _error(error) from None
    return success_response(_user(principal))
