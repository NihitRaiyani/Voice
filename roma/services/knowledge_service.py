"""Admin governance and durable ingestion intent; inference belongs to workers."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from roma.domain.knowledge.governance import KnowledgeError
from roma.repositories.postgres.knowledge import KnowledgeRepository
from roma.repositories.postgres.unit_of_work import AsyncpgAvailabilityError
from roma.services.auth_service import Permission


def document_metadata(document):
    keys = (
        "id",
        "source_key",
        "source_reference",
        "title",
        "version",
        "category",
        "language",
        "owner_id",
        "course_id",
        "branch_id",
        "status",
        "content_hash",
        "approved_hash",
        "approved_by",
        "approved_at",
        "approval_reference",
        "effective_from",
        "effective_until",
        "activated_at",
        "archived_at",
    )
    result = {}
    for key in keys:
        value = getattr(document, key)
        result[key] = (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
    return result


class KnowledgeService:
    def __init__(
        self, session_factory=None, *, repository=None, now_fn=lambda: datetime.now(UTC),
        ingestion_repository=None, ingestion=None
    ):
        self.repository = repository or KnowledgeRepository(session_factory)
        self.now_fn = now_fn
        self.ingestion_repository, self.ingestion = ingestion_repository, ingestion

    async def _database(self, operation, *, serialize=document_metadata):
        try:
            return serialize(await operation())
        except IntegrityError:
            raise KnowledgeError("KNOWLEDGE_REFERENCE_OR_VERSION_CONFLICT", 409) from None
        except (SQLAlchemyError, *AsyncpgAvailabilityError):
            raise KnowledgeError("KNOWLEDGE_UNAVAILABLE", 503) from None

    async def upload(self, principal, request):
        principal.require(Permission.RAG_UPLOAD)
        async def operation():
            intent = {'pipeline_identity': self.ingestion.pipeline_identity,
                      'identity': self.ingestion.identity, 'now': self.now_fn()} if self.ingestion else None
            document = await self.repository.create(request, actor_id=principal.user_id,
                                                    **({'ingestion': intent} if intent else {}))
            result = document_metadata(document)
            if self.ingestion is not None:
                job = await self.ingestion_repository.enqueue_ingestion(
                    document.id, document.content_hash, self.ingestion.pipeline_identity,
                    identity=self.ingestion.identity, now=self.now_fn())
                result['ingestion_job_id'], result['ingestion_status'] = str(job.id), job.status
            return result
        return await self._database(operation, serialize=lambda value: value)

    async def ingest(self, principal, document_id):
        principal.require(Permission.RAG_UPLOAD)
        if self.ingestion is None:
            raise KnowledgeError('KNOWLEDGE_INGESTION_NOT_CONFIGURED', 503)
        async def operation():
            document = await self.repository.get(document_id)
            job = await self.ingestion_repository.enqueue_ingestion(
                document.id, document.content_hash, self.ingestion.pipeline_identity,
                identity=self.ingestion.identity, now=self.now_fn())
            return {'job_id': str(job.id), 'status': job.status}
        return await self._database(operation, serialize=lambda value: value)

    async def ingestion_status(self, principal, document_id):
        principal.require(Permission.RAG_UPLOAD)
        if self.ingestion_repository is None:
            raise KnowledgeError('KNOWLEDGE_INGESTION_NOT_CONFIGURED', 503)
        async def operation():
            await self.repository.get(document_id)
            rows = await self.ingestion_repository.jobs_for_document(document_id)
            return {'jobs': [{'id': str(row.id), 'status': row.status, 'attempts': row.attempts,
                              'reason': row.last_error, 'quarantined': row.quarantined}
                             for row in rows]}
        return await self._database(operation, serialize=lambda value: value)

    async def approve(self, principal, document_id, request):
        principal.require(Permission.RAG_APPROVE)
        return await self._database(
            lambda: self.repository.transition(
                document_id,
                "approve",
                actor_id=principal.user_id,
                reference=request.approval_reference,
                now=self.now_fn(),
            )
        )

    async def activate(self, principal, document_id, request):
        principal.require(Permission.RAG_APPROVE)
        return await self._database(
            lambda: self.repository.transition(
                document_id,
                "activate",
                actor_id=principal.user_id,
                expected_version=request.expected_version,
                now=self.now_fn(),
            )
        )

    async def archive(self, principal, document_id, request):
        principal.require(Permission.RAG_APPROVE)
        return await self._database(
            lambda: self.repository.transition(
                document_id,
                "archive",
                actor_id=principal.user_id,
                expected_version=request.expected_version,
                now=self.now_fn(),
            )
        )
