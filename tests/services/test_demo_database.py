"""Only native disposable PostgreSQL; never configured working DATABASE_URL."""
import asyncio
from datetime import datetime

import pytest
from roma.domain.appointments.timeresolve import IST
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.parametrize('url,env', [
    ('postgresql+asyncpg://roma@192.0.2.1/lab', 'dev'),
    ('postgresql+asyncpg://roma@127.0.0.1/roma', 'dev'),
    ('postgresql+asyncpg://roma@127.0.0.1/%72oma', 'dev'),
    ('postgresql+asyncpg://roma@127.0.0.1/lab', 'prod'),
    ('postgresql+asyncpg://roma@127.0.0.1/lab', 'staging'),
])
def test_demo_target_refuses_remote_working_or_non_development_databases(url, env):
    from roma.repositories.postgres.demo_database import validate_demo_target
    with pytest.raises(ValueError):
        validate_demo_target(url, env)


def test_demo_database_native_lifecycle_migrations_and_future_test_only_data(tmp_path):
    from roma.repositories.postgres.demo_database import start_demo, stop_demo
    from roma.repositories.postgres.models import AppointmentSlot, Branch, Course, Institute
    from sqlalchemy import text
    root = tmp_path / 'demo'
    url = start_demo(root, app_env='test')
    try:
        assert '/roma_demo' in url and '127.0.0.1' in url
        async def check():
            engine = create_async_engine(url)
            try:
                async with async_sessionmaker(engine)() as session:
                    assert await session.scalar(text('SELECT version_num FROM alembic_version')) == '20261024_0006'
                    for model in (Institute, Branch, Course):
                        rows = (await session.scalars(select(model))).all()
                        assert rows and all(row.code.startswith('TEST_ONLY') for row in rows)
                    assert await session.scalar(select(func.count()).select_from(AppointmentSlot)) > 0
                    slots = (await session.scalars(select(AppointmentSlot))).all()
                    assert all(slot.appointment_date > datetime.now(IST).date() for slot in slots)
            finally:
                await engine.dispose()
        asyncio.run(check())
    finally:
        stop_demo(root, app_env='test')
    assert not root.exists()


def test_demo_stop_refuses_unowned_or_symlinked_directory(tmp_path):
    from roma.repositories.postgres.demo_database import stop_demo
    root = tmp_path / 'unowned'
    root.mkdir()
    (root / 'preserve').write_text('preserve')
    with pytest.raises(ValueError):
        stop_demo(root, app_env='test')
    assert (root / 'preserve').exists()


def test_booking_readiness_rejects_empty_and_wrong_branch_and_accepts_seeded_db():
    from roma.repositories.postgres.demo_database import migrate_demo, seed_voice_demo
    from roma.repositories.postgres.voice_lab import require_booking_data
    from tests.repositories.postgres.test_migrations import _local_postgres_url

    with _local_postgres_url() as url:
        migrate_demo(url, app_env='test')
        async def check():
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine)
            try:
                with pytest.raises(RuntimeError, match=r'^BOOKING-DATA-MISSING:'):
                    await require_booking_data(factory, branch='Vadodara')
                await seed_voice_demo(url, app_env='test')
                counts = await require_booking_data(factory, branch='Vadodara')
                assert counts == {'branches': 1, 'slots': 9}
                with pytest.raises(RuntimeError, match=r'^BOOKING-DATA-MISSING:'):
                    await require_booking_data(factory, branch='Ahmedabad')
            finally:
                await engine.dispose()
        asyncio.run(check())


def test_empty_booking_db_stops_cli_before_model_readiness_mic_or_lab_call(monkeypatch):
    from types import SimpleNamespace

    import scripts.voice_prototype as cli
    from roma.core.speech_settings import SpeechSettings
    from roma.core.tts_settings import TTSSettings
    from roma.repositories.postgres.demo_database import migrate_demo
    from tests.repositories.postgres.test_migrations import _local_postgres_url

    with _local_postgres_url() as url:
        migrate_demo(url, app_env='test')
        monkeypatch.setattr(cli, 'local_preflight', lambda args: (
            SpeechSettings(_env_file=None), None, TTSSettings(_env_file=None)))
        monkeypatch.setattr(cli, 'MigrationSettings', lambda: SimpleNamespace(require_url=lambda: url))
        monkeypatch.setattr(cli, 'build_stt_provider', lambda cfg: pytest.fail('models reached'))
        monkeypatch.setattr(cli.PostgresVoiceLabStore, 'start', lambda *a: pytest.fail('lab call reached'))
        monkeypatch.setattr(cli, 'LocalAudioDevice', lambda **kw: pytest.fail('mic reached'))
        async def close():
            pass
        async def ping():
            pytest.fail('Redis readiness reached before booking readiness')
        monkeypatch.setattr('redis.asyncio.Redis.from_url', lambda *a, **kw: SimpleNamespace(aclose=close, ping=ping))
        args = cli.parser().parse_args(['--mode', 'local', '--llm-provider', 'mock'])
        with pytest.raises(RuntimeError, match=r'^BOOKING-DATA-MISSING:'):
            asyncio.run(cli.local_run(args))
