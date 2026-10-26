import asyncio
from uuid import uuid4

import pytest
from roma.providers.jobs.knowledge import ACTOR_NAME, QUEUE_NAME, KnowledgePublisher
from roma.workers.knowledge import create_knowledge_actor, process_knowledge_job, run_pending


class Service:
    def __init__(self):
        self.ids = []

    async def process(self, job_id):
        self.ids.append(job_id)
        return "succeeded"


def test_worker_accepts_only_uuid_delivery_and_drains_durable_ids():
    async def scenario():
        service = Service()
        job_id = uuid4()
        assert await process_knowledge_job(service, str(job_id)) == "succeeded"
        with pytest.raises(ValueError):
            await process_knowledge_job(service, "caller transcript")

        class Repository:
            async def dispatchable_ids(self, now, limit):
                return (job_id,)

        assert await run_pending(service, Repository(), limit=1) == 1
        assert service.ids == [job_id, job_id]

    asyncio.run(scenario())


def test_dramatiq_publisher_contains_only_job_uuid():
    class Broker:
        def __init__(self):
            self.queues, self.messages = [], []

        def declare_queue(self, queue):
            self.queues.append(queue)

        def enqueue(self, message):
            self.messages.append(message)

    async def scenario():
        broker, job_id = Broker(), uuid4()
        publisher = KnowledgePublisher(broker)
        await publisher.publish(job_id)
        message = broker.messages[0]
        assert message.queue_name == QUEUE_NAME and message.actor_name == ACTOR_NAME
        assert message.args == (str(job_id),) and message.kwargs == {}
        with pytest.raises(ValueError):
            await publisher.publish("document content")

    asyncio.run(scenario())


def test_actor_runs_fake_service_with_real_framework_delivery():
    from dramatiq import Worker
    from dramatiq.brokers.stub import StubBroker
    from dramatiq.middleware import AsyncIO

    service, job_id = Service(), uuid4()
    broker = StubBroker(middleware=[AsyncIO()])
    actor = create_knowledge_actor(broker, lambda: service)
    actor.send(str(job_id))
    actor.send("malformed")
    worker = Worker(broker, worker_threads=1, worker_timeout=20)
    worker.start()
    try:
        broker.join(QUEUE_NAME, timeout=5000)
        worker.join()
        assert service.ids == [job_id]
    finally:
        worker.stop(timeout=2000)
        broker.close()


def test_cli_requires_explicit_database_write_and_only_runs_bounded_drain(monkeypatch):
    from roma.workers import knowledge

    seen = []

    async def drain(*, limit):
        seen.append(limit)
        return 2

    monkeypatch.setattr(knowledge, "run_database_drain", drain)
    with pytest.raises(SystemExit):
        knowledge.main(["--drain"])
    assert not seen
    assert knowledge.main(["--drain", "--limit", "2", "--allow-database-write"]) == 0
    assert seen == [2]


def test_framework_retries_do_not_log_provider_exception_bodies(caplog):
    from dramatiq import Worker
    from dramatiq.brokers.stub import StubBroker
    from dramatiq.middleware import AsyncIO, Retries

    def broken():
        raise RuntimeError("private@example.test provider-secret")

    broker = StubBroker(middleware=[AsyncIO(), Retries(max_retries=0)])
    actor = create_knowledge_actor(broker, broken)
    actor.send(str(uuid4()))
    worker = Worker(broker, worker_threads=1, worker_timeout=20)
    worker.start()
    try:
        broker.join(QUEUE_NAME, timeout=5000, fail_fast=False)
        worker.join()
        assert len(broker.dead_letters) == 1
        assert "private@example.test" not in caplog.text
        assert "provider-secret" not in caplog.text
    finally:
        worker.stop(timeout=2000)
        broker.close()


def test_cli_invalid_limit_never_opens_database_and_failure_output_is_sanitized(
    monkeypatch, capsys
):
    from roma.workers import knowledge

    seen = []

    async def unavailable(*, limit):
        seen.append(limit)
        raise RuntimeError("synthetic private connection secret")

    monkeypatch.setattr(knowledge, "run_database_drain", unavailable)
    with pytest.raises(SystemExit):
        knowledge.main(["--drain", "--allow-database-write", "--limit", "1001"])
    assert not seen
    assert knowledge.main(["--drain", "--allow-database-write", "--limit", "1"]) == 1
    output = capsys.readouterr()
    assert "Knowledge worker unavailable" in output.out
    assert "private connection secret" not in output.out + output.err


@pytest.mark.parametrize("fail_close", [False, True])
def test_database_drain_closes_provider_and_engine_without_opening_real_services(
    monkeypatch, fail_close
):
    from types import SimpleNamespace

    import roma.core.migration_settings
    import roma.core.rag_settings
    import roma.services.knowledge_runtime
    import sqlalchemy.ext.asyncio
    from roma.workers.knowledge import run_database_drain

    events = []
    job_id = uuid4()

    class Engine:
        async def dispose(self):
            events.append("engine_closed")

    class Provider:
        async def aclose(self):
            events.append("provider_closed")
            if fail_close:
                raise RuntimeError("synthetic close failure")

    class Repository:
        async def dispatchable_ids(self, now, limit):
            assert limit == 1
            return (job_id,)

    service = Service()
    components = SimpleNamespace(
        provider=Provider(), ingestion=service, ingestion_repository=Repository()
    )
    monkeypatch.setattr(
        sqlalchemy.ext.asyncio, "create_async_engine", lambda *args, **kwargs: Engine()
    )
    monkeypatch.setattr(
        sqlalchemy.ext.asyncio, "async_sessionmaker", lambda *args, **kwargs: "fake-factory"
    )
    monkeypatch.setattr(
        roma.core.migration_settings,
        "MigrationSettings",
        lambda: SimpleNamespace(require_url=lambda: "fake-url"),
    )
    monkeypatch.setattr(
        roma.core.rag_settings, "KnowledgeRuntimeSettings", lambda: "fake-settings"
    )

    def build(factory, settings):
        assert factory == "fake-factory" and settings == "fake-settings"
        return components

    monkeypatch.setattr(roma.services.knowledge_runtime, "build_knowledge_components", build)

    async def scenario():
        if fail_close:
            with pytest.raises(RuntimeError, match="synthetic close failure"):
                await run_database_drain(limit=1)
        else:
            assert await run_database_drain(limit=1) == 1
        assert service.ids == [job_id]
        assert events == ["provider_closed", "engine_closed"]

    asyncio.run(scenario())
