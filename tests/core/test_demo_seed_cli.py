"""Demo CLI refuses implicit or production writes without exposing connection data."""

import os
import subprocess
import sys


def test_demo_cli_requires_explicit_opt_in():
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py"], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "--demo" in result.stderr


def test_demo_cli_rejects_production_without_connecting_or_exposing_secrets():
    environment = {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql://demo:private-secret@invalid.example/roma",
    }
    result = subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--demo"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "APP_ENV=dev or test" in result.stdout
    assert "private-secret" not in result.stdout + result.stderr
    assert "invalid.example" not in result.stdout + result.stderr
