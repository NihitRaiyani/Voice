"""Call-context and booking-authority checks for governed knowledge answers."""

import asyncio
import hashlib
import json
from uuid import uuid4

from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState
from roma.domain.knowledge.retrieval import Evidence, RetrievalFilters
from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.contracts import LLMChunk
from roma.services.knowledge_answer import KnowledgeAnswer, KnowledgeAnswerService
from roma.services.knowledge_retrieval import SearchResult
from roma.services.knowledge_runtime import ScopedKnowledgeAnswers
from roma.services.safety_service import SafetyService


class Search:
    def __init__(self, content):
        self.evidence = Evidence(uuid4(), uuid4(), content,
            hashlib.sha256(content.encode()).hexdigest(), 1, 'hi-en', .95)
        self.records = []

    async def search(self, query, filters, **kwargs):
        return SearchResult((self.evidence,), query, 1, 1, filters)

    async def revalidate(self, evidence, filters):
        return True

    async def record(self, *args, **kwargs):
        self.records.append(kwargs)


class Selector:
    def __init__(self, search, quote=None):
        self.search, self.quote = search, quote

    async def generate(self, request):
        yield LLMChunk(json.dumps({'chunk_id': str(self.search.evidence.chunk_id),
                                  'quote': self.quote or self.search.evidence.content}),
                       finish_reason='stop')


class CapturingSafety(SafetyService):
    def __init__(self):
        super().__init__()
        self.contexts = []

    async def check(self, text, context, **kwargs):
        self.contexts.append(context)
        return await super().check(text, context, **kwargs)


def test_standalone_knowledge_uses_valid_synthetic_conversation_context():
    async def run():
        search, safety = Search('SEO practical campaigns mein sikhaya jaata hai.'), CapturingSafety()
        answer = await KnowledgeAnswerService(search, Selector(search), safety=safety).answer(
            'SEO course kya hai?', RetrievalFilters('course', 'hi-en'))
        assert not answer.fallback
        assert safety.contexts and all(context.stage == 'value' for context in safety.contexts)
    asyncio.run(run())


def test_scoped_knowledge_forwards_actual_call_safety_context():
    course_id, branch_id, call_id = uuid4(), uuid4(), uuid4()

    class Rows:
        def all(self):
            return [(course_id, branch_id)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            return Rows()

        async def scalar(self, statement):
            return call_id

    class Answer:
        async def answer(self, query, filters, **kwargs):
            self.kwargs = kwargs
            return KnowledgeAnswer(NO_EVIDENCE_LINE)

    async def run():
        answer = Answer()
        state = CallState(call_sid='CA-test-knowledge', stage=ConversationStage.STRUCTURE,
                          turn_count=7)
        await ScopedKnowledgeAnswers(Session, answer).answer_turn(state, 'Policy kya hai?', 'policy')
        assert answer.kwargs['call_id'] == call_id
        assert answer.kwargs['safety_context'] == SafetyContext(state.call_sid, 7, 'structure')
    asyncio.run(run())


def test_explicit_knowledge_safety_context_is_used_for_every_check():
    async def run():
        search, safety = Search('SEO practical campaigns mein sikhaya jaata hai.'), CapturingSafety()
        context = SafetyContext('CA-test-knowledge', 7, 'structure')
        answer = await KnowledgeAnswerService(search, Selector(search), safety=safety).answer(
            'SEO course kya hai?', RetrievalFilters('course', 'hi-en'), safety_context=context)
        assert not answer.fallback and safety.contexts
        assert all(item == context for item in safety.contexts)
    asyncio.run(run())


def test_verbatim_booking_confirmation_cannot_be_released_as_knowledge():
    async def run():
        search = Search('Policy ke anusaar appointment confirmed hai ka message sirf database commit ke baad dena hai.')
        answer = await KnowledgeAnswerService(search, Selector(search, 'appointment confirmed hai')).answer(
            'What is the appointment policy?', RetrievalFilters('policy', 'hi-en'))
        assert answer.fallback and answer.text == NO_EVIDENCE_LINE and not answer.evidence
        assert search.records[0]['fallback'] is True
    asyncio.run(run())
