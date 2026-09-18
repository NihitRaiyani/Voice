# Roma — Twilio Voice Agent Backend

Roma is a backend-only voice agent for Weltec Institute. It places and receives calls through
Twilio Programmable Voice, streams audio bidirectionally through Pipecat, transcribes with
Sarvam Saaras, reasons with OpenAI, and speaks through Sarvam Bulbul.

```text
Twilio → Pipecat/Silero → Saaras STT → OpenAI → pre-TTS guard → Bulbul TTS → Twilio
                         ↕
              Redis state, cache, status, and post-call queue
```

## Configuration

Copy `.env.example` to the ignored local `.env` and set at least:

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

Never commit credentials or test destination numbers. Configure the Twilio phone number's
Voice webhook as `POST <PUBLIC_BASE_URL>/answer`. The answer route returns
`<Connect><Stream>` TwiML for `wss://.../ws`; both callbacks validate
`X-Twilio-Signature`.

## Run

```bash
uv sync --extra telephony --extra dev
./scripts/start_roma.sh
```

The service exposes:

- `GET /health`
- `POST /answer` and `WS /ws` for Twilio
- `POST /api/call` and `GET /api/call/{request_uuid}` for authenticated backend clients

Example outbound request:

```bash
curl -X POST http://127.0.0.1:8020/api/call \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to_number":"+91XXXXXXXXXX"}'
```

The call API retains the calling-window, denylist, spend, hourly-cap, reachability, and Redis
ordering protections. A live call is never part of automated verification.

## Verify offline

```bash
uv run --extra telephony --extra dev ruff check src tests scripts
uv run --extra telephony --extra dev pytest -q
uv run --extra telephony --extra dev python scripts/verify_media.py
bash -n scripts/start_roma.sh
```

The former React/Vite frontend was removed. This repository is intentionally a pure backend.
Runtime data under `var/roma` contains spend, recording, and job state and must be preserved.
