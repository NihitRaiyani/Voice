# Roma Voice Agent

Roma is a backend-only counselling voice agent for Weltec's Digital Marketing course. Its goal is a specific branch, day and time with explicit readback and caller confirmation. It understands Gujarati, Hindi, English and code-mix. Live replies remain Hindi-base Hinglish; multilingual output is evaluated separately.

**Working repository: `Voice_Agent`.** The [v4 roadmap](docs/roadmaps/AI_Voice_Agent_Unified_Roadmap_for_Student_Skill_Development_v4.docx) governs future level-by-level development. Start with the [documentation index](docs/README.md) and [engineering rules](CLAUDE.md).

## Current system and accepted target

| Boundary | Present in this repository | v4 target / remaining work |
|---|---|---|
| Calls | Twilio bidirectional Media Streams; signed HTTP/WS callbacks | Retain Twilio; integrate measured local providers at Level 10 |
| Voice | Pipecat, Silero, Saaras v3, GPT-4o, Bulbul v3 | Provider contracts, direct local LLM/ASR/TTS labs, then model serving |
| Conversation | Seven deterministic stages, state-machine facade, durable latest checkpoint adapter, extraction, time resolver, speech safety | Direct Transformers/text console and language lab present; 8B hardware/native review and structured safety audit pending |
| Data | PostgreSQL schema, Alembic, repositories, booking locks/constraints, bounded pools/process budget, checkpoint/model/benchmark schema, turn-language metadata and versioned resource API | Live booking and restoration pending |
| Transient state | Redis checkpoints, leads, status, caches and recording queue; PostgreSQL now has compatible latest-checkpoint restore by call SID | Bounded pressure, redial/session mapping and measured rate limits |
| Webhooks | Atomic `/answer` receipt/call/event acceptance | Extend only to required callback kinds with explicit replay policy |
| Jobs | Recording/spool handoff, PostgreSQL ledger, Dramatiq/Redis execution | Publication bounds, retention, audited replay and privacy controls |
| Knowledge | Approved static facts | Conditional governed RAG with local embeddings/pgvector at Level 9 |
| Deployment | Local scripts and pre-v4 infrastructure Compose | Non-container local learning first; full deployment packaging at Level 13 |

The database booking repository is implemented, but the live controller still uses the static/read-only calendar and conversational `locked_slot`. A spoken win is not yet proof of a committed appointment. Schema tables also do not prove all corresponding runtime writes exist.

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

## Level 2 local text lab

Section 11 adds the direct Transformers provider, console turn traces and 40-case multilingual native-review workflow. Run `make local-llm-sync`, then `make text-console` for the offline mock demo. On a suitable host, use `LLM_PROVIDER=qwen3_transformers make text-console`. See [the local LLM guide](docs/25-local-transformers-llm.md) for devices/dtypes, metrics and language scoring. The current 8 GiB Mac cannot admit Qwen3-8B; real 8B inference/native quality approval remain pending. Live replies remain Hinglish.

## Code map

`roma/main.py` composes `api`, `services`, `domain`, `repositories`, `providers`, `realtime` and `workers`. Business rules live in the domain; provider SDKs and storage remain at the edges. Runtime prompts live in `roma/prompts/` and affect speech directly. Read [architecture](docs/01-architecture.md) before changing those boundaries.
