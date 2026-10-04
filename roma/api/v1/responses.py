"""Shared response envelopes and REST API error helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(RuntimeError):
    """Error shape used by versioned JSON resource endpoints."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def success_response(
    data: Any,
    *,
    meta: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """Return the Level 8 success envelope."""
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data, "meta": meta or {}},
    )


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return the Level 8 error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
    )


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


async def validation_error_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return error_response(422, "VALIDATION_ERROR", "Request validation failed.")


__all__ = [
    "ApiError",
    "api_error_handler",
    "error_response",
    "success_response",
    "validation_error_handler",
]
