"""Governed Admin uploads and bounded Admin/Counsellor metadata search."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from roma.api.v1.auth import require_permission
from roma.api.v1.responses import ApiError, success_response
from roma.domain.knowledge.governance import KnowledgeError
from roma.schemas.knowledge import (
    ApproveKnowledgeRequest,
    IngestKnowledgeRequest,
    KnowledgeActionRequest,
    SearchKnowledgeRequest,
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


DebugUser = Annotated[Principal, Depends(require_permission("rag.debug"))]


@router.post("/search")
async def search(body: SearchKnowledgeRequest, request: Request, principal: DebugUser):
    from dataclasses import asdict

    from roma.domain.knowledge.retrieval import RetrievalFilters
    search_service = getattr(request.app.state, 'knowledge_search_service', None)
    if search_service is None:
        raise ApiError(503, 'KNOWLEDGE_NOT_CONFIGURED', 'Knowledge retrieval is not configured.')
    filters = RetrievalFilters(body.category, body.language, body.course_id, body.branch_id,
                               body.version, body.allow_english)
    try:
        result = await search_service.search(body.query, filters)
        # The debug API exposes metadata, never caller query or source text.
        selected = tuple(e for e in result.evidence if e.document_id == result.evidence[0].document_id) if result.evidence else ()
        await search_service.record(filters, selected, fallback=not bool(selected),
                                    outcome='eligible' if selected else 'no_evidence', latency_ms=result.latency_ms)
    except KnowledgeError as error:
        raise ApiError(error.status_code, error.code, 'Knowledge retrieval unavailable.') from None
    used = {key: str(value) if isinstance(value, UUID) else value for key, value in asdict(filters).items()}
    return success_response({'chunk_ids': [str(e.chunk_id) for e in result.evidence],
                             'document_ids': [str(e.document_id) for e in result.evidence],
                             'scores': [e.score for e in result.evidence], 'filters': used,
                             'latency_ms': result.latency_ms, 'embedding_ms': result.embedding_ms})


@router.post('/documents/{document_id}/ingest')
async def ingest(document_id: UUID, body: IngestKnowledgeRequest, request: Request, principal: UploadAdmin):
    return await invoke(service(request).ingest(principal, document_id))


@router.get('/documents/{document_id}/ingestion-jobs')
async def ingestion_jobs(document_id: UUID, request: Request, principal: UploadAdmin):
    return await invoke(service(request).ingestion_status(principal, document_id))
