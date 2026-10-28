"""Human-only recording access and explicit preview/apply privacy operations."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.exc import SQLAlchemyError

from roma.api.v1.auth import current_principal, require_permission
from roma.api.v1.responses import ApiError, success_response
from roma.core.field_encryption import PrivacyError
from roma.services.auth_service import Principal
from roma.services.recording_service import RecordingUnavailable

router = APIRouter(prefix="/api/v1")
RecordingActor = Annotated[Principal, Depends(require_permission("recordings.view"))]
PrivacyActor = Annotated[Principal, Depends(require_permission("users.manage"))]


class DisposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete", "anonymize"] = "delete"
    apply: StrictBool = False


def _service(request, name):
    service = getattr(request.app.state, name, None)
    if service is None:
        raise ApiError(503, "PRIVACY_NOT_CONFIGURED", "Privacy storage is unavailable.")
    return service


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/recordings/{recording_id}/access")
async def recording_access(recording_id: UUID, request: Request, actor: RecordingActor):
    try:
        value = await _service(request, "recording_service").issue(recording_id, actor.user_id)
    except (RecordingUnavailable, SQLAlchemyError, OSError, TimeoutError):
        raise ApiError(
            404, "RECORDING_UNAVAILABLE", "Recording access is unavailable."
        ) from None
    await require_permission("recordings.view")(await current_principal(request))
    return _private(success_response(value))


@router.get("/recordings/{recording_id}/content")
async def recording_content(
    recording_id: UUID, token: str, request: Request, actor: RecordingActor
):
    try:
        data, media_type = await _service(request, "recording_service").play(
            recording_id, actor.user_id, token
        )
    except (RecordingUnavailable, SQLAlchemyError, OSError, TimeoutError):
        raise ApiError(
            404, "RECORDING_UNAVAILABLE", "Recording access is unavailable."
        ) from None
    await require_permission("recordings.view")(await current_principal(request))
    return _private(
        Response(
            data,
            media_type=media_type,
            headers={"Content-Disposition": 'inline; filename="recording.wav"'},
        )
    )


@router.post("/privacy/calls/{call_id}")
async def dispose_call(
    call_id: UUID, body: DisposalRequest, request: Request, actor: PrivacyActor
):
    try:
        value = await _service(request, "privacy_service").dispose(
            call_id, action=body.action, apply=body.apply, actor_id=actor.user_id
        )
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Call does not exist.") from None
    except ValueError:
        raise ApiError(
            409, "PRIVACY_DISPOSAL_BLOCKED", "Call cannot be disposed in its current state."
        ) from None
    except (SQLAlchemyError, PrivacyError, OSError, TimeoutError):
        raise ApiError(503, "PRIVACY_UNAVAILABLE", "Privacy storage is unavailable.") from None
    return _private(success_response(value))


@router.post("/privacy/subjects/{caller_id}")
async def dispose_subject(
    caller_id: UUID, body: DisposalRequest, request: Request, actor: PrivacyActor
):
    try:
        value = await _service(request, "privacy_service").dispose_subject(
            caller_id, action=body.action, apply=body.apply, actor_id=actor.user_id
        )
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Caller does not exist.") from None
    except ValueError:
        raise ApiError(
            409, "PRIVACY_DISPOSAL_BLOCKED", "Subject cannot be disposed in its current state."
        ) from None
    except (SQLAlchemyError, PrivacyError, OSError, TimeoutError):
        raise ApiError(503, "PRIVACY_UNAVAILABLE", "Privacy storage is unavailable.") from None
    return _private(success_response(value))
