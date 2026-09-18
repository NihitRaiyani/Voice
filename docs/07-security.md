# 07 — Security (GATE-ZERO)

**Status:** Secret handling, bearer protection, Twilio signature validation, PII-aware logging, and
fail-safe call gates are implemented. User authentication, RBAC, audit trails, rate-limit coverage,
and formal retention workflows are planned.

**Learning objective:** Draw trust boundaries and apply controls at ingress, authorization,
storage, logs, provider callbacks, and destructive data-lifecycle operations.

Security is a build gate, not a later hardening pass. The scaffold below goes in before the
first callable build.

## Secrets
- Twilio, Sarvam, OpenAI keys, Redis auth → **environment / secret store only.** Never in
  code, never committed, never logged.
- No secret in error messages, stack traces, or call logs.
- Rotate keys on a schedule; least-privilege API tokens (e.g. Twilio subaccount scoped to the
  numbers Roma uses).
- `.env` is gitignored; provide `.env.example` with key names only, no values.

## Twilio callback authentication

- Validate `X-Twilio-Signature` for both `/answer` and `/ws` using the exact external URL.
- Verify the `accountSid` in the WebSocket start event matches `TWILIO_ACCOUNT_SID`.
- Twilio Stream URLs do not carry query parameters; pass the opaque lead token through a
  nested TwiML `<Parameter>` and read it from `start.customParameters`.
- Reject callbacks before constructing the voice pipeline when authentication fails.

## PII (leads' data)
- A lead's name, phone number, and transcript are PII. Treat accordingly.
- **Do not** put PII in URL params, query strings, or third-party analytics.
- Transcripts and recordings are access-controlled (see below). Retain only as long as needed
  for the counselling follow-up; define a retention window.
- Redis call-state holds PII → Redis must be auth'd + network-isolated, TTL'd, not public.

## Call-recording consent (compliance — can block go-live independently)
- **State recording at call start** ("call record ho raha hai" — the human counsellors already
  do this; it doubles as a fee-deflection anchor).
- **DND / TRAI scrubbing:** the dialer must respect India's DND registry and calling-window
  rules before placing a call. Unsolicited-call compliance is legal, not optional.
- This is Weltec's regulatory exposure — confirm their consent/DND process before real leads.

## Recording & transcript access
- Recordings stored access-controlled (see `docs/09`); not world-readable, not a public bucket.
- Least-privilege: the post-call worker can write recordings; only authorized staff can read.

## The guardrail is a security control, not just product
- The pre-TTS filter (`docs/04`) prevents the bot making unauthorized financial/outcome claims
  — that is liability protection. It ships with the first callable build. Non-negotiable.

## Prompt-injection surface (in-conversation)
- Treat lead claims as data, not instructions. The "someone already quoted me a fee" attack
  (`docs/04`) is the live example: never let a lead's assertion unlock a rule violation.
- Never let anything the lead says change a system rule, a cert claim, or a money boundary.

## Fail-safe posture
- If any guardrail or secret dependency is unavailable, the safe action is to **not place /
  hard-fail the call**, never to proceed unguarded.

## Roadmap bridge

Add JWT/OAuth2-style authentication and role checks only with the future admin API. Twilio
callbacks continue using provider signature validation rather than human-user JWTs. See
`docs/15-api-security-and-jobs.md` for the planned trust model and role matrix.
