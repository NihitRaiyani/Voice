# 07 — Security (GATE-ZERO)

Security is a build gate, not a later hardening pass. The scaffold below goes in before the
first callable build.

## Secrets
- Vobiz, Sarvam, OpenAI keys, Redis auth → **environment / secret store only.** Never in
  code, never committed, never logged.
- No secret in error messages, stack traces, or call logs.
- Rotate keys on a schedule; least-privilege API tokens (e.g. Vobiz subaccount scoped to the
  numbers Roma uses).
- `.env` is gitignored; provide `.env.example` with key names only, no values.

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
