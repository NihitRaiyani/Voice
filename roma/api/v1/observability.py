"""Private scrape endpoint: trusted socket loopback or a live human administrator."""

from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from roma.api.v1.auth import current_principal
from roma.api.v1.responses import ApiError
from roma.core.telemetry import get_telemetry, sample_process_resources
from roma.services.auth_service import Principal

router = APIRouter()


async def metrics_principal(request: Request):
    # Forwarded headers are deliberately ignored. A proxy must strip/unset its
    # trusted-proxy configuration or protect this path before forwarding to Roma.
    try:
        loopback = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        loopback = False
    if loopback:
        return None
    return await current_principal(request)


@router.get('/metrics', include_in_schema=False)
async def metrics(request: Request, principal: Annotated[Principal | None, Depends(metrics_principal)]):
    if principal is not None and (not principal.is_active or 'admin' not in principal.roles):
        raise ApiError(403, 'FORBIDDEN', 'Administrator permission required.')
    telemetry = getattr(request.app.state, 'telemetry', None) or get_telemetry()
    sample_process_resources(telemetry)
    return Response(telemetry.render(), media_type='text/plain; version=0.0.4',
                    headers={'Cache-Control': 'no-store'})
