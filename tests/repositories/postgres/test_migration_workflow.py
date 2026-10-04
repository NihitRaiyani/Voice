"""Developer migration history, offline review and database-only execution."""

from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from tests.repositories.postgres.test_migrations import (
    disposable_postgres_url as postgres_fixture,
)

__all__ = ["disposable_postgres_url"]


@pytest.fixture(scope="module")
def disposable_postgres_url():
    # Reuse Docker/native provisioning, but release this module's server promptly.
    # Extra session-scoped servers can exhaust macOS shared-memory IDs.
    yield from postgres_fixture.__wrapped__()


def test_existing_history_is_linear_and_offline_sql_contains_schema_indexes_only():
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "postgresql://demo@localhost/roma")
    directory = ScriptDirectory.from_config(config)
    assert directory.get_heads() == ["20261004_0003"]
    assert [revision.revision for revision in directory.walk_revisions()] == [
        "20261004_0003",
        "20260930_0002",
        "20260920_0001",
    ]
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE benchmark_results" in sql
    assert "CREATE INDEX ix_benchmark_results_model_id_language_metric" in sql
    assert "roma-demo" not in sql
    assert "INSERT INTO institutes" not in sql


@pytest.mark.postgres
def test_environment_url_runs_upgrades_checks_and_rollback_without_cloud_keys(
    disposable_postgres_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", disposable_postgres_url)
    for name in ("OPENAI_API_KEY", "SARVAM_API_KEY", "TWILIO_AUTH_TOKEN", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "20260930_0002")
        command.upgrade(config, "head")
        command.check(config)
    finally:
        command.downgrade(config, "base")
