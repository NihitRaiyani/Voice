# Roma engineering context

Roma is a backend-only Twilio voice agent for Weltec Institute. Preserve the existing
conversation phases, guardrails, recording behavior, and post-call workflow unless a task
explicitly changes them.

## Locked stack

| Layer | Choice |
|---|---|
| Telephony | Twilio Programmable Voice + bidirectional Media Streams |
| Orchestration | Pipecat |
| VAD | Silero |
| STT | Sarvam Saaras |
| LLM | OpenAI |
| TTS | Sarvam Bulbul |
| State/cache/queue | Redis |

## Non-negotiable boundaries

1. Every outbound call passes the pre-call gates before Twilio is contacted.
2. Every spoken generated line passes the pre-TTS guard.
3. Twilio HTTP and WebSocket callbacks validate `X-Twilio-Signature`.
4. Lead tokens are never logged and travel as Twilio Stream custom parameters.
5. Credentials and destination phone numbers never enter tracked files.
6. Automated verification never places a live call.
7. Preserve `var/roma` runtime data.

The React/Vite frontend was removed. `POST /api/call` and
`GET /api/call/{request_uuid}` remain protected backend endpoints for programmatic clients.
Read `docs/10-build-order.md` and `docs/decisions.md` before changing core behavior.
