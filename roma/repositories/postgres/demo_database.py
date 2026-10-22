"""Explicit, disposable native voice-demo database. Never reads DATABASE_URL."""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from roma.domain.appointments.timeresolve import IST
from roma.repositories.postgres.models import (
    AppointmentSlot,
    Branch,
    BranchCourse,
    Course,
    Institute,
)

_OWNER = "roma-test-only-demo-v1"
_PROJECT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = _PROJECT / "var/roma/demo-db"


def validate_demo_target(url, app_env):
    target = make_url(url)
    database = unquote(target.database or "")
    if (app_env not in {"dev", "test"} or target.drivername != "postgresql+asyncpg"
            or target.host not in {"127.0.0.1", "localhost", "::1"}
            or database.casefold() == "roma" or not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", database)
            or target.query):
        raise ValueError("Demo database requires dev/test, loopback and a non-roma database")
    return url


def _binary(name):
    value = shutil.which(name)
    bundled = Path('/Library/PostgreSQL/18/bin') / name
    if value:
        return value
    if bundled.is_file():
        return str(bundled)
    raise RuntimeError("Native PostgreSQL tools are required for demo-db")


def _run(args, *, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=60)


def _safe_root(root):
    root = Path(root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("Demo directory must not use symlinks")
    return root


def migrate_demo(url, *, app_env):
    validate_demo_target(url, app_env)
    config = Config(str(_PROJECT / 'alembic.ini'))
    config.set_main_option('script_location', str(_PROJECT / 'migrations'))
    config.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    command.upgrade(config, 'head')


async def seed_voice_demo(url, *, app_env, now=None):
    """Short, repeatable transaction; codes label canonical branch names TEST ONLY."""
    validate_demo_target(url, app_env)
    now = (now or datetime.now(IST)).astimezone(IST)
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            institute = await session.scalar(select(Institute).where(Institute.code == 'TEST_ONLY_VOICE'))
            if institute is None:
                institute = Institute(code='TEST_ONLY_VOICE', name='TEST ONLY Voice Demo Institute')
                session.add(institute)
                await session.flush()
            branch = await session.scalar(select(Branch).where(Branch.institute_id == institute.id,
                                                               Branch.code == 'TEST_ONLY_VADODARA'))
            if branch is None:
                # The controller's allowed-list matches this canonical name.
                branch = Branch(institute_id=institute.id, code='TEST_ONLY_VADODARA',
                                name='Vadodara', city='Vadodara', timezone='Asia/Kolkata')
                session.add(branch)
                await session.flush()
            course = await session.scalar(select(Course).where(Course.institute_id == institute.id,
                                                               Course.code == 'TEST_ONLY_COURSE'))
            if course is None:
                course = Course(institute_id=institute.id, code='TEST_ONLY_COURSE', name='TEST ONLY Demo Course')
                session.add(course)
                await session.flush()
            if await session.get(BranchCourse, (branch.id, course.id)) is None:
                session.add(BranchCourse(branch_id=branch.id, course_id=course.id))
            for offset in (1, 2, 3):
                date = now.date() + timedelta(days=offset)
                for hour in (11, 15, 17):
                    exists = await session.scalar(select(AppointmentSlot.id).where(
                        AppointmentSlot.branch_id == branch.id, AppointmentSlot.appointment_date == date,
                        AppointmentSlot.start_time == time(hour)))
                    if exists is None:
                        session.add(AppointmentSlot(branch_id=branch.id, appointment_date=date,
                                                    start_time=time(hour), end_time=time(hour, 30),
                                                    capacity=1, status='available'))
    finally:
        await engine.dispose()


def start_demo(root=DEFAULT_ROOT, *, app_env, host='127.0.0.1', database='roma_demo'):
    validate_demo_target(f'postgresql+asyncpg://roma@{host}/{database}', app_env)
    root = _safe_root(root)
    root.mkdir(parents=True, exist_ok=False)
    marker = root / 'owner.json'
    marker.write_text(json.dumps({'owner': _OWNER}))
    os.chmod(root, 0o700)
    data = root / 'data'
    try:
        _run([_binary('initdb'), '-D', str(data), '--username=roma', '--auth=trust', '--no-instructions'])
        port = None
        for _ in range(5):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                candidate = sock.getsockname()[1]
            options = (f"-h 127.0.0.1 -p {candidate} -c unix_socket_directories='' "
                       '-c max_connections=10 -c max_worker_processes=0 -c max_wal_senders=0 '
                       '-c shared_buffers=8MB -c autovacuum=off')
            if _run([_binary('pg_ctl'), '-D', str(data), '-o', options,
                     '-l', str(root / 'postgres.log'), '-w', 'start'], check=False).returncode == 0:
                port = candidate
                break
        if port is None:
            raise RuntimeError('Disposable PostgreSQL could not start')
        _run([_binary('createdb'), '-h', '127.0.0.1', '-p', str(port), '-U', 'roma', database])
        url = f'postgresql+asyncpg://roma@127.0.0.1:{port}/{database}'
        marker.write_text(json.dumps({'owner': _OWNER, 'database_url': url}))
        migrate_demo(url, app_env=app_env)
        asyncio.run(seed_voice_demo(url, app_env=app_env))
        return url
    except BaseException:
        stop_demo(root, app_env=app_env)
        raise


def stop_demo(root=DEFAULT_ROOT, *, app_env):
    if app_env not in {'dev', 'test'}:
        raise ValueError('Demo database requires dev/test')
    root = _safe_root(root)
    if not root.exists():
        return
    marker = root / 'owner.json'
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('Refusing unowned demo directory')
    metadata = json.loads(marker.read_text())
    if metadata.get('owner') != _OWNER:
        raise ValueError('Refusing unowned demo directory')
    if 'database_url' in metadata:
        validate_demo_target(metadata['database_url'], app_env)
    data = root / 'data'
    if data.is_symlink():
        raise ValueError('Refusing symlinked demo data')
    if data.exists():
        if _run([_binary('pg_ctl'), '-D', str(data), 'status'], check=False).returncode == 0:
            _run([_binary('pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'])
    shutil.rmtree(root)
