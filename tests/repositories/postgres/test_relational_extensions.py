"""Real constraint, retention, provenance and additive-migration verification."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from roma.domain.persistence import CallTurnRecord
from roma.repositories.postgres.models import (
    BenchmarkResult,
    BenchmarkRun,
    Call,
    ConversationState,
    ModelRegistry,
)
from roma.repositories.postgres.unit_of_work import PostgresUnitOfWork
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tests.repositories.postgres.test_migrations import alembic_config, disposable_postgres_url
from tests.repositories.postgres.test_repositories import session_factory

__all__ = ["alembic_config", "disposable_postgres_url", "session_factory"]
pytestmark = pytest.mark.postgres
NOW = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)
NEW_TABLES = {"conversation_states", "model_registry", "benchmark_runs", "benchmark_results"}


async def _graph(session):
    call = Call(id=uuid4(), direction="inbound", status="in_progress", started_at=NOW)
    model = ModelRegistry(
        id=uuid4(), provider="local", name=f"lab-{uuid4()}", version="revision-1", task="stt"
    )
    run = BenchmarkRun(
        id=uuid4(),
        run_key=str(uuid4()),
        dataset_name="synthetic",
        dataset_version="v1",
        created_at=NOW,
    )
    session.add_all([call, model, run])
    await session.flush()
    state = ConversationState(
        id=uuid4(),
        call_id=call.id,
        policy_version="roma-v4-hinglish-1",
        conversation_stage="open",
        state={"stage": "open"},
        created_at=NOW,
        retention_until=NOW + timedelta(days=1),
    )
    result = BenchmarkResult(
        id=uuid4(),
        run_id=run.id,
        model_id=model.id,
        language="hi-IN",
        metric="wer",
        value=Decimal("0.12345678"),
        unit="ratio",
        sample_count=20,
    )
    session.add_all([state, result])
    await session.flush()
    return {"call": call, "state": state, "model": model, "run": run, "result": result}


def test_checkpoint_and_benchmark_records_round_trip_and_turn_language_is_optional(
    session_factory,
):
    async def run():
        async with session_factory() as session, session.begin():
            graph = await _graph(session)
            state_id, result_id, call_id = (
                graph["state"].id,
                graph["result"].id,
                graph["call"].id,
            )
        async with session_factory() as session:
            state = await session.get(ConversationState, state_id)
            assert state.schema_version == state.revision == 1
            assert state.state == {"stage": "open"}
            assert state.retention_until == NOW + timedelta(days=1)
            result = await session.get(BenchmarkResult, result_id)
            assert result.value == Decimal("0.12345678")
        async with PostgresUnitOfWork(session_factory) as uow:
            for number, language in [(1, None), (2, "gu-IN")]:
                record = await uow.calls.add_turn(
                    CallTurnRecord(
                        id=uuid4(),
                        call_id=call_id,
                        turn_number=number,
                        speaker="caller",
                        created_at=NOW,
                        language=language,
                    )
                )
                assert record.language == language
            await uow.commit()
        async with PostgresUnitOfWork(session_factory) as uow:
            assert [r.language for r in await uow.calls.list_turns(call_id)] == [None, "gu-IN"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("entity", "changes", "constraint"),
    [
        ("state", {"revision": 0}, "revision_positive"),
        ("state", {"schema_version": 0}, "schema_version_positive"),
        ("state", {"policy_version": " "}, "policy_version_nonblank"),
        ("state", {"conversation_stage": "unknown"}, "conversation_stage"),
        ("state", {"state": []}, "state_object"),
        ("state", {"retention_until": NOW}, "retention_after_creation"),
        ("model", {"task": "unknown"}, "task"),
        ("model", {"version": " "}, "identity_nonblank"),
        ("model", {"checksum_sha256": "invalid"}, "checksum_sha256"),
        ("run", {"run_key": " "}, "identity_nonblank"),
        ("run", {"dataset_sha256": "invalid"}, "dataset_sha256"),
        ("run", {"sample_count": -1}, "sample_count_nonnegative"),
        ("run", {"status": "running"}, "started_when_executing"),
        (
            "run",
            {"status": "succeeded", "started_at": NOW, "ended_at": NOW},
            "succeeded_has_samples",
        ),
        ("run", {"status": "failed"}, "terminal_has_end"),
        ("run", {"ended_at": NOW}, "terminal_has_end"),
        (
            "run",
            {"status": "failed", "started_at": NOW, "ended_at": NOW - timedelta(seconds=1)},
            "time_order",
        ),
        ("run", {"config": []}, "config_object"),
        ("run", {"environment": []}, "environment_object"),
        ("run", {"retention_until": NOW}, "retention_after_creation"),
        ("result", {"value": Decimal("NaN")}, "value_finite"),
        ("result", {"sample_count": 0}, "sample_count_positive"),
        ("result", {"language": " "}, "measurement_nonblank"),
        ("result", {"metadata_": []}, "metadata_object"),
    ],
)
def test_new_checks_reject_invalid_records_at_the_database(
    session_factory, entity, changes, constraint
):
    async def run():
        async with session_factory() as session:
            graph = await _graph(session)
            for key, value in changes.items():
                setattr(graph[entity], key, value)
            with pytest.raises(IntegrityError) as error:
                await session.flush()
            assert constraint in str(error.value.orig)
            await session.rollback()

    asyncio.run(run())


@pytest.mark.parametrize("entity", ["state", "model", "run", "result"])
def test_business_identity_prevents_duplicates(session_factory, entity):
    async def run():
        async with session_factory() as session:
            first, second = await _graph(session), await _graph(session)
            fields = {
                "state": ["call_id"],
                "model": ["provider", "name", "version", "task"],
                "run": ["run_key"],
                "result": ["run_id", "model_id", "language", "metric"],
            }[entity]
            for field in fields:
                setattr(second[entity], field, getattr(first[entity], field))
            with pytest.raises(IntegrityError) as error:
                await session.flush()
            assert "uq_" in str(error.value.orig)
            await session.rollback()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("entity", "field"), [("state", "call_id"), ("result", "model_id"), ("result", "run_id")]
)
def test_new_foreign_keys_reject_orphaned_rows(session_factory, entity, field):
    async def run():
        async with session_factory() as session:
            graph = await _graph(session)
            setattr(graph[entity], field, uuid4())
            with pytest.raises(IntegrityError) as error:
                await session.flush()
            assert "fk_" in str(error.value.orig)
            await session.rollback()

    asyncio.run(run())


def test_owned_data_cascades_but_measured_model_deletion_is_restricted(session_factory):
    async def run():
        async with session_factory() as session, session.begin():
            graph = await _graph(session)
            call_id, model_id, run_id, state_id, result_id = (
                graph[k].id for k in ["call", "model", "run", "state", "result"]
            )
        async with session_factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(delete(ModelRegistry).where(ModelRegistry.id == model_id))
            await session.rollback()
        async with session_factory() as session, session.begin():
            await session.execute(delete(Call).where(Call.id == call_id))
            assert await session.get(ConversationState, state_id) is None
            assert await session.get(BenchmarkResult, result_id) is not None
            await session.execute(delete(BenchmarkRun).where(BenchmarkRun.id == run_id))
            session.expire_all()
            assert await session.get(BenchmarkResult, result_id) is None
            assert await session.get(ModelRegistry, model_id) is not None
            await session.execute(delete(ModelRegistry).where(ModelRegistry.id == model_id))

    asyncio.run(run())


def test_successful_runs_and_signed_metrics_have_valid_semantics(session_factory):
    async def run():
        async with session_factory() as session, session.begin():
            graph = await _graph(session)
            graph["run"].status = "succeeded"
            graph["run"].sample_count = 20
            graph["run"].started_at = NOW
            graph["run"].ended_at = NOW + timedelta(seconds=1)
            graph["result"].metric = "correlation"
            graph["result"].value = Decimal("-0.25")
            await session.flush()

    asyncio.run(run())


def test_additive_upgrade_and_downgrade_preserve_legacy_data(
    alembic_config, disposable_postgres_url
):
    call_id, turn_id = uuid4(), uuid4()

    async def seed_legacy():
        engine = create_async_engine(disposable_postgres_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO calls (id, direction, status, started_at) "
                        "VALUES (:id, 'inbound', 'in_progress', :now)"
                    ),
                    {"id": call_id, "now": NOW},
                )
                await connection.execute(
                    text(
                        "INSERT INTO call_turns (id, call_id, turn_number, speaker) "
                        "VALUES (:id, :call, 1, 'caller')"
                    ),
                    {"id": turn_id, "call": call_id},
                )
        finally:
            await engine.dispose()

    async def check(upgraded):
        engine = create_async_engine(disposable_postgres_url)
        try:
            async with engine.connect() as connection:
                tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
                assert (NEW_TABLES <= tables) if upgraded else not (NEW_TABLES & tables)
                row = (
                    await connection.execute(
                        text(
                            "SELECT id, call_id, turn_number, speaker "
                            "FROM call_turns WHERE id = :id"
                        ),
                        {"id": turn_id},
                    )
                ).one()
                assert tuple(row) == (turn_id, call_id, 1, "caller")
                columns = await connection.run_sync(
                    lambda c: {r["name"] for r in inspect(c).get_columns("call_turns")}
                )
                assert ("language" in columns) == upgraded
                if upgraded:
                    assert (
                        await connection.execute(
                            text("SELECT language FROM call_turns WHERE id = :id"),
                            {"id": turn_id},
                        )
                    ).scalar_one() is None
        finally:
            await engine.dispose()

    command.upgrade(alembic_config, "20260930_0002")
    asyncio.run(seed_legacy())
    try:
        command.upgrade(alembic_config, "head")
        asyncio.run(check(True))
        command.check(alembic_config)
        command.downgrade(alembic_config, "20260930_0002")
        asyncio.run(check(False))
        command.upgrade(alembic_config, "head")
        asyncio.run(check(True))
    finally:
        command.downgrade(alembic_config, "base")
