# Voice_Agent Level 0 baseline evidence

Assessment: 2026-10-04. Working repository: `/Users/nihitraiyani/Voice_Agent`. Source revision: `28403729b0575adf233498c58a9c67c3f7194177`. This identifies the source; it is not a new baseline tag or complete Level 0 gate.

Two pre-existing local edits in `roma/repositories/redis/postcall_queue.py` and `roma/workers/postcall/worker.py` were preserved. No level feature, dependency, secret setting, runtime prompt or audio asset was changed during documentation redesign. Small script docstrings were synchronized only; executable behavior is unchanged.

## Source identity and environment

The selected Word file was retained unchanged. SHA-256: `a3e0590b63da620131f41d1ffd3b738518b2d2efacba1f403dd930efb44ba644`. Review covered document-order paragraphs/tables and Levels 0–14 plus completion sections 56–68.

`uv.lock` SHA-256: `05157827cd694fc43a7dfa367f28c231deb4c9c6930b6f0032a54400e402221d`. Tested installed environment: Python 3.12.12, Pipecat 1.6.0, SQLAlchemy 2.0.54, Alembic 1.20.0, asyncpg 0.31.0, Dramatiq 2.2.1, FastAPI 0.139.2, pytest 9.1.1, Ruff 0.16.0, NLTK 3.10.0 and Twilio SDK 9.11.1. This records installed versions; a clean locked restore remains a separate gate.

## Actual ownership trace

Signed Twilio `/answer` → bounded webhook service → PostgreSQL receipt/call/event transaction → TwiML `<Connect><Stream>` → signed `/ws`/Account SID → Pipecat VAD/ASR → domain stages/extraction/resolution → GPT-4o wording → sentence/confirmation/pacing/final safety → Bulbul/cache → Twilio output → local capture/cleanup → Redis/spool → one recording worker → PostgreSQL call/job intents → Dramatiq ID delivery/actors → durable effect/settlement.

Current local defaults: Saaras v3 code-mix, Bulbul v3/ishita/Hindi pace 1.05, VAD stop 0.45 s, contextual endpoint 0.50/0.85/1.30 s, backchannel 0.60 s. Database pool 5 + 10 overflow, 5-second wait; Redis conversation TTL one hour; recording retention 90-day default; OpenAI ceiling ₹200. These are source defaults, not disclosed `.env` values or approved production policies.

The transactional appointment repository exists but is not wired into live controller booking. Static/read-only calendar and `locked_slot` still drive spoken confirmation. Local models, durable conversation restore, governed RAG and production roles/telemetry are unverified/unimplemented targets.

## Fresh checks

| Check | Actual result |
|---|---|
| Full offline pytest with explicit tokenizer data and permitted disposable services | **1,571 passed, 23 warnings, 50.80 seconds** |
| Offline conversation evaluation | **10/10 passed, zero findings; filter canaries pass** |
| Ruff `roma tests scripts` | **5 existing I001 import-format errors** |
| Live/local-model/latency/capacity benchmarks | Not run |

The first restricted test run had 1 sentence-test failure and 31 database/worker setup errors. The sentence error identified missing `punkt_tab/english` data and reproduced in isolation; it passed after explicitly supplying official installed tokenizer data in a temporary test directory. Minimal PostgreSQL reproduction showed `shmget: Operation not permitted`; allowing disposable local service execution removed that environment restriction. The subsequent full suite passed without application/test changes.

Reproduction command for that verified environment:

```bash
NLTK_DATA=/private/tmp/voice-agent-tokenizer PYTHONDONTWRITEBYTECODE=1 \
    .venv/bin/python -m pytest -q -p no:cacheprovider
```

That temporary path is test setup, not a portable project dependency. On a new machine, install approved NLTK data explicitly into the environment and set its lookup path; ordinary tests must not download automatically. For example:

```bash
uv run --no-sync python - <<'PYSETUP'
import nltk
nltk.download('punkt_tab', download_dir='.venv/nltk_data', raise_on_error=True)
PYSETUP
```

Lint findings predate these documentation edits and are in `roma/core/config.py`, `roma/core/database.py`, `roma/domain/conversation/state.py`, `roma/repositories/postgres/models/base.py` and `roma/repositories/postgres/repositories.py`. They were recorded without modifying unrelated source formatting/user comments.

## Gate position

- [x] Source/roadmap identified and actual system traced.
- [x] Fresh complete offline regression and evaluation evidence recorded.
- [x] PostgreSQL booking/webhook/job and Redis broker integration tests executed.
- [ ] Resolve the recorded lint findings in a separately scoped maintenance change.
- [ ] Reproduce the locked environment/tokenizer/native services on a clean machine.
- [ ] Measure representative stage and end-to-end P50/P95 timing.
- [ ] Review student prerequisite self-check and system explanation.

**Level 0 gate pending.** No later level is passed solely from these regression results. See [readiness](../13-backend-roadmap.md) and the [level briefs](../README.md).
