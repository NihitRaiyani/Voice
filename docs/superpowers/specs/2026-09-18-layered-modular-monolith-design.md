# Layered Modular Monolith Design

**Date:** 2026-09-18

## Objective

Refactor Roma into a clean backend-only modular monolith that a new developer can navigate from
HTTP/Twilio entrypoint to use case, domain rule, repository, provider, and worker without searching
through unrelated responsibilities.

This is a behavior-preserving structural refactor. It does not add PostgreSQL, authentication,
new APIs, or other future roadmap features.

## Architectural principles

1. **Dependency direction:** API, providers, repositories, and workers depend on application/domain
   modules. Domain rules do not import FastAPI, Twilio, Redis, Pipecat, or provider SDKs.
2. **Thin routes:** HTTP and WebSocket modules validate transport input, call a use case, and map
   domain results to transport responses.
3. **Business logic outside routes:** calling gates, conversation transitions, booking rules,
   safety rules, and cost rules live outside route handlers.
4. **Repository seams:** persistence operations are named by business intent and isolated from use
   cases. Redis remains the current adapter; future PostgreSQL adapters can be added deliberately.
5. **Provider seams:** Twilio, Sarvam, OpenAI, and Google integration details stay behind narrow
   provider modules. Pipecat coordination remains in the realtime layer.
6. **Realtime isolation:** frame processing, VAD, interruption, audio rendering, and latency-critical
   orchestration are not mixed with ordinary API or post-call work.
7. **No decorative layers:** a directory is created only when real code owns that responsibility.

## Target repository structure

```text
roma/
|-- __init__.py
|-- main.py
|-- api/
|   |-- dependencies.py
|   `-- v1/
|       |-- calls.py
|       |-- health.py
|       `-- twilio_webhooks.py
|-- core/
|   |-- config.py
|   |-- exceptions.py
|   |-- logging.py
|   `-- security.py
|-- domain/
|   |-- calls/
|   |-- conversation/
|   |-- appointments/
|   |-- safety/
|   `-- costs/
|-- services/
|   |-- call_service.py
|   |-- conversation_service.py
|   `-- recording_service.py
|-- repositories/
|   |-- interfaces/
|   `-- redis/
|-- providers/
|   |-- telephony/twilio/
|   |-- stt/sarvam/
|   |-- llm/openai/
|   |-- tts/sarvam/
|   `-- calendar/google/
|-- realtime/
|   |-- pipeline.py
|   |-- audio/
|   `-- turn_taking/
|-- workers/
|   `-- postcall/
|-- eval/
`-- prompts/

tests/
|-- api/
|-- core/
|-- domain/
|-- services/
|-- repositories/
|-- providers/
|-- realtime/
`-- workers/
```

The package moves from `src/roma/` to the requested top-level `roma/`. `pyproject.toml`, scripts,
and test discovery will be updated so imports remain `roma.*`.

## Current-to-target ownership map

| Current location | Target ownership |
|---|---|
| `config.py`, `logging_setup.py` | `core/config.py`, `core/logging.py` |
| `guardrails/` | `domain/safety/` |
| `controller/` business rules | `domain/conversation/` and `domain/appointments/` |
| `dialer/` gates and models | `domain/calls/`, `services/call_service.py`, Redis repositories |
| Twilio client/auth/TwiML | `providers/telephony/twilio/` |
| Sarvam/OpenAI construction | provider packages for STT, TTS, and LLM |
| Pipecat/media/turn-taking | `realtime/` |
| `postcall/` queue/storage execution | repositories plus `workers/postcall/` |
| Google Calendar adapter | `providers/calendar/google/` |
| `spend.py` | `domain/costs/` plus persistence adapter where needed |
| `eval/`, `prompts/` | remain first-class package areas |

## Dependency rules

```text
api ----------> services ----------> domain
                   |                   ^
workers ---------->|                   |
                   v                   |
             repository interfaces ---+
                   ^
                   |
      Redis/database adapters

realtime ------> services/domain
realtime ------> provider interfaces
provider adapters -> external SDKs
```

- Domain modules contain plain Python types and rules.
- Services coordinate a complete use case and transaction/order of operations.
- Repository interfaces describe persistence needs; adapters implement them.
- Provider adapters translate external SDK inputs, outputs, and failures.
- API and worker entrypoints assemble dependencies at the edge.

## Migration method

The refactor will be incremental rather than one uncontrolled move:

1. Move the package root and update packaging/import discovery.
2. Move core configuration and logging.
3. Move domain modules without changing behavior.
4. Separate API routes from Twilio/realtime orchestration.
5. Establish provider packages and move SDK-specific code.
6. Establish repository interfaces/adapters around existing Redis stores.
7. Move post-call execution into workers.
8. Mirror the test tree and update documentation.

Each step must keep imports valid and run its focused tests. The complete suite runs at the end.
Compatibility re-export modules may be used briefly during a step, then removed before completion.

## Environment file

Create a root `.env` that remains ignored by Git. Populate it without printing values by:

- carrying forward the existing non-obsolete OpenAI, Sarvam, Redis, API, runtime, and tuning values;
- adding the supplied Twilio Account SID, Auth Token, and Twilio phone number;
- removing VoBiz, SIP, Vite, frontend, and browser-origin variables;
- adding defaults from `.env.example` only where no existing value is available;
- never adding the personal destination number as an application default.

The committed `.env.example` remains value-free and documents every supported key.

## Error and failure behavior

- External SDK exceptions are translated at provider adapters.
- Domain modules expose stable domain outcomes/errors, not SDK exceptions.
- Routes map domain outcomes to HTTP/WebSocket behavior.
- Existing fail-closed pre-call, pre-TTS, signature, PII, recording, and cost protections remain.
- No paid call or provider request is introduced into automated verification.

## Verification

- Import smoke test for every top-level package.
- Ruff over `roma`, `tests`, and `scripts`.
- Existing focused tests after every migration step.
- Full offline suite must remain at least `1358 passed` with no new failures.
- `rg` confirms no active imports from `src.roma`, obsolete paths, VoBiz runtime modules, or frontend.
- `.env` exists locally, is ignored, and is never printed or committed.
- Documentation tree and startup commands match the final layout.

## Completion criteria

A first-time developer can locate:

- an API route under `roma/api/`;
- a business rule under `roma/domain/`;
- a use case under `roma/services/`;
- persistence under `roma/repositories/`;
- an external integration under `roma/providers/`;
- latency-critical processing under `roma/realtime/`;
- asynchronous post-call work under `roma/workers/`.

All existing behavior and safety guarantees remain verified.
