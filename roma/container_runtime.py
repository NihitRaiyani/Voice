"""Container-only launch and probes. No private dotenv or model downloads."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

SECRET_ROOT = Path("/run/secrets")


def load_environment() -> None:
    def read(name):
        value = (SECRET_ROOT / name).read_text().strip()
        if len(value) < 32 or not all(c in "0123456789abcdef" for c in value):
            raise RuntimeError("Invalid private runtime secret")
        return value

    password = read("postgres_password")
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://roma_stack:{password}@postgres:5432/roma_stack"
    )
    os.environ["REDIS_URL"] = f"redis://:{read('redis_password')}@redis:6379/0"
    os.environ["AUTH_JWT_SECRET"] = read("auth_jwt_secret")
    os.environ["PII_HASH_KEY"] = read("pii_hash_key")
    os.environ["PII_ENCRYPTION_KEY"] = read("pii_encryption_key")
    os.environ["PII_ENCRYPTION_ENABLED"] = "true"
    os.environ["ROMA_CONTAINER_MOCK"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


async def dependencies() -> None:
    import asyncpg
    from redis.asyncio import Redis

    database = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    async with asyncio.timeout(5):
        connection = await asyncpg.connect(database)
        try:
            await connection.fetchval("SELECT version_num FROM alembic_version")
            await connection.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            )
        finally:
            await connection.close()
        redis = Redis.from_url(os.environ["REDIS_URL"])
        try:
            if not await redis.ping():
                raise RuntimeError("Redis unavailable")
        finally:
            await redis.aclose()


async def worker() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from roma.repositories.postgres.background_jobs import BackgroundJobStore
    from roma.workers.background import run_background_worker

    engine = create_async_engine(
        os.environ["DATABASE_URL"], pool_size=2, max_overflow=0, hide_parameters=True
    )
    store = BackgroundJobStore(async_sessionmaker(engine, expire_on_commit=False))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    async def heartbeat():
        while not stop.is_set():
            await dependencies()
            await asyncio.to_thread(Path("/tmp/worker-heartbeat").touch)
            await asyncio.sleep(3)

    consumer = asyncio.create_task(run_background_worker(store, stop=stop))
    pulse = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait((consumer, pulse), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        stop.set()
        consumer.cancel()
        pulse.cancel()
        await asyncio.gather(consumer, pulse, return_exceptions=True)
        await engine.dispose()


async def exercise_worker() -> None:
    """Synthetic SQL receipt proves the separate consumer, without provider calls."""
    from uuid import uuid4

    import asyncpg

    database = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(database)
    call_id, job_id = uuid4(), uuid4()
    key = "container-smoke-" + str(job_id)
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO calls(id,provider_call_id,direction,status,started_at) "
                "VALUES($1,$2,'outbound','completed',now())",
                call_id,
                key,
            )
            await connection.execute(
                "INSERT INTO followup_jobs(id,call_id,job_type,payload,idempotency_key) "
                "VALUES($1,$2,'compute_statistics',$3::jsonb,$4)",
                job_id,
                call_id,
                json.dumps({"duration_secs": 1, "audio_secs": 0, "turn_count": 0}),
                key,
            )
        async with asyncio.timeout(15):
            while (  # noqa: ASYNC110 — bounded polling of another process SQL receipt
                await connection.fetchval(
                    "SELECT status FROM followup_jobs WHERE id=$1", job_id
                )
                != "succeeded"
            ):
                await asyncio.sleep(0.5)
        if (
            await connection.fetchval(
                "SELECT count(*) FROM call_events WHERE idempotency_key=$1", key
            )
            != 1
        ):
            raise RuntimeError("Synthetic job did not produce exactly one durable event")
        async with connection.transaction():
            await connection.execute("DELETE FROM call_events WHERE call_id=$1", call_id)
            await connection.execute("DELETE FROM followup_jobs WHERE id=$1", job_id)
            await connection.execute("DELETE FROM calls WHERE id=$1", call_id)
    finally:
        await connection.close()


def check_model_platform(profile: str) -> None:
    # No CUDA implementation exists for these retained quantized MLX artifacts.
    if profile == "gpu":
        raise RuntimeError(
            "Retained Whisper Turbo MLX 4-bit requires native Apple Silicon Mac; "
            "NVIDIA device reservation does not make it CUDA compatible"
        )
    raise RuntimeError(
        "Native Mac speech requires an explicitly configured authenticated "
        "external bridge; loopback-only native service is not Docker-reachable"
    )


def initialize_secrets() -> None:
    # Controlled one-shot root initializer only; every long-running consumer is nonroot.
    groups = {
        "app": (
            10001,
            (
                "postgres_password",
                "redis_password",
                "auth_jwt_secret",
                "pii_hash_key",
                "pii_encryption_key",
                "metrics_token",
            ),
        ),
        "redis": (999, ("redis_password",)),
        "prometheus": (65534, ("metrics_token",)),
        "grafana": (472, ("grafana_password",)),
    }
    for group, (uid, names) in groups.items():
        directory = Path("/out") / group
        directory.mkdir(exist_ok=True)
        for name in names:
            destination = directory / name
            if destination.is_symlink():
                raise RuntimeError("Runtime secret target must be a regular file")
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write((SECRET_ROOT / name).read_text())
            destination.chmod(0o400)
            os.chown(destination, uid, uid)
        directory.chmod(0o700)
        os.chown(directory, uid, uid)


def main() -> None:
    action = sys.argv[1]
    if action == "secrets-init":
        initialize_secrets()
        return
    if action in ("gpu", "native-mac"):
        check_model_platform(action)
    if action == "worker-health":
        path = Path("/tmp/worker-heartbeat")
        if not path.exists() or time.time() - path.stat().st_mtime > 15:
            raise SystemExit(1)
        return
    if action in ("api-health", "model-health"):
        with urllib.request.urlopen("http://127.0.0.1:8020/ready", timeout=4) as response:
            if response.status != 200:
                raise SystemExit(1)
        return
    load_environment()
    if action == "api":
        import uvicorn

        uvicorn.run(
            "roma.backend_app:create_app",
            factory=True,
            host="0.0.0.0",
            port=8020,
            workers=1,
            proxy_headers=False,
            access_log=False,
        )
    elif action == "migrate":
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("/app/alembic.ini"), "head")
    elif action == "worker":
        asyncio.run(worker())
    elif action == "smoke":
        asyncio.run(dependencies())
        asyncio.run(exercise_worker())
        with urllib.request.urlopen("http://127.0.0.1:8020/ready", timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("API not ready")
        print(
            json.dumps(
                {
                    "profile": "mock",
                    "sql_redis_api": "ready",
                    "paid_calls": 0,
                    "model_downloads": 0,
                    "native_speech_gate": False,
                }
            )
        )
    else:
        raise ValueError("Unknown runtime action")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — no secret-bearing exception bodies
        if sys.argv[1] in ("gpu", "native-mac"):
            print(str(exc), file=sys.stderr)
        else:
            print("Container runtime operation failed", file=sys.stderr)
        raise SystemExit(1) from None
