# Twilio Backend Migration Design

**Date:** 2026-09-17  
**Status:** Approved for planning  
**Scope:** Replace VoBiz with Twilio and remove the frontend. This work does not execute the mentor roadmap.

## Objective

Convert Roma into a backend-only Twilio voice-agent service without changing its conversation pipeline, safety gates, VAD/STT/LLM/TTS behavior, Redis state, recording flow, or business rules.

## Telephony architecture

- Replace all `VOBIZ_*` configuration with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER`.
- Keep credentials only in the ignored local `.env`; `.env.example` contains names and documentation but no values.
- Use the official Twilio Python SDK for outbound calls, TwiML generation, and webhook-signature validation.
- Use Pipecat's maintained `TwilioFrameSerializer` for bidirectional audio instead of maintaining a carrier serializer in this repository.
- Place outbound calls through Twilio's Calls API using the existing `/answer?lead=...` URL. This preserves the current lead-record and pre-rendered-opener flow.
- Return `<Response><Connect><Stream>` TwiML from `/answer` so audio is bidirectional.
- Use nested TwiML `<Parameter>` values to pass the lead token into Twilio's `start.customParameters`. Twilio Stream URLs do not support query parameters.
- Parse Twilio's `start.streamSid`, `start.callSid`, `start.accountSid`, `start.mediaFormat`, and `start.customParameters` fields.
- Validate Twilio signatures at carrier-facing HTTP and WebSocket boundaries and retain the Account SID consistency check as defense in depth.
- Keep automatic REST hang-up enabled in production through `TwilioFrameSerializer`; tests use `auto_hang_up=False`.
- Keep 8 kHz mu-law audio throughout the telephony boundary.

## Backend API

Retain these backend surfaces:

- `POST /api/call` for authenticated, gated outbound call creation.
- `GET /api/call/{id}` for backend call-status polling.
- `POST /answer` for Twilio call instructions.
- `WebSocket /ws` for Twilio Media Streams.
- `GET /health` and existing debug endpoints for operational verification.

The bearer-token protection, calling window, denylist, hourly cap, spend cap, and public-base-URL preflight remain in place. Browser-specific CORS configuration is removed.

## Frontend removal

- Delete the entire `web/` React/Vite application.
- Remove Vite startup, shutdown, environment matching, logs, and UI-opening output from `scripts/start_roma.sh`.
- Remove `WEB_ORIGIN` and CORS middleware.
- Keep `API_TOKEN` because it protects the retained backend call API; rewrite comments that currently describe it as a browser token.
- Remove the frontend-only documentation file and update remaining README/runbook text that instructs the operator to use the UI.
- Keep backend call-status storage and its tests, but rename frontend-specific comments to describe the API consumer instead.

## VoBiz removal

- Delete the custom VoBiz serializer and VoBiz-specific serializer tests.
- Replace the VoBiz REST client with a Twilio client while preserving dependency injection used by tests.
- Replace VoBiz XML generation with TwiML generation.
- Remove VoBiz SIP credentials, API endpoints, answer-field spellings, carrier scripts, tests, and active documentation references.
- Replace synthetic VoBiz protocol verification with Twilio protocol verification.
- Do not resurrect obsolete behavior from the earlier Twilio implementation. Reuse only patterns that still match current Twilio and Pipecat documentation.

## Runtime data (`var/`)

`var/` is ignored runtime state, not frontend source. Preserve:

- `var/roma/spend.jsonl` because it enforces the cumulative test budget.
- Existing recordings and post-call jobs because they may contain required operational evidence or PII.
- Backend and tunnel logs useful for diagnosis.

Remove only frontend-specific runtime artifacts such as `var/run/vite.log`. Do not inspect or publish recording contents.

## Credential handling

- Write the provided Account SID, Auth Token, and Twilio caller number only to `.env`.
- Never place the personal test destination in source code, `.env.example`, tests, or documentation.
- Redact all Twilio secrets from application and Uvicorn logs.
- Rotate the Auth Token after migration because it was shared in chat, then update only the local `.env`.

## Error handling

- Missing Twilio credentials disable outbound dialing with a clear configuration error rather than breaking unrelated imports.
- Invalid Twilio signatures are rejected before pipeline construction.
- Malformed or incomplete Stream start messages close the socket with a policy error.
- Twilio API failures remain sanitized at the backend boundary so credentials and account paths never reach callers or logs.
- Existing Redis degradation and call-finalization behavior remain unchanged.

## Verification

Offline verification must cover:

- TwiML shape, escaping, custom parameters, and bidirectional Stream URL.
- Outbound Twilio Calls API arguments and returned Call SID.
- Stream start parsing and Account SID validation.
- Twilio media serialization, clear/interruption behavior, and automatic-hang-up configuration.
- Webhook/WebSocket signature rejection and acceptance.
- Retained backend call API gates and status responses.
- Absence of VoBiz/SIP/frontend references in active code and configuration.
- Full unit test suite and Ruff checks.

No paid live call is placed during implementation verification. A live call to the user-provided test destination requires a separate explicit instruction.

## Success criteria

1. The repository contains no active VoBiz or VoBiz SIP integration.
2. The repository contains no React/Vite frontend or frontend startup path.
3. The service can create a gated Twilio call and return valid bidirectional Media Streams TwiML.
4. The media pipeline uses Pipecat's Twilio serializer without changing downstream conversation behavior.
5. All secrets remain untracked and redacted.
6. Offline tests and lint pass.

## Primary references

- Twilio Call resource: <https://www.twilio.com/docs/voice/api/call-resource>
- Twilio Media Streams: <https://www.twilio.com/docs/voice/media-streams>
- Twilio `<Stream>` TwiML: <https://www.twilio.com/docs/voice/twiml/stream>
- Twilio WebSocket messages: <https://www.twilio.com/docs/voice/media-streams/websocket-messages>
- Pipecat `TwilioFrameSerializer`: <https://docs.pipecat.ai/api-reference/server/services/serializers/twilio>
