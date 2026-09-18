# Current handoff

Roma now uses Twilio Programmable Voice end to end.

- Outbound calls use the official Twilio Calls API and point to `/answer` with POST.
- `/answer` validates `X-Twilio-Signature` and returns bidirectional
  `<Connect><Stream>` TwiML.
- `/ws` validates the Twilio signature before accepting, verifies the Account SID from the
  start event, reads lead metadata from `customParameters`, and uses Pipecat's native
  `TwilioFrameSerializer`.
- The backend call and status APIs remain bearer-protected with the existing safety gates.
- The React/Vite frontend and browser CORS support were removed.
- Offline verification does not place a live call.

Required carrier settings are `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and
`TWILIO_FROM_NUMBER`. Configure the Twilio number's Voice webhook to
`POST <PUBLIC_BASE_URL>/answer`.

Before any live test, confirm the public `/health` endpoint, API token, calling window,
denylist, spend budget, hourly limit, Redis, and signed Twilio webhook configuration. Use a
test handset only and obtain explicit operator approval. Never commit that destination.

The Auth Token previously shared in chat must be rotated in the Twilio Console and updated
only in the ignored local `.env`.
