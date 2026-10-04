"""Operator exit behavior and credential-safe failure output."""

import argparse
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import SecretStr
from roma.core.config import Settings
from scripts import check_database_pool


def test_unsafe_preflight_skips_load_and_closes_database(monkeypatch, capsys):
    settings = Settings(
        _env_file=None,
        sarvam_api_key=SecretStr("unused"),
        openai_api_key=SecretStr("unused"),
        redis_url=SecretStr("redis://localhost/0"),
        database_url=SecretStr("postgresql+asyncpg://test:secret@localhost/unused"),
    )
    close = AsyncMock()
    load = AsyncMock()
    monkeypatch.setattr(check_database_pool, "get_settings", lambda: settings)
    monkeypatch.setattr(
        check_database_pool.Database,
        "from_settings",
        lambda _: SimpleNamespace(close=close),
    )
    monkeypatch.setattr(
        check_database_pool, "check_capacity", AsyncMock(return_value={"safe": False})
    )
    monkeypatch.setattr(check_database_pool, "run_load", load)
    code = asyncio.run(
        check_database_pool._run(argparse.Namespace(headroom=10, run_load=True))
    )
    assert code == 1
    load.assert_not_awaited()
    close.assert_awaited_once()
    assert json.loads(capsys.readouterr().out) == {"capacity": {"safe": False}}


def test_cli_failure_output_does_not_expose_exception_secrets(monkeypatch, capsys):
    async def fail(_):
        raise RuntimeError("postgresql+asyncpg://test:private-password@localhost/unused")

    monkeypatch.setattr(check_database_pool, "_run", fail)
    monkeypatch.setattr("sys.argv", ["check_database_pool.py"])
    assert check_database_pool.main() == 1
    output = capsys.readouterr().out
    assert "private-password" not in output
    assert json.loads(output) == {"error": "database check failed", "type": "RuntimeError"}
