import asyncio
from types import SimpleNamespace

from roma.core.telemetry import Telemetry, set_telemetry
from roma.domain.knowledge.retrieval import RetrievalFilters
from roma.domain.safety.audit import SafetyContext
from roma.providers.ai.mocks import MockEmbeddingProvider
from roma.services.knowledge_answer import KnowledgeAnswerService
from roma.services.knowledge_retrieval import KnowledgeSearchService
from roma.services.safety_service import SafetyService


def test_real_retrieval_answer_and_safety_observations_exclude_query_and_context():
    async def empty_search(**kwargs):
        return ()

    async def run():
        telemetry = Telemetry()
        previous = set_telemetry(telemetry)
        try:
            search = KnowledgeSearchService(SimpleNamespace(search_chunks=empty_search), MockEmbeddingProvider())
            await search.search('SEO caller@example.invalid?', RetrievalFilters('course', 'hi-en'))
            await KnowledgeAnswerService(search, None).answer('Hello', RetrievalFilters('course', 'hi-en'))
            await SafetyService().check('Fees 50000 hain.', SafetyContext('private-call', 1, 'value'))
            body = telemetry.render()
            for stage in ('rag', 'rag_embedding', 'rag_answer', 'safety'):
                assert f'stage="{stage}"' in body
            assert 'event="rag_route"' in body and 'event="safety"' in body
            assert 'private-call' not in body and 'caller@example.invalid' not in body
            assert '50000' not in body
        finally:
            set_telemetry(previous)

    asyncio.run(run())


def test_actual_queue_checkpoint_lease_deadletter_and_plc_publish_bounded_metrics():
    from fakeredis.aioredis import FakeRedis
    from roma.core.async_queue import BoundedChannel
    from roma.domain.audio.adaptive_jitter import AdaptiveJitterBuffer
    from roma.domain.conversation.state import CallState
    from roma.repositories.redis.call_lease import RedisCallLease
    from roma.repositories.redis.postcall_queue import InMemoryPostcallQueue
    from roma.services.live_call_state import LiveCallStateStore
    from roma.workers.postcall.job import PostcallJob
    from tests.domain.audio.test_adaptive_jitter import packet
    from tests.services.test_live_call_state import MemoryStore

    async def run():
        telemetry = Telemetry()
        previous = set_telemetry(telemetry)
        redis = FakeRedis()
        try:
            q = BoundedChannel('audio', capacity=1, high=1, low=0)
            other = BoundedChannel('audio', capacity=1, high=1, low=0)
            await q.put('private@example.invalid')
            await other.put('another private payload')
            assert not await q.put('drop private')
            assert 'roma_queue_depth{queue="audio"} 2' in telemetry.render()
            await q.get()
            await other.close(discard=True)
            writer = LiveCallStateStore(MemoryStore(), MemoryStore())
            await writer.save(CallState(call_sid='private-observability-test'))
            lease = RedisCallLease(redis, 'private-observability-test')
            assert await lease.acquire()
            assert not await RedisCallLease(redis, 'private-observability-test').acquire()
            # fakeredis without the optional Lua runtime cannot EVAL owner release.
            # Owner-safe release is covered against disposable native Redis separately.
            job = PostcallJob('private-observability-test', None, None, '', '', 0, 0, 'no_lock')
            jobs = InMemoryPostcallQueue()
            await jobs.push(job)
            await jobs.dead(await jobs.reserve())
            buffer = AdaptiveJitterBuffer(clock=lambda: 0.)
            buffer.push(*packet(1))
            buffer.flush(last_seq_no=3)
            body = telemetry.render()
            assert 'roma_queue_depth{queue="audio"} 0' in body
            assert 'roma_queue_drops_total{queue="audio"} 1' in body
            for event in ('queue_drop', 'checkpoint', 'lock', 'dead_letter', 'plc'):
                assert f'event="{event}"' in body
            assert 'event="lock",outcome="rejected"' in body
            assert 'stage="checkpoint"' in body and 'stage="lock"' in body
            assert 'private' not in body
        finally:
            await redis.aclose()
            set_telemetry(previous)
    asyncio.run(run())


def test_actual_retrieval_log_contains_reviewed_route_evidence_ids_and_measured_latency(monkeypatch):
    from roma.core.logging import LogEvent
    from tests.services.test_knowledge_retrieval import Repository
    observed = []
    monkeypatch.setattr('roma.services.knowledge_retrieval.log_event',
                        lambda event, **fields: observed.append((event, fields)))
    async def run():
        repo = Repository()
        result = await KnowledgeSearchService(repo, MockEmbeddingProvider()).search(
            'SEO details caller@example.invalid', RetrievalFilters('course', 'hi-en'))
        event, fields = observed[0]
        assert event == LogEvent.KNOWLEDGE_ROUTED
        assert fields['route'] == 'knowledge'
        assert fields['chunk_ids'] == [str(repo.evidence.chunk_id)]
        assert fields['latency_ms'] == result.latency_ms
        assert set(fields) == {'route', 'chunk_ids', 'latency_ms'}
        assert 'caller@example.invalid' not in repr(observed)
    asyncio.run(run())
