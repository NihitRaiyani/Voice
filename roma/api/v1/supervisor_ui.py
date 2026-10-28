"""Data-free public shell; existing Admin JWT APIs authorize every data request."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/admin/supervisor", include_in_schema=False)
_ASSETS = Path(__file__).with_name("supervisor_assets")
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


@router.get("")
async def supervisor_page():
    return FileResponse(_ASSETS / "index.html", media_type="text/html", headers=_HEADERS)


@router.get("/assets/{name}")
async def supervisor_asset(name: str):
    types = {"app.js": "text/javascript", "style.css": "text/css"}
    if name not in types:
        raise HTTPException(status_code=404)
    return FileResponse(_ASSETS / name, media_type=types[name], headers=_HEADERS)
