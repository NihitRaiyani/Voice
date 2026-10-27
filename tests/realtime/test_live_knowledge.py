import asyncio

from roma.domain.conversation.state import CallState
from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
from roma.services.knowledge_answer import KnowledgeAnswer


def test_live_retrieval_deadline_cancels_source_and_defers():
    from roma.realtime.knowledge import LiveKnowledge
    async def run():
        stopped = asyncio.Event()
        class Source:
            async def answer_turn(self, *args, **kwargs):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
        answer = await LiveKnowledge(Source(), timeout_secs=.01).answer_turn(CallState(), 'SEO?', 'course')
        assert stopped.is_set() and answer.fallback and answer.text == NO_EVIDENCE_LINE
    asyncio.run(run())


def test_live_retrieval_plays_one_cached_wait_and_cleans_up():
    from roma.realtime.knowledge import LiveKnowledge
    async def run():
        waits = []
        async def filler():
            waits.append('played')
        class Source:
            async def answer_turn(self, *args, **kwargs):
                await asyncio.sleep(.02)
                return KnowledgeAnswer('SEO campaigns mein sikhaya jaata hai.', (), False)
        answer = await LiveKnowledge(Source(), filler_after_secs=.001).answer_turn(
            CallState(), 'SEO?', 'course', on_wait=filler)
        assert not answer.fallback and waits == ['played']
    asyncio.run(run())


def test_barge_in_cancels_wait_and_late_fact():
    from roma.realtime.knowledge import LiveKnowledge
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        class Source:
            async def answer_turn(self, *args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        live = LiveKnowledge(Source())
        task = asyncio.create_task(live.answer_turn(CallState(), 'SEO?', 'course'))
        await entered.wait()
        await live.interrupt()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('Interrupted fact escaped')
        assert cancelled.is_set()
    asyncio.run(run())
