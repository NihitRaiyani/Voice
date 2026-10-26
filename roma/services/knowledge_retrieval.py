"""Bounded query embedding/exact search; raw caller transcripts never enter logs."""
import asyncio
import math
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from roma.core.prompt_privacy import redact_prompt
from roma.core.turn_epoch import require_current_epoch
from roma.domain.knowledge.governance import KnowledgeError
from roma.domain.knowledge.retrieval import cosine_similarity
from roma.domain.safety.inputs import injection_attempt
from roma.providers.ai.contracts import EmbeddingRequest
from roma.schemas.knowledge import RetrievalMetadata


def normalize_query(query, *, known_name=None):
    if not isinstance(query, str) or not 1 <= len(query) <= 2000:
        raise KnowledgeError('KNOWLEDGE_INVALID_QUERY')
    text = unicodedata.normalize('NFC', query)
    if any(unicodedata.category(c) in {'Cc', 'Cf'} and c not in '\n\r\t' for c in text):
        raise KnowledgeError('KNOWLEDGE_INVALID_QUERY')
    text = ' '.join(redact_prompt(text, known_name=known_name).split())
    if not text or injection_attempt(text):
        raise KnowledgeError('KNOWLEDGE_INVALID_QUERY')
    return text


@dataclass(frozen=True)
class SearchResult:
    evidence: tuple
    normalized_query: str
    latency_ms: float
    embedding_ms: float
    filters: object


class KnowledgeSearchService:
    def __init__(self, repository, provider, *, audit_repository=None, k=5, threshold=.8,
                 timeout_secs=.45, now_fn=lambda: datetime.now(UTC)):
        if (type(k) is not int or not 1 <= k <= 20
                or type(threshold) not in (int, float) or not math.isfinite(threshold)
                or not -1 <= threshold <= 1 or not 0 < timeout_secs <= 10):
            raise ValueError('Invalid retrieval policy')
        self.repository, self.provider = repository, provider
        self.audit_repository, self.k, self.threshold = audit_repository, k, threshold
        self.timeout_secs, self.now_fn = timeout_secs, now_fn

    async def search(self, query, filters, *, known_name=None):
        query = normalize_query(query, known_name=known_name)
        started = perf_counter()
        require_current_epoch()
        try:
            async with asyncio.timeout(self.timeout_secs):
                vector_started = perf_counter()
                result = await self.provider.embed(EmbeddingRequest((query,), kind='query'))
                require_current_epoch()
                identity = self.provider.identity
                if (result.identity != identity or result.dimension != identity.dimension
                        or len(result.vectors) != 1 or len(result.vectors[0]) != identity.dimension):
                    raise ValueError('Incompatible embedding identity')
                cosine_similarity(result.vectors[0], result.vectors[0])
                embedding_ms = (perf_counter() - vector_started) * 1000
                evidence = await self.repository.search_chunks(
                    query_vector=result.vectors[0], identity=identity, filters=filters,
                    now=self.now_fn(), k=self.k, threshold=self.threshold)
                require_current_epoch()
                if len(evidence) > self.k:
                    raise ValueError('Excess evidence')
                return SearchResult(tuple(evidence), query, (perf_counter()-started)*1000,
                                    embedding_ms, filters)
        except KnowledgeError:
            raise
        except Exception:  # noqa: BLE001 — provider/database details never cross the API.
            raise KnowledgeError('KNOWLEDGE_UNAVAILABLE', 503) from None

    async def revalidate(self, evidence, filters):
        async with asyncio.timeout(self.timeout_secs):
            fresh = await self.repository.revalidate_evidence(
                chunk_ids=tuple(e.chunk_id for e in evidence), identity=self.provider.identity,
                filters=filters, now=self.now_fn())
        require_current_epoch()
        by_id = {e.chunk_id: e for e in fresh}
        return bool(evidence) and all(
            e.chunk_id in by_id and by_id[e.chunk_id].content_hash == e.content_hash
            and by_id[e.chunk_id].content == e.content
            and by_id[e.chunk_id].document_id == e.document_id
            and by_id[e.chunk_id].document_version == e.document_version for e in evidence)

    async def record(self, filters, evidence=(), *, fallback=True, outcome='no_evidence',
                     latency_ms=0, call_id=None, turn_id=None, request_id=None):
        if self.audit_repository is None:
            raise KnowledgeError('KNOWLEDGE_AUDIT_UNAVAILABLE', 503)
        metadata = RetrievalMetadata(
            request_id=request_id or uuid4(), call_id=call_id, turn_id=turn_id,
            chunk_ids=[e.chunk_id for e in evidence], scores=[float(e.score) for e in evidence],
            selected_document_id=evidence[0].document_id if evidence else None,
            category=filters.category, route='no_evidence' if fallback else 'knowledge',
            fallback_used=fallback, outcome=outcome, eligible_count=len(evidence),
            latency_ms=min(60000, max(0, int(latency_ms))))
        async with asyncio.timeout(self.timeout_secs):
            await self.audit_repository.record_retrieval(metadata, now=self.now_fn())
        require_current_epoch()
