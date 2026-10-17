# Roma Voice Agent

Roma is a backend-only counselling voice agent for Weltec's Digital Marketing course. Its goal is a specific branch, day and time with explicit readback and caller confirmation. It understands Gujarati, Hindi, English and code-mix. Live replies remain Hindi-base Hinglish; multilingual output is evaluated separately.

**Working repository: `Voice_Agent`.** The [OpenAI roadmap](docs/roadmaps/AI_VOICE_AGENT_openai_llm.docx) governs future level-by-level development. Start with the [documentation index](docs/README.md) and [engineering rules](CLAUDE.md).

## Current system and accepted target

| Boundary | Present in this repository | OpenAI roadmap target / remaining work |
|---|---|---|
| Calls | Twilio bidirectional Media Streams; signed HTTP/WS callbacks | Retain Twilio; integrate measured local providers at Level 10 |
| Voice | Pipecat, Silero, Saaras v3, GPT-4o, Bulbul v3 | OpenAI LLM provider, local ASR/TTS labs, then speech model serving |
| Conversation | Seven deterministic stages, checkpoint adapter, OpenAI text seam, deterministic safety audit and bounded prompt inputs | Native quality review and full voice approval |
| Data | PostgreSQL schema, Alembic, repositories, booking locks/constraints, bounded pools/process budget, checkpoint/model/benchmark schema, turn-language metadata and versioned resource API | Section 13 booking/holds/API and same-SID appointment truth implemented; broader redial recovery pending |
| Transient state | Redis checkpoints, leads, status, caches and recording queue; PostgreSQL now has compatible latest-checkpoint restore by call SID | Bounded pressure, redial/session mapping and measured rate limits |
| Webhooks | Atomic `/answer` receipt/call/event acceptance | Extend only to required callback kinds with explicit replay policy |
| Jobs | Recording/spool handoff, PostgreSQL ledger, Dramatiq/Redis execution | Publication bounds, retention, audited replay and privacy controls |
| Knowledge | Approved static facts | Conditional governed RAG with local embeddings/pgvector at Level 9 |
| Deployment | Local scripts and pre-v4 infrastructure Compose | Non-container local learning first; full deployment packaging at Level 13 |

Production booking now uses PostgreSQL availability, expiring Redis holds and a short commit before confirmed speech. Cancellation releases the slot atomically; reconnect consults database appointment truth. Static calendars remain for explicit offline exercises. Schema tables do not prove all corresponding runtime writes exist. See [section 13](docs/levels/level-03.md).

There is no frontend. Live telephony still uses `/api/call`, `/api/call/{request_uuid}`, `/answer`, `/ws` and `/health`. Durable business resources now use versioned `/api/v1/...` JSON routes with consistent envelopes.

## Run and verify

Use the [operating guide](docs/runbook.md) for local PostgreSQL/Redis, migrations, configuration and worker commands. The Level 1 native startup path is:

```bash
make app-start
```

A configured, migrated PostgreSQL database is required for successful `/answer` webhooks. `scripts/serve_media.py` adds development diagnostics; keep those private.

Offline checks use fake providers and disposable data:

```bash
make ci
make test
uv run --no-sync python scripts/run_eval.py
```

See [verification](docs/12-verification.md) for environment requirements and what those results prove. Level 1 sections 3–9 now include persistence/pool safeguards, additive relational foundations, the [migration/separate-demo workflow](docs/21-database-migrations.md), provider contracts, the [versioned REST API](docs/24-rest-api-design.md) and day-one testing/CI skeleton. Hosted CI passed on 2026-10-05 for the Level 1 workflow. Verification uses no provider spending or live calls.

## Code map

`roma/main.py` composes `api`, `services`, `domain`, `repositories`, `providers`, `realtime` and `workers`. Business rules live in the domain; provider SDKs and storage remain at the edges. Runtime prompts live in `roma/prompts/` and affect speech directly. Read [architecture](docs/01-architecture.md) before changing those boundaries.
