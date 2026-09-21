import asyncio

from pydantic import SecretStr
from roma.core.config import Settings
from roma.core.database import Database


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        sarvam_api_key=SecretStr("test-sarvam"),
        openai_api_key=SecretStr("test-openai"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        database_url=SecretStr("postgresql+asyncpg://roma:secret@localhost:5432/roma"),
        database_pool_size=3,
        database_max_overflow=4,
        database_pool_timeout_secs=2.5,
    )


def test_database_from_settings_builds_secret_safe_async_lifecycle():
    database = Database.from_settings(_settings())

    async def close_database() -> None:
        await database.close()

    try:
        assert "secret" not in repr(database)
        assert "secret" not in str(database.engine.url)
        assert database.engine.echo is False
        assert database.engine.sync_engine.pool._pre_ping is True
        assert database.engine.sync_engine.pool.size() == 3
        assert database.engine.sync_engine.pool._max_overflow == 4
        assert database.engine.sync_engine.pool._timeout == 2.5
        assert database.session_factory.kw["expire_on_commit"] is False
    finally:
        asyncio.run(close_database())
