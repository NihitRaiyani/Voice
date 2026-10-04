# Roma Voice Agent

Roma is a backend-only counselling voice agent for Weltec's Digital Marketing course. Its goal is a specific branch, day and time with explicit readback and caller confirmation. It understands Gujarati, Hindi, English and code-mix. Live replies remain Hindi-base Hinglish; multilingual output is evaluated separately.

**Working repository: `Voice_Agent`.** The [v4 roadmap](docs/roadmaps/AI_Voice_Agent_Unified_Roadmap_for_Student_Skill_Development_v4.docx) governs future level-by-level development. Start with the [documentation index](docs/README.md) and [engineering rules](CLAUDE.md).

## Current system and accepted target

| Boundary | Present in this repository | v4 target / remaining work |
|---|---|---|
| Calls | Twilio bidirectional Media Streams; signed HTTP/WS callbacks | Retain Twilio; integrate measured local providers at Level 10 |
| Voice | Pipecat, Silero, Saaras v3, GPT-4o, Bulbul v3 | Provider contracts, direct local LLM/ASR/TTS labs, then model serving |
| Conversation | Seven deterministic stages, extraction, time resolver, speech safety | Durable milestone restore and structured safety audit |
| Data | PostgreSQL schema, Alembic, repositories and booking locks/constraints | Bounded pools/process budget, checkpoint/model/benchmark schema and turn-language metadata; live booking, restoration and versioned APIs pending |
| Transient state | Redis checkpoints, leads, status, caches and recording queue | Bounded pressure, checkpoint recovery and measured rate limits |
| Webhooks | Atomic `/answer` receipt/call/event acceptance | Extend only to required callback kinds with explicit replay policy |
| Jobs | Recording/spool handoff, PostgreSQL ledger, Dramatiq/Redis execution | Publication bounds, retention, audited replay and privacy controls |
| Knowledge | Approved static facts | Conditional governed RAG with local embeddings/pgvector at Level 9 |
| Deployment | Local scripts and pre-v4 infrastructure Compose | Non-container local learning first; full deployment packaging at Level 13 |

The database booking repository is implemented, but the live controller still uses the static/read-only calendar and conversational `locked_slot`. A spoken win is not yet proof of a committed appointment. Schema tables also do not prove all corresponding runtime writes exist.

There is no frontend. Existing APIs are `/api/call`, `/api/call/{request_uuid}`, `/answer`, `/ws` and `/health`; the `api/v1` Python folder does not mean the public paths are versioned.

## Run and verify

Use the [operating guide](docs/runbook.md) for local PostgreSQL/Redis, migrations, configuration and worker commands. The backend composition root is:

```bash
uv run --extra telephony uvicorn roma.main:create_app --factory --host 127.0.0.1 --port 8020
```

A configured, migrated PostgreSQL database is required for successful `/answer` webhooks. `scripts/serve_media.py` adds development diagnostics; keep those private.

Offline checks use fake providers and disposable data:

```bash
uv run --no-sync pytest -q
uv run --no-sync ruff check roma tests scripts
uv run --no-sync python scripts/run_eval.py
```

See [verification](docs/12-verification.md) for environment requirements and what those results prove. Level 1 sections 3–5 now include persistence/pool safeguards, additive relational foundations and the [migration/separate-demo workflow](docs/21-database-migrations.md); remaining sections stay pending. Verification uses no provider spending or live calls.

## Code map

`roma/main.py` composes `api`, `services`, `domain`, `repositories`, `providers`, `realtime` and `workers`. Business rules live in the domain; provider SDKs and storage remain at the edges. Runtime prompts live in `roma/prompts/` and affect speech directly. Read [architecture](docs/01-architecture.md) before changing those boundaries.
