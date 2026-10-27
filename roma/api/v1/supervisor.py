"""Administrator-only live metadata; no query-string credentials or caller text."""

import asyncio
import secrets
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, SecretStr

from roma.api.v1.auth import current_principal
from roma.api.v1.responses import ApiError, success_response
from roma.repositories.postgres.supervisor import SupervisorRepository
from roma.services.auth_service import Principal
from roma.services.supervisor import relay_supervisor

router = APIRouter(prefix="/api/v1/supervisor", tags=["supervisor"])


async def admin(principal: Annotated[Principal, Depends(current_principal)]):
    if not principal.is_active or "admin" not in principal.roles:
        raise ApiError(403, "FORBIDDEN", "Administrator permission required.")
    return principal


@router.get("/snapshot", dependencies=[Depends(admin)])
async def snapshot(request: Request):
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "Supervisor snapshot unavailable.")
    try:
        async with asyncio.timeout(2):
            async with factory() as session:
                values = await SupervisorRepository(session).snapshot()
    except Exception:  # noqa: BLE001 — never expose DB data/errors in a live channel.
        raise ApiError(
            503, "DATABASE_UNAVAILABLE", "Supervisor snapshot unavailable."
        ) from None
    response = success_response(
        {
            "calls": [event.model_dump(mode="json") for event in values],
            "delivery": "ephemeral; fetch snapshot after reconnect",
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


class Admission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


_RENEW = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],30) else return 0 end"
_RELEASE = "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end"


@router.websocket("/ws")
async def supervisor_socket(websocket: WebSocket):
    # Browser access is same-origin only; forwarded headers never widen trust.
    origin = websocket.headers.get("origin")
    expected = (
        "https://" if websocket.url.scheme == "wss" else "http://"
    ) + websocket.headers.get("host", "")
    if websocket.query_params or (origin is not None and origin != expected):
        await websocket.close(code=1008)
        return
    service = getattr(websocket.app.state, "auth_service", None)
    hub = getattr(websocket.app.state, "supervisor_hub", None)
    if service is None or hub is None:
        await websocket.close(code=1013)
        return
    limiter = getattr(websocket.app.state, "request_rate_limiter", None)
    try:
        async with asyncio.timeout(1):
            if limiter is None or not await limiter.take(
                "supervisor_admission",
                websocket.client.host if websocket.client else "unknown",
                limit=10,
                window=60,
            ):
                await websocket.close(code=1013)
                return
    except Exception:  # noqa: BLE001 — rate-limit coordination fails closed.
        await websocket.close(code=1013)
        return
    await websocket.accept()
    owner = secrets.token_hex(16)
    lease = None
    try:
        async with asyncio.timeout(5):
            raw = await websocket.receive_text()
            if len(raw) > 4200:
                raise ValueError()
            token = Admission.model_validate_json(raw).token.get_secret_value()
            principal = await service.authenticate(token)
            if not principal.is_active or "admin" not in principal.roles:
                raise PermissionError()
            lease = "roma:supervisor:subscriber:" + str(principal.user_id)
            if not await hub.redis.set(lease, owner, nx=True, ex=30):
                lease = None
                raise BufferError()

        async def authorize():
            principal = await service.authenticate(token)
            return (
                principal.is_active
                and "admin" in principal.roles
                and bool(await hub.redis.eval(_RENEW, 1, lease, owner))
            )

        await relay_supervisor(websocket, hub.redis, authorize)
    except WebSocketDisconnect:
        pass
    except (PermissionError, ValueError):
        with suppress(Exception):
            await websocket.close(code=1008)
    except Exception:  # noqa: BLE001 — closed/saturated/failed channels require reconnect.
        with suppress(Exception):
            await websocket.close(code=1013)
    finally:
        if lease is not None:
            with suppress(Exception):
                async with asyncio.timeout(1):
                    await hub.redis.eval(_RELEASE, 1, lease, owner)
