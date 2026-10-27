"""Owned, loopback-only disposable PostgreSQL/Redis for backend measurements.

No environment file or configured service URL is read; no models, schema changes to
working databases, downloads or Docker images. Temporary data is removed on exit.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def _postgres_binary(name):
    found=shutil.which(name)
    bundled=Path('/Library/PostgreSQL/18/bin')/name
    if found:
        return found
    if bundled.is_file():
        return str(bundled)
    raise RuntimeError('Native PostgreSQL binaries unavailable; no download attempted')


@contextmanager
def disposable_services():
    initdb,pg_ctl=_postgres_binary('initdb'),_postgres_binary('pg_ctl')
    redis_binary=shutil.which('redis-server')
    if not redis_binary:
        raise RuntimeError('Native redis-server unavailable; no download attempted')
    with TemporaryDirectory(prefix='roma-load-') as directory:
        data=Path(directory)/'data'
        postgres_started=False
        redis_process=None
        try:
            subprocess.run([initdb,'-D',str(data),'--username=roma','--auth=trust','--no-instructions'],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            postgres_port=_port()
            subprocess.run([pg_ctl,'-D',str(data),'-o',f'-h 127.0.0.1 -p {postgres_port} -k {directory}',
                            '-l',str(Path(directory)/'postgres.log'),'-w','start'],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            postgres_started=True
            redis_port=_port()
            redis_process=subprocess.Popen([redis_binary,'--bind','127.0.0.1','--port',str(redis_port),
                  '--save','','--appendonly','no','--dir',directory],
                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+5
            while True:
                try:
                    with socket.create_connection(('127.0.0.1',redis_port),timeout=.2):
                        break
                except OSError:
                    if time.monotonic()>deadline or redis_process.poll() is not None:
                        raise RuntimeError('Disposable Redis unavailable') from None
                    time.sleep(.02)
            yield (f'postgresql+asyncpg://roma@127.0.0.1:{postgres_port}/postgres',
                   f'redis://127.0.0.1:{redis_port}/0')
        finally:
            if redis_process is not None:
                redis_process.terminate()
                redis_process.wait(timeout=5)
            if postgres_started:
                subprocess.run([pg_ctl,'-D',str(data),'-m','fast','-w','stop'],check=False,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
