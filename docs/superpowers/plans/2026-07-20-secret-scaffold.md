# Secret & Security Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Gate 0 (first half) secret/config module: all Roma credentials load from
env only, validation fails fast if any required key is missing, and secrets can never reach
logs or tracebacks.

**Architecture:** A single `pydantic-settings` `Settings` class holds every credential as a
required `SecretStr` (auto-redacted in repr/tracebacks) plus two non-secret settings. A
`get_settings()` cached accessor validates once at first call — a missing key raises before
anything callable boots. A `RedactionFilter` scrubs known secret values out of every log
record so "no secret in logs" is enforced by the system, not by discipline.

**Tech Stack:** Python 3.11+, uv + pyproject (src layout), pydantic-settings v2, pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-secret-scaffold-design.md` (enforces `docs/07-security.md`).

---

## File Structure

- Create: `pyproject.toml` — uv project metadata, deps, pytest config.
- Create: `src/roma/__init__.py` — package marker.
- Create: `src/roma/config.py` — `Settings` + `get_settings()`.
- Create: `src/roma/logging_setup.py` — `RedactionFilter` + `configure_logging()`.
- Create: `.env.example` — all 8 env var names, no live values.
- Create: `tests/test_config.py` — fail-fast, repr redaction, `.env.example` drift guard.
- Create: `tests/test_logging.py` — `RedactionFilter` scrubbing.
- Exists already: `.gitignore` (ignores `.env`, `.venv/`, caches — committed with the spec).

---

## Task 1: Project bootstrap (uv + src layout)

**Files:**
- Create: `pyproject.toml`
- Create: `src/roma/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "roma"
version = "0.1.0"
description = "Roma — outbound code-mix voice agent for Weltec"
requires-python = ">=3.11"
dependencies = [
    "pydantic-settings>=2.5",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/roma"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the package marker**

`src/roma/__init__.py`:

```python
"""Roma — outbound code-mix voice agent for Weltec."""
```

- [ ] **Step 3: Sync the environment**

Run: `uv sync --extra dev`
Expected: creates `.venv/`, installs `pydantic-settings` and `pytest`, builds `roma` (editable).
No error. (`.venv/` is already gitignored.)

- [ ] **Step 4: Verify pytest runs (no tests yet)**

Run: `uv run pytest -q`
Expected: exit cleanly with "no tests ran" (exit code 5 is fine at this point).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/roma/__init__.py uv.lock
git commit -m "chore: bootstrap uv project (src layout, pydantic-settings, pytest)"
```

---

## Task 2: Settings class with fail-fast validation

**Files:**
- Create: `tests/test_config.py`
- Create: `src/roma/config.py`

> **API checkpoint:** the code below targets pydantic-settings v2. If `uv sync` installed a
> version whose API differs (e.g. `SettingsConfigDict` import path changed), pull current docs
> via context7 (`resolve-library-id` → `pydantic-settings`) before adjusting. Do NOT guess.

- [ ] **Step 1: Write the failing fail-fast test**

`tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from roma.config import Settings

REQUIRED = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
    "REDIS_URL",
]


def _set_full_env(monkeypatch):
    """Populate every required secret with a dummy value."""
    for key in REQUIRED:
        monkeypatch.setenv(key, f"test-{key.lower()}")


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_key_fails_fast(monkeypatch, missing):
    _set_full_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    # _env_file=None makes the test hermetic — a developer's real .env is ignored.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_all_required_present_constructs(monkeypatch):
    _set_full_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.openai_api_key.get_secret_value() == "test-openai_api_key"
    assert settings.app_env == "dev"  # default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roma.config'`.

- [ ] **Step 3: Write `src/roma/config.py`**

```python
"""Roma runtime configuration. Secrets are env-only (docs/07-security.md).

A missing required secret raises at get_settings() — the process refuses to run
unguarded rather than proceed. Secret values are read only via .get_secret_value()
at the exact API-client boundary.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Secrets — required, no defaults. A missing one hard-fails validation.
    twilio_account_sid: SecretStr
    twilio_auth_token: SecretStr
    twilio_from_number: SecretStr
    sarvam_api_key: SecretStr
    openai_api_key: SecretStr
    redis_url: SecretStr

    # Non-secret operational settings.
    app_env: str = "dev"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings once. Raises ValidationError if a required
    secret is missing (docs/07 fail-safe: never run unguarded)."""
    return Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS — 7 tests (6 parametrized missing-key cases + the construct case).

- [ ] **Step 5: Commit**

```bash
git add src/roma/config.py tests/test_config.py
git commit -m "feat: fail-fast Settings with env-only required secrets"
```

---

## Task 3: Prove secrets never leak in repr

**Files:**
- Modify: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing redaction test**

Append to `tests/test_config.py`:

```python
def test_secret_never_appears_in_repr(monkeypatch):
    secret = "super-secret-openai-value"
    _set_full_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    settings = Settings(_env_file=None)

    # repr of the whole object and str of the field both redact.
    assert secret not in repr(settings)
    assert secret not in str(settings.openai_api_key)
    # The real value is still retrievable at the API boundary.
    assert settings.openai_api_key.get_secret_value() == secret
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_config.py::test_secret_never_appears_in_repr -v`
Expected: PASS immediately — `SecretStr` already redacts. (This test *documents and locks*
the guarantee; no implementation change is needed. If it fails, a field was declared as a
plain `str` instead of `SecretStr` — fix the field type.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py
git commit -m "test: lock secret redaction in repr/str"
```

---

## Task 4: `.env.example` + drift guard

**Files:**
- Create: `.env.example`
- Modify: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing drift-guard test**

Append to `tests/test_config.py`:

```python
from pathlib import Path

from roma.config import Settings as _SettingsForFields


def test_env_example_matches_settings_fields():
    repo_root = Path(__file__).resolve().parents[1]
    example = repo_root / ".env.example"
    keys_in_example = set()
    for raw in example.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys_in_example.add(line.split("=", 1)[0].strip())

    expected = {name.upper() for name in _SettingsForFields.model_fields}
    assert keys_in_example == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_env_example_matches_settings_fields -v`
Expected: FAIL — `FileNotFoundError: .env.example` (not created yet).

- [ ] **Step 3: Create `.env.example`**

```bash
# Roma — environment configuration.
# Copy to .env and fill in real values. .env is gitignored; NEVER commit secrets.

# --- Secrets (required; env-only per docs/07) ---
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
SARVAM_API_KEY=
OPENAI_API_KEY=
REDIS_URL=

# --- Non-secret operational settings ---
APP_ENV=dev
LOG_LEVEL=INFO
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_env_example_matches_settings_fields -v`
Expected: PASS — the 8 keys match the 8 `Settings` fields exactly.

- [ ] **Step 5: Commit**

```bash
git add .env.example tests/test_config.py
git commit -m "feat: .env.example with drift guard against Settings fields"
```

---

## Task 5: Log redaction filter

**Files:**
- Create: `tests/test_logging.py`
- Create: `src/roma/logging_setup.py`

- [ ] **Step 1: Write the failing filter test**

`tests/test_logging.py`:

```python
import logging

from roma.logging_setup import RedactionFilter


def test_redaction_filter_scrubs_secret_in_args():
    secret = "twilio-auth-abcdef"
    filt = RedactionFilter([secret])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling twilio with token %s",
        args=(secret,),
        exc_info=None,
    )
    assert filt.filter(record) is True
    message = record.getMessage()
    assert secret not in message
    assert "***REDACTED***" in message


def test_redaction_filter_ignores_empty_secrets():
    # Empty strings must not scrub everything to REDACTED.
    filt = RedactionFilter(["", None])
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="nothing secret here",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    assert record.getMessage() == "nothing secret here"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'roma.logging_setup'`.

- [ ] **Step 3: Write `src/roma/logging_setup.py`**

```python
"""Logging setup with secret redaction (docs/07: no secret in logs).

RedactionFilter is defense-in-depth: even if a secret value is accidentally
logged, it is scrubbed from the record before emission.
"""

import logging

from roma.config import Settings, get_settings

_REDACTED = "***REDACTED***"


class RedactionFilter(logging.Filter):
    """Scrub known secret values out of every log record before emission."""

    def __init__(self, secrets):
        super().__init__()
        # Drop empties; longest-first so overlapping values fully scrub.
        self._secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, _REDACTED)
        if redacted != message:
            # Collapse to the redacted string; args already applied above.
            record.msg = redacted
            record.args = ()
        return True


def _secret_values(settings: Settings):
    return [
        settings.twilio_account_sid.get_secret_value(),
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_from_number.get_secret_value(),
        settings.sarvam_api_key.get_secret_value(),
        settings.openai_api_key.get_secret_value(),
        settings.redis_url.get_secret_value(),
    ]


def configure_logging(settings: Settings | None = None) -> None:
    """Install a root handler that redacts known secrets. Call once at start."""
    settings = settings or get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter(_secret_values(settings)))
    logging.basicConfig(
        level=settings.log_level.upper(),
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_logging.py -v`
Expected: PASS — both filter tests green.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS — all config + logging tests green (10 tests total).

- [ ] **Step 6: Commit**

```bash
git add src/roma/logging_setup.py tests/test_logging.py
git commit -m "feat: RedactionFilter + configure_logging (no secret in logs)"
```

---

## Done — Gate 0 (first half) exit check

After Task 5, verify the success criteria from the spec:

- [ ] `uv run pytest -v` is fully green.
- [ ] `git ls-files | grep -i '\.env$'` returns nothing (only `.env.example` is tracked).
- [ ] With a required key unset, `uv run python -c "from roma.config import get_settings; get_settings()"`
      raises `ValidationError` and the traceback shows no raw key values.

Then update `SESSION.md` / `HANDOFF.md`: Gate 0 first half done, next task = the standalone
pre-TTS filter (`docs/04`, Gate 0 second half), which imports this config.
```
