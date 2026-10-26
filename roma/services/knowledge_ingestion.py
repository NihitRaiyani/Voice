"""Claim, prepare and atomically publish governed evidence outside the voice path."""

import asyncio
import math
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from roma.core.ai_contracts import EmbeddingIdentity, EmbeddingRequest, ProviderUnavailable
from roma.domain.knowledge.governance import KnowledgeError, validate_content
from roma.domain.knowledge.ingestion import IngestionError, chunk_document, pipeline_identity
from roma.providers.ai.embeddings import validate_embedding_result


class KnowledgeIngestionService:
    def __init__(
        self,
        repository,
        provider,
        *,
        identity: EmbeddingIdentity,
        timeout_secs: float = 60,
        batch_size: int = 4,
        now_fn=lambda: datetime.now(UTC),
    ):
        if (
            not math.isfinite(timeout_secs)
            or not 0 < timeout_secs <= 240
            or not 1 <= batch_size <= 16
        ):
            raise ValueError("Invalid ingestion resource bounds")
        self.repository, self.provider, self.identity = repository, provider, identity
        self.timeout_secs, self.batch_size, self.now_fn = timeout_secs, batch_size, now_fn
        self.pipeline_identity = pipeline_identity(identity)

    async def process(self, job_id: UUID) -> str:
        if not isinstance(job_id, UUID):
            raise ValueError("Knowledge job requires UUID identity")
        owner = str(uuid4())
        job = await self.repository.claim_ingestion(job_id, owner, self.now_fn())
        if job is None:
            return "ignored"
        try:
            async with asyncio.timeout(self.timeout_secs):
                if job.pipeline_identity != self.pipeline_identity or any(
                    getattr(job, field) != value
                    for field, value in (
                        ("model", self.identity.model),
                        ("revision", self.identity.revision),
                        ("dimensions", self.identity.dimension),
                        ("preprocessing", self.identity.preprocessing),
                        ("artifacts_sha256", self.identity.artifacts_sha256),
                    )
                ):
                    raise IngestionError("KNOWLEDGE_EMBEDDING_IDENTITY")
                document = await self.repository.load_ingestion_document(job)
                if document.content_hash != job.expected_hash or document.status == "archived":
                    raise IngestionError("KNOWLEDGE_CONTENT_CHANGED")
                chunks = chunk_document(document.content, language=document.language)
                if any(chunk.quarantined for chunk in chunks):
                    raise IngestionError("KNOWLEDGE_INSTRUCTION_QUARANTINED", quarantine=True)
                validate_content(document.content)
                for chunk in chunks:
                    validate_content(chunk.content)
                prepared = []
                for offset in range(0, len(chunks), self.batch_size):
                    batch = chunks[offset : offset + self.batch_size]
                    result = await self.provider.embed(
                        EmbeddingRequest(
                            texts=tuple(chunk.content for chunk in batch),
                            model=self.identity.model,
                            kind="document",
                        )
                    )
                    try:
                        validate_embedding_result(
                            result, count=len(batch), identity=self.identity
                        )
                    except (ProviderUnavailable, TypeError, AttributeError):
                        raise IngestionError("KNOWLEDGE_EMBEDDING_INVALID") from None
                    for chunk, vector in zip(batch, result.vectors, strict=True):
                        payload = asdict(chunk)
                        payload.pop("quarantined")
                        payload["vector"] = vector
                        prepared.append(payload)
                settled = await self.repository.complete_ingestion(
                    job,
                    owner,
                    job.expected_hash,
                    tuple(prepared),
                    self.identity,
                    now=self.now_fn(),
                )
                return "succeeded" if settled else "lost_lease"
        except IngestionError as error:
            return await self.repository.fail_ingestion(
                job,
                owner,
                error.reason.lower(),
                permanent=True,
                quarantine=error.quarantine,
                now=self.now_fn(),
            )
        except KnowledgeError as error:
            return await self.repository.fail_ingestion(
                job,
                owner,
                "knowledge_validation_failed",
                permanent=True,
                quarantine=error.code in {"KNOWLEDGE_UNSAFE", "KNOWLEDGE_PRIVATE_CONTENT"},
                now=self.now_fn(),
            )
        except Exception as error:  # noqa: BLE001 — no provider text or source content in job errors
            reason = (
                "ingestion_timeout"
                if isinstance(error, TimeoutError)
                else "embedding_unavailable"
                if isinstance(error, ProviderUnavailable)
                else "ingestion_unavailable"
            )
            return await self.repository.fail_ingestion(
                job, owner, reason, permanent=False, quarantine=False, now=self.now_fn()
            )
