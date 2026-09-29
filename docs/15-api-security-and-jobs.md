# 15 — API, Security, and Background Jobs Tutorial

**Status:** Twilio and bearer-protected call endpoints and the post-call job foundation are
implemented. The versioned admin API and human authentication/RBAC are planned.

## Versioned REST shape

Suggested resources:

```text
POST   /api/v1/calls
GET    /api/v1/calls
GET    /api/v1/calls/{id}
GET    /api/v1/leads
GET    /api/v1/leads/{id}
GET    /api/v1/appointments
POST   /api/v1/appointments
PATCH  /api/v1/appointments/{id}
DELETE /api/v1/appointments/{id}
GET    /api/v1/analytics/overview
GET    /api/v1/safety/events
```

Use Pydantic request/response schemas, documented pagination/filter/sort parameters, consistent
HTTP status codes, and stable domain error codes such as `APPOINTMENT_SLOT_UNAVAILABLE`. Do not
expose provider SDK payloads as the public contract.

## Authentication versus provider verification

These are separate trust boundaries:

- **Humans/backend clients:** tokens establish an application identity and permissions.
- **Twilio callbacks:** `X-Twilio-Signature` proves the request was signed for the expected URL.
- **Internal workers:** job identity and scoped service credentials authorize specific work.

Never replace Twilio signature validation with a user JWT or treat a caller-supplied Call SID as
proof of identity.

## Role model

| Operation | Admin | Counsellor | Viewer |
|---|---:|---:|---:|
| View calls | Yes | Yes | Yes |
| Listen to recordings | Yes | Yes | No |
| Manage appointments | Yes | Yes | No |
| Change safety rules | Yes | No | No |
| Manage users | Yes | No | No |
| View analytics | Yes | Yes | Yes |

Authentication work includes password hashing, short-lived access tokens, refresh/revocation
policy, route-level permission checks, and tests for both allowed and forbidden operations.

## Webhook security and idempotency

Provider delivery is at-least-once: the same event may arrive twice. The backend must produce one
business effect.

```text
delivery 1 -> verify -> claim provider_event_id -> apply effect -> record success
delivery 2 -> verify -> provider_event_id already claimed -> acknowledge, no second effect
```

Use a unique database constraint for durable event identity or an atomic Redis `SET ... NX` for an
appropriately short-lived claim. Signature validation, replay-window checks, strict payload
validation, expected content types, and secret-safe failure logs remain required.

## Background-job lifecycle

```text
call ends -> Redis queue (local spool fallback)
          -> recording worker stores audio if captured
          -> one PostgreSQL transaction commits call + job intents
          -> Redis message acknowledged

PostgreSQL worker -> SELECT ... FOR UPDATE SKIP LOCKED -> execute
                  -> effect + succeeded status in one transaction
                  -> retry with backoff or dead_letter on failure
```

The existing `followup_jobs` table is the physical job ledger. Each post-call intent has a
versioned payload, call reference, unique idempotency key, status, attempts, due time, and
lease owner/time. Repeated messages cannot create duplicate calls or jobs. An expired
five-minute lease can be reclaimed after a worker crash. The active job types are
`compute_statistics`, `summarize_call`, and `update_lead`. The recording worker also
records `process_recording` status for captured audio. A `send_followup` intent is
scheduled one hour after the call ends when a visit time was captured; no messaging worker claims it yet:
phone contact and messaging consent are a separate task.

Statistics use duration, captured audio seconds, and turn count. The current summary is
deterministic metadata, not an LLM-generated transcript summary. Outbound lead updates
use a keyed phone hash to link to `callers`; an unlinked caller makes the job
`dead_letter` with `caller_not_linked` so the missing effect stays visible. Redis remains
the transient recording transport; PostgreSQL stores call history and job status.

Recording retries use a bounded delay and then Redis/local-spool dead-letter storage.
PostgreSQL jobs have a 60-second execution timeout and retry up to five attempts with
exponential backoff capped at five minutes.
Do not run multiple recording-worker processes: its startup recovery is designed for one
consumer. The PostgreSQL worker can run in multiple processes because claims use row locks.

No Celery, Dramatiq, or ARQ dependency was needed: the existing Redis transport and
PostgreSQL job table cover this project's current volume and transactional requirements.

## Privacy and auditability

- Hash phone numbers where full plaintext lookup is unnecessary.
- Mask PII in logs and analytics dimensions.
- Encrypt selected sensitive fields and use signed, expiring recording access.
- Define transcript/recording retention and deletion or anonymization workflows.
- Record who changed appointments, roles, safety rules, and retention decisions.
- Never let logs become an uncontrolled secondary PII database.

## Required exercises

1. Produce an OpenAPI contract with success and error examples.
2. Prove each RBAC denial with an automated test.
3. Deliver the same webhook/job twice and show one business effect.
4. Crash a worker at the acknowledgement boundary and explain recovery.
5. Trace one PII field from ingress through storage, logs, response, retention, and deletion.
