"""Explicit knowledge composition shared by API, worker and per-call adapters."""
from dataclasses import dataclass

from sqlalchemy import func, select

from roma.domain.knowledge.retrieval import RetrievalFilters
from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.registry import build_embedding_provider
from roma.repositories.postgres.knowledge import KnowledgeRepository
from roma.repositories.postgres.knowledge_ingestion import KnowledgeIngestionRepository
from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository
from roma.repositories.postgres.models.calls import Call
from roma.repositories.postgres.models.organization import Branch, BranchCourse, Course
from roma.services.knowledge_answer import KnowledgeAnswer, KnowledgeAnswerService
from roma.services.knowledge_ingestion import KnowledgeIngestionService
from roma.services.knowledge_retrieval import KnowledgeSearchService
from roma.services.knowledge_service import KnowledgeService


@dataclass(frozen=True)
class KnowledgeComponents:
    catalog: object
    provider: object
    search: object
    ingestion: object
    ingestion_repository: object


def build_knowledge_components(session_factory, settings):
    provider = build_embedding_provider(settings)
    repository = KnowledgeRepository(session_factory)
    jobs = KnowledgeIngestionRepository(session_factory)
    ingestion = KnowledgeIngestionService(jobs, provider, identity=provider.identity)
    search = KnowledgeSearchService(KnowledgeSearchRepository(session_factory), provider,
                                    audit_repository=repository, k=settings.rag_top_k,
                                    threshold=settings.rag_min_score,
                                    timeout_secs=settings.rag_retrieval_timeout_secs)
    catalog = KnowledgeService(session_factory, repository=repository,
                               ingestion_repository=jobs, ingestion=ingestion)
    return KnowledgeComponents(catalog, provider, search, ingestion, jobs)


class ScopedKnowledgeAnswers:
    """Read only approved course/branch scope; pass no profile or booking authority."""
    def __init__(self, session_factory, answer):
        self.session_factory, self.answer = session_factory, answer

    async def answer_turn(self, state, text, topic, *, output_language='code_mix'):
        try:
            async with self.session_factory() as session:
                rows = (await session.execute(
                    select(Course.id, Branch.id).join(BranchCourse, BranchCourse.course_id == Course.id)
                    .join(Branch, Branch.id == BranchCourse.branch_id).where(
                        Course.code == state.course, Course.is_active.is_(True), Branch.is_active.is_(True),
                        Course.institute_id == Branch.institute_id,
                        func.lower(Branch.name) == state.branch.casefold())
                    .limit(2))).all()
                call_id = await session.scalar(select(Call.id).where(Call.provider_call_id == state.call_sid))
            if len(rows) != 1:
                return KnowledgeAnswer(NO_EVIDENCE_LINE, reason='unknown_scope')
            # Cross-lingual queries target the approved spoken-output evidence policy.
            # Caller-language metadata never changes Hindi-base Hinglish production replies.
            language = 'hi' if output_language == 'voice_hi' else 'hi-en'
            return await self.answer.answer(text, RetrievalFilters(topic, language,
                        course_id=rows[0][0], branch_id=rows[0][1]),
                        output_language=output_language, known_name=state.lead_name, call_id=call_id,
                        safety_context=SafetyContext(state.call_sid, max(1, state.turn_count),
                                                     state.stage.value))
        except Exception:  # noqa: BLE001 — unknown scope/storage cannot authorize a fact.
            return KnowledgeAnswer(NO_EVIDENCE_LINE, reason='scope_unavailable')


def bind_knowledge_answers(session_factory, components, llm, *, safety=None, timeout_secs=10):
    answer = KnowledgeAnswerService(components.search, llm, safety=safety, timeout_secs=timeout_secs)
    return ScopedKnowledgeAnswers(session_factory, answer)
