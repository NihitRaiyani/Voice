# Roma — Voice Agent Backend and Backend-Engineering Lab

Roma is a backend-only multilingual counselling and visit-booking voice agent for Weltec
Institute. It is also a learning project for mastering production backend engineering through a
real-time system rather than a collection of disconnected CRUD exercises.

The current system places and receives calls with Twilio Programmable Voice, moves audio through
Pipecat, detects speech with Silero, transcribes with Sarvam Saaras, generates constrained replies
with OpenAI, and speaks with Sarvam Bulbul.

```text
Twilio -> Pipecat/Silero -> Saaras STT -> conversation controller -> OpenAI
                                                               -> pre-TTS guard -> Bulbul TTS
       <- bidirectional media stream <-----------------------------------------+

Redis: active-call state, caches, call status, spend controls, and post-call queue
```

## Two goals, one repository

- **Product goal:** make safe, low-latency calls that move a prospective student toward a
  confirmed institute visit.
- **Learning goal:** understand state machines, persistence, transactions, concurrency,
  authentication, queues, observability, testing, resilience, API design, and delivery well
  enough to explain and defend the design in a backend interview.

The voice pipeline is the implemented baseline. PostgreSQL, SQLAlchemy, Alembic, appointment
locking, JWT/RBAC, durable workers, OpenTelemetry, Prometheus, Grafana, Docker, and CI/CD are
roadmap targets—not current features. See [the documentation map](docs/README.md) and
[the backend roadmap](docs/13-backend-roadmap.md).

## Current capabilities

- Signed Twilio HTTP and WebSocket callbacks with bidirectional Media Streams.
- Authenticated outbound-call and call-status APIs.
- Pre-call calling-window, denylist, spend, rate, reachability, and configuration gates.
- Seven-stage software-owned conversation state machine.
- Deterministic multilingual pre-TTS safety filtering.
- Barge-in, per-call isolation, Redis state, prompt caching, and post-call recording jobs.
- Offline unit, integration-style, media, safety, and evaluation tests that do not place calls.

## Configuration

Copy `.env.example` to the ignored local `.env` and set the required values. Never commit
credentials or destination numbers.

```dotenv
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
SARVAM_API_KEY=
OPENAI_API_KEY=
REDIS_URL=
PUBLIC_BASE_URL=https://your-public-host.example
API_TOKEN=
```

Configure the Twilio number's Voice webhook as `POST <PUBLIC_BASE_URL>/answer`. The route returns
`<Connect><Stream>` TwiML for `wss://.../ws`; both callbacks validate `X-Twilio-Signature`.

## Run the current backend

```bash
uv sync --extra telephony --extra dev
./scripts/start_roma.sh
```

Current endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Process health |
| `POST /answer` | Signed Twilio voice webhook |
| `WS /ws` | Signed bidirectional Twilio media stream |
| `POST /api/call` | Authenticated outbound-call request |
| `GET /api/call/{request_uuid}` | Authenticated call-status lookup |

```bash
curl -X POST http://127.0.0.1:8020/api/call \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to_number":"+91XXXXXXXXXX"}'
```

This command can place a real paid call. Use only an approved test handset after the offline gate.

## Verify offline

```bash
uv run --extra telephony --extra dev ruff check src tests scripts
uv run --extra telephony --extra dev pytest -q
uv run --extra telephony --extra dev python scripts/verify_media.py
bash -n scripts/start_roma.sh
```

Automated verification must never place a live call. Runtime data under `var/roma` contains
spend, recording, and job state and must be preserved. This repository intentionally has no web
frontend.

## Suggested reading path

1. [Project and learning charter](docs/00-project-charter.md)
2. [Current architecture](docs/01-architecture.md)
3. [Pipeline and latency](docs/02-pipeline.md)
4. [Conversation state machine](docs/03-phase-machine.md)
5. [Safety guardrails](docs/04-guardrails.md)
6. [Four-level backend roadmap](docs/10-build-order.md)
7. [Placement study guide](docs/17-placement-study-guide.md)
