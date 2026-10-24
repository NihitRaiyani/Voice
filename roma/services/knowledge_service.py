"""Admin governance facade. No embeddings, downloads or conversational retrieval yet."""

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
        self, session_factory=None, *, repository=None, now_fn=lambda: datetime.now(UTC)
    ):
        self.repository = repository or KnowledgeRepository(session_factory)
        self.now_fn = now_fn

    async def _database(self, operation):
        try:
            return document_metadata(await operation())
        except IntegrityError:
            raise KnowledgeError("KNOWLEDGE_REFERENCE_OR_VERSION_CONFLICT", 409) from None
        except (SQLAlchemyError, *AsyncpgAvailabilityError):
            raise KnowledgeError("KNOWLEDGE_UNAVAILABLE", 503) from None

    async def upload(self, principal, request):
        principal.require(Permission.RAG_UPLOAD)
        return await self._database(
            lambda: self.repository.create(request, actor_id=principal.user_id)
        )

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
