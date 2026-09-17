# Secret & Security Scaffold — Design

**Date:** 2026-07-20
**Gate:** Gate 0 (first half) in `docs/10-build-order.md`
**Governs / enforces:** `docs/07-security.md`
**Status:** Approved design → implementation

## Purpose

The first code in the repo. A config module that makes it *structurally impossible* to run
Roma without its secrets present and *structurally impossible* to leak them. Per `docs/07`,
security is a build gate, not a later hardening pass. A build that can place a call without
this scaffold is not allowed to exist.

This is the **first half of Gate 0**. The second half — the standalone pre-TTS filter
(`docs/04`) — is a separate module that follows immediately after and will import this one.

## Decisions (locked in brainstorm)

- **Config loader:** `pydantic-settings` (`BaseSettings`). Chosen over hand-rolled `os.environ`
  because `SecretStr` auto-redacts in `repr()`/tracebacks and validation is declarative and
  fail-fast. Pydantic is already transitively present via the OpenAI/Pipecat tree.
- **Packaging:** `uv` + `pyproject.toml`, `src/roma/` layout. Establishes the pattern the whole
  build inherits.
- **Python:** 3.11+.

## Components

### 1. `pyproject.toml`
- uv-managed. Runtime dep: `pydantic-settings`. Dev dep: `pytest`.
- `src/roma/` package layout.

### 2. `src/roma/config.py`
`Settings(BaseSettings)` — every credential a **required `SecretStr`** (no default, so a
missing one is a hard error):

| Env var | Type | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | SecretStr | env-only |
| `TWILIO_AUTH_TOKEN`  | SecretStr | env-only |
| `TWILIO_FROM_NUMBER` | SecretStr | the Roma outbound number |
| `SARVAM_API_KEY`     | SecretStr | STT (Saaras) + TTS (Bulbul) |
| `OPENAI_API_KEY`     | SecretStr | |
| `REDIS_URL`          | SecretStr | carries auth credentials |
| `APP_ENV`            | str = `"dev"` | non-secret; dev/staging/prod |
| `LOG_LEVEL`          | str = `"INFO"` | non-secret |

- `model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")`.
- `get_settings()` wrapped in `functools.lru_cache` → validation runs **once, at first call**.
  A missing/blank required key raises `ValidationError` at that point. Process start calls it
  explicitly, so the process **refuses to boot rather than run unguarded** — the `docs/07`
  fail-safe posture, enforced in code.
- Real secret values are read only via `.get_secret_value()`, at the exact API-client boundary,
  never earlier.

### 3. `src/roma/logging_setup.py`
- `configure_logging()` sets format + level from `Settings`.
- `RedactionFilter(logging.Filter)` scrubs any known secret value out of every `LogRecord`
  before emission. This is the mechanism that makes "no secret in logs" a property of the
  system rather than a rule people must remember. ~25 lines.

### 4. `.env.example` + `.gitignore`
- `.env.example`: all 8 env var names, commented, **no live values** (the two non-secret ones
  may show their default as an illustrative comment, e.g. `APP_ENV=dev`).
- `.gitignore`: `.env` (already committed with the scaffold).

### 5. `tests/test_config.py`
Guardrail tests:
1. Missing a required key → `get_settings()` raises (fail-fast). Test each required key.
2. `repr(settings)` and `str(<secret field>)` never contain the secret value (redaction).
3. `RedactionFilter` scrubs a secret out of a formatted log line.
4. `.env.example`'s key set exactly matches **all** `Settings` fields, upper-cased (drift guard
   — the example can never silently fall out of sync with the code, in either direction).

## Fail-safe contract (the property this scaffold guarantees)

- **No secret in code, git, or logs.** Enforced by: `SecretStr` (repr/traceback),
  `RedactionFilter` (logs), `.gitignore` (git), `.env.example` values-free (never a real value).
- **No unguarded boot.** `get_settings()` validates all required keys before the process is
  usable; a missing dependency hard-fails rather than proceeding.

## Explicitly out of scope (deferred, correctly)

- Pre-TTS filter (`docs/04`) — Gate 0 second half, next module; will import this config.
- Dialer / DND / calling-window / consent-line (Step 1) — will import this scaffold.
- Tuning knobs (endpoint ms, VAD sensitivity) — not secrets; belong with their modules.
- Real external secret store (Vault/cloud) — env + `.env` is correct for the self-hosted
  self-hosted origin; revisit only if the deploy model changes.

## Success criteria

- `pytest` green on all four guardrail tests.
- Importing `roma` with a required key unset raises before anything callable initializes.
- `grep`-ing logs/tracebacks from a forced error shows redaction, never a raw key.
