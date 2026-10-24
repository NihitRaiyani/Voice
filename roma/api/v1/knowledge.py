"""Admin-only evidence governance. JSON text only; no upload execution or retrieval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from roma.api.v1.auth import require_permission
from roma.api.v1.responses import ApiError, success_response
from roma.domain.knowledge.governance import KnowledgeError
from roma.schemas.knowledge import (
    ApproveKnowledgeRequest,
    KnowledgeActionRequest,
    UploadKnowledgeRequest,
)
from roma.services.auth_service import Principal

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
UploadAdmin = Annotated[Principal, Depends(require_permission("rag.upload"))]
ApprovalAdmin = Annotated[Principal, Depends(require_permission("rag.approve"))]


def service(request):
    result = getattr(request.app.state, "knowledge_service", None)
    if result is None:
        raise ApiError(
            503, "KNOWLEDGE_NOT_CONFIGURED", "Knowledge governance is not configured."
        )
    return result


async def invoke(operation, *, created=False):
    try:
        result = await operation
    except KnowledgeError as error:
        raise ApiError(
            error.status_code,
            error.code,
            "Knowledge governance request could not be completed.",
        ) from None
    return success_response(result, status_code=201 if created else 200)


@router.post("/documents", status_code=201)
async def upload(body: UploadKnowledgeRequest, request: Request, principal: UploadAdmin):
    return await invoke(service(request).upload(principal, body), created=True)


@router.post("/documents/{document_id}/approve")
async def approve(
    document_id: UUID, body: ApproveKnowledgeRequest, request: Request, principal: ApprovalAdmin
):
    return await invoke(service(request).approve(principal, document_id, body))


@router.post("/documents/{document_id}/activate")
async def activate(
    document_id: UUID, body: KnowledgeActionRequest, request: Request, principal: ApprovalAdmin
):
    return await invoke(service(request).activate(principal, document_id, body))


@router.post("/documents/{document_id}/archive")
async def archive(
    document_id: UUID, body: KnowledgeActionRequest, request: Request, principal: ApprovalAdmin
):
    return await invoke(service(request).archive(principal, document_id, body))
