# Decisions and operational lessons

## Locked architecture

- Telephony is Twilio Programmable Voice with bidirectional Media Streams.
- Twilio invokes `POST /answer`; Roma returns `<Connect><Stream>` for `/ws`.
- HTTP and WebSocket requests validate `X-Twilio-Signature`; the WebSocket start event must
  carry the configured Account SID.
- Pipecat's native `TwilioFrameSerializer` owns the `media`, `clear`, and hang-up protocol.
- Lead metadata is a Twilio Stream custom parameter, never a Stream URL query parameter.
- The service is backend-only. The call and status APIs remain bearer-protected.
- Redis holds call state, lead records, opener audio, status, and post-call work.
- The pre-call gate and pre-TTS guard are mandatory and fail closed where safety requires it.

## Lessons that still constrain the implementation

- Store the lead before calling Twilio; a fast pickup can race any later Redis write.
- Validate the public base URL before consuming the hourly dial allowance.
- A status-store failure after Twilio accepts a call must not report that successful dial as
  an HTTP 500. A lead-store failure before dialing must stop the dial and report our outage.
- Do not derive identity from a missing Redis record. Presence of the Twilio custom lead
  parameter establishes outbound direction even when Redis is temporarily unavailable.
- Barge-in must emit Twilio `clear` before later queued audio reaches the carrier.
- Logging must be configured before the media app so call evidence and secret redaction are
  both active.
- Cloudflare tunnels are development scaffolding, not architecture. Production needs a
  stable public HTTPS/WSS origin near callers.
- Automated tests and the synthetic media probe never place a live call.

## Safety and compliance

- Never log credentials, phone numbers, or lead tokens.
- Never commit `.env` or a test destination.
- The outbound API permits only valid Indian mobile numbers and retains the calling window,
  denylist, spend cap, hourly cap, reachability preflight, and bearer authentication.
- Recording remains gated by approved consent wording and retention controls.
- Claims about course guarantees, money, certification, and outcomes stay behind the
  existing deterministic guardrails.
