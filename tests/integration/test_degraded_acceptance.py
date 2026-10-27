"""Combined dependency failures through public orchestration, with no paid/native AI."""
import asyncio

from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState
from roma.domain.conversation.state_machine import InMemoryConversationStateStore
from roma.domain.knowledge.retrieval import RetrievalFilters
from roma.domain.safety.filter import screen
from roma.providers.ai.contracts import ProviderUnavailable
from roma.services.live_call_state import LiveCallStateStore
from roma.services.text_conversation_service import TextConversationService
from tests.services.test_knowledge_answer import Repository, Selector, service


class FailingHotState(InMemoryConversationStateStore):
    async def save(self,state):
        raise OSError('synthetic unavailable hot projection')
    async def load(self,sid):
        raise OSError('synthetic unavailable hot projection')


class UnavailableLLM:
    def __init__(self):
        self.closed=False
    async def generate(self,request):
        try:
            raise ProviderUnavailable('synthetic unavailable generation')
            yield  # asynchronous provider contract
        finally:
            self.closed=True


def test_llm_and_hot_cache_outage_preserve_durable_slots_and_safe_unbooked_response():
    async def run():
        durable=InMemoryConversationStateStore()
        checkpoints=LiveCallStateStore(FailingHotState(),durable,retry_delay=0)
        state=CallState(call_sid='synthetic-degraded-call',stage=ConversationStage.VALUE,
                        education='BCom',city='Vadodara')
        provider=UnavailableLLM()
        conversation=TextConversationService(provider,store=checkpoints)
        from roma.eval.runner import FROZEN_NOW
        trace=await conversation.turn(state,'haan ji',now=FROZEN_NOW)
        assert provider.closed and trace.provider_error is not None
        assert screen(trace.final_response).allowed and not trace.booking_committed
        assert state.appointment_id is None
        restored=await checkpoints.load(state.call_sid)
        assert restored.education=='BCom' and restored.city=='Vadodara'
        assert restored.turn_count==state.turn_count
        assert checkpoints.metrics()['redis_failures']>=2
        await checkpoints.aclose()
    asyncio.run(run())


def test_retrieval_timeout_cancels_search_before_llm_and_next_call_recovers():
    async def run():
        class SlowRepository(Repository):
            slow=True
            cancelled=False
            async def search_chunks(self,**kwargs):
                if self.slow:
                    try:
                        await asyncio.sleep(10)
                    finally:
                        self.cancelled=True
                return await super().search_chunks(**kwargs)
        repository=SlowRepository()
        provider=Selector(repository)
        answers=service(repository,provider,timeout_secs=.01)
        filters=RetrievalFilters('course','hi-en')
        before=set(asyncio.all_tasks())
        result=await answers.answer('SEO practical mein kya sikhate hain?',filters)
        assert result.fallback and not result.evidence and repository.cancelled
        assert not provider.requests and screen(result.text).allowed
        repository.slow=False
        recovered=await answers.answer('SEO practical mein kya sikhate hain?',filters)
        assert not recovered.fallback and recovered.evidence
        assert set(asyncio.all_tasks())==before
    asyncio.run(run())
