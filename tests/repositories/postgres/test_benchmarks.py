"""Validation is offline; PostgreSQL cases use only the disposable test cluster."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from roma.domain.persistence import PersistenceConflict
from roma.repositories.postgres.benchmarks import (
    AggregateMetric,
    BenchmarkEvidence,
    FailureArtifact,
    ModelEvidence,
    PostgresBenchmarkStore,
    _prepare,
)
from roma.repositories.postgres.models import BenchmarkResult, BenchmarkRun, ModelRegistry
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.repositories.postgres.test_migrations import (
    disposable_postgres_url as _provision_disposable_postgres_url,
)

# Reuse the shared provisioning/stop generator, but release this module's cluster
# immediately after its tests instead of retaining another cluster until session end.
disposable_postgres_url = pytest.fixture(scope="module")(
    _provision_disposable_postgres_url.__wrapped__
)


@pytest.fixture(scope="module")
def benchmark_database(disposable_postgres_url):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_url)
    command.upgrade(config, "head")
    # Every test uses asyncio.run; avoid sharing pooled connections across loops.
    engine = create_async_engine(disposable_postgres_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())
        command.downgrade(config, "base")


@pytest.fixture()
def session_factory(benchmark_database):
    async def clear_benchmarks():
        async with benchmark_database() as session, session.begin():
            await session.execute(delete(BenchmarkRun))
            await session.execute(delete(ModelRegistry))

    asyncio.run(clear_benchmarks())
    try:
        yield benchmark_database
    finally:
        asyncio.run(clear_benchmarks())


NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


def evidence(**changes):
    model = ModelEvidence("synthetic", f"asr-{uuid4()}", "revision-1")
    metric = AggregateMetric(
        model.key,
        "gu-IN",
        "wer.clean.public",
        Decimal("0.125"),
        "ratio",
        2,
        {"variant": "clean", "corpus_kind": "public"},
    )
    base = BenchmarkEvidence(
        run_key=str(uuid4()),
        dataset_name="synthetic-public-fixture",
        dataset_version="v1",
        dataset_sha256="a" * 64,
        status="succeeded",
        sample_count=2,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        models=(model,),
        metrics=(metric,),
        config={"normalizer": "indic-v1"},
        environment={"execution": "offline-fake"},
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_key": " "},
        {"dataset_sha256": "bad"},
        {"status": "running"},
        {"sample_count": 0},
        {"sample_count": True},
        {"started_at": NOW.replace(tzinfo=None)},
        {"ended_at": NOW - timedelta(seconds=1)},
        {"models": ()},
        {"metrics": ()},
        {"environment": {"token": "never-store"}},
        {"config": {"nested": {"hypothesis": "private"}}},
        {"config": {"value": float("nan")}},
        {"status": "failed"},
    ],
)
def test_invalid_evidence_rejected_before_opening_a_session(changes):
    def no_session():
        pytest.fail("Invalid evidence must not acquire a SQL session")

    with pytest.raises(ValueError):
        asyncio.run(PostgresBenchmarkStore(no_session).save(evidence(**changes)))


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        Decimal("-Infinity"),
        Decimal("1000000000000"),
        Decimal("999999999999.999999999"),
        True,
    ],
)
def test_invalid_metrics_are_rejected(value):
    item = evidence()
    with pytest.raises(ValueError):
        _prepare(replace(item, metrics=(replace(item.metrics[0], value=value),)))


def test_duplicate_or_unknown_metric_identity_rejected():
    item = evidence()
    with pytest.raises(ValueError, match="Duplicate metric"):
        _prepare(replace(item, metrics=item.metrics * 2))
    with pytest.raises(ValueError, match="undeclared"):
        _prepare(
            replace(item, metrics=(replace(item.metrics[0], model_key=("x", "x", "x", "stt")),))
        )
    with pytest.raises(ValueError, match="Duplicate benchmark model"):
        _prepare(replace(item, models=item.models * 2))


def test_failure_identity_metadata_precision_and_detached_config():
    item = evidence(
        status="failed",
        failure_artifacts=(FailureArtifact("var/roma/lab/failures.json", "b" * 64),),
    )
    item = replace(item, metrics=(replace(item.metrics[0], value=Decimal("-0.123456789")),))
    run, _, metrics = _prepare(item)
    assert metrics[0]["value"] == Decimal("-0.12345679")
    stored = run["config"]["roma_benchmark_evidence"]
    assert stored["failure_artifacts"] == [
        {"path": "var/roma/lab/failures.json", "sha256": "b" * 64}
    ]
    assert "roma_benchmark_evidence" not in item.config
    changed, _, _ = _prepare(replace(item, environment={"execution": "different"}))
    assert stored["sha256"] != changed["config"]["roma_benchmark_evidence"]["sha256"]
    with pytest.raises(ValueError, match="credentials"):
        _prepare(
            replace(
                item,
                failure_artifacts=(
                    FailureArtifact("https://host/report?token=secret", "b" * 64),
                ),
            )
        )


@pytest.mark.postgres
def test_atomic_save_idempotency_and_conflicting_evidence(session_factory):
    async def run():
        store = PostgresBenchmarkStore(session_factory)
        item = evidence()
        first = await store.save(item)
        second = await store.save(item)
        assert first.created and not second.created and first.run_id == second.run_id
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(BenchmarkRun)) == 1
            assert await session.scalar(select(func.count()).select_from(BenchmarkResult)) == 1
            assert await session.scalar(select(func.count()).select_from(ModelRegistry)) == 1
            metric = await session.scalar(select(BenchmarkResult))
            assert metric.value == Decimal("0.12500000")
        for changes in (
            {"dataset_sha256": "b" * 64},
            {"environment": {"execution": "different"}},
            {"metrics": (replace(item.metrics[0], value=0.5),)},
            {"failure_artifacts": (FailureArtifact("var/roma/lab/failures.json", "c" * 64),)},
        ):
            with pytest.raises(PersistenceConflict, match="different evidence"):
                await store.save(replace(item, **changes))

    asyncio.run(run())


@pytest.mark.postgres
def test_model_provenance_conflict_rolls_back_the_entire_new_run(session_factory):
    async def run():
        store = PostgresBenchmarkStore(session_factory)
        item = evidence()
        await store.save(item)
        changed_model = replace(item.models[0], checksum_sha256="d" * 64)
        with pytest.raises(PersistenceConflict, match="model identity"):
            await store.save(replace(item, run_key=str(uuid4()), models=(changed_model,)))
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(BenchmarkRun)) == 1
            assert await session.scalar(select(func.count()).select_from(BenchmarkResult)) == 1

    asyncio.run(run())


@pytest.mark.postgres
def test_concurrent_retries_create_exactly_one_run_and_metric(session_factory):
    async def run():
        store = PostgresBenchmarkStore(session_factory)
        item = evidence()
        saved = await asyncio.gather(*(store.save(item) for _ in range(6)))
        assert len({result.run_id for result in saved}) == 1
        assert sum(result.created for result in saved) == 1
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(BenchmarkResult)) == 1

    asyncio.run(run())


def test_retry_identity_ignores_model_metric_and_artifact_order():
    item = evidence()
    second_model = replace(item.models[0], name="other-model")
    second_metric = replace(item.metrics[0], model_key=second_model.key)
    item = replace(
        item,
        models=(*item.models, second_model),
        metrics=(*item.metrics, second_metric),
        failure_artifacts=(
            FailureArtifact("var/roma/a.json", "b" * 64),
            FailureArtifact("var/roma/b.json", "c" * 64),
        ),
    )
    reordered = replace(
        item,
        models=item.models[::-1],
        metrics=item.metrics[::-1],
        failure_artifacts=item.failure_artifacts[::-1],
    )
    first, _, _ = _prepare(item)
    second, _, _ = _prepare(reordered)
    assert (
        first["config"]["roma_benchmark_evidence"]
        == second["config"]["roma_benchmark_evidence"]
    )


@pytest.mark.postgres
def test_failed_run_preserves_artifact_without_fabricated_measurements(session_factory):
    async def run():
        item = evidence(
            status="failed",
            metrics=(),
            failure_artifacts=(FailureArtifact("var/roma/failures.json", "f" * 64),),
        )
        saved = await PostgresBenchmarkStore(session_factory).save(item)
        async with session_factory() as session:
            stored = await session.get(BenchmarkRun, saved.run_id)
            assert stored.status == "failed"
            assert stored.config["roma_benchmark_evidence"]["failure_artifacts"] == [
                {"path": "var/roma/failures.json", "sha256": "f" * 64}
            ]
            assert await session.scalar(select(func.count()).select_from(BenchmarkResult)) == 0

    asyncio.run(run())


@pytest.mark.parametrize("retain", [False, True])
def test_voice_lab_short_turn_writes_reuse_safety_anchor_and_preserve_privacy(
    session_factory, retain
):
    from roma.domain.safety.audit import SafetyContext, check_sentence
    from roma.repositories.postgres.models import Call, CallEvent, CallTurn, SafetyEvent
    from roma.repositories.postgres.safety import PostgresSafetyEventStore
    from roma.repositories.postgres.voice_lab import PostgresVoiceLabStore

    async def run():
        sid = "voice-lab-" + uuid4().hex
        store = PostgresVoiceLabStore(session_factory, persist_transcripts=retain)
        await store.start(sid)
        _, _, events = check_sentence("fees 50000")
        await PostgresSafetyEventStore(session_factory).record(
            SafetyContext(sid, 1, "discover"), events
        )
        record = {
            "attempt": 1,
            "turn_number": 1,
            "status": "played",
            "stage": "discover",
            "language": "hi",
            "caller": "PRIVATE synthetic caller words",
            "agent": "approved synthetic agent words",
            "timings": {"asr_ms": 2.0, "llm_ms": 3.0, "response_total_ms": 10.0},
            "error_code": None,
        }
        await store.save(sid, record)
        await store.save(sid, record)
        async with session_factory() as session:
            call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
            turns = list(
                await session.scalars(select(CallTurn).where(CallTurn.call_id == call.id))
            )
            logs = list(
                await session.scalars(select(CallEvent).where(CallEvent.call_id == call.id))
            )
            safety = await session.get(SafetyEvent, events[0].id)
            assert len(turns) == len(logs) == 1 and safety.turn_id == turns[0].id
            assert (
                turns[0].latency_ms == 10 and turns[0].retention_until == call.retention_until
            )
            assert turns[0].transcript == (record["agent"] if retain else None)
            assert ("PRIVATE" in str(logs[0].payload)) is retain
        await store.finish(sid)
        with pytest.raises(ValueError, match="Missing lab call"):
            await store.save(sid, record)
        async with session_factory() as session:
            call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
            assert call.status == "completed" and call.ended_at is not None
            await session.delete(call)
            await session.commit()

    asyncio.run(run())


def test_voice_store_rejects_private_or_invalid_metric_keys_before_database():
    from roma.repositories.postgres.voice_lab import PostgresVoiceLabStore

    class NoDatabase:
        def __call__(self):
            pytest.fail("Invalid evidence opened a database session")

    async def run():
        for timings in ({"secret-transcript": 1}, {"asr_ms": float("nan")}, {"asr_ms": -1}):
            with pytest.raises(ValueError, match="timings"):
                await PostgresVoiceLabStore(NoDatabase()).save(
                    "voice-lab-test", {"timings": timings}
                )

    asyncio.run(run())
