# 15 — API, Security, and Background Jobs Tutorial

**Status:** Current Twilio and bearer-protected call endpoints are implemented. The versioned admin
API, human authentication/RBAC, and durable job architecture are planned.

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
call ends
   +-> commit call outcome
   +-> enqueue recording job
   +-> enqueue summary/analytics job
   `-> enqueue follow-up job

worker: claim -> execute -> persist effect -> acknowledge
                  | failure
                  +-> retry with backoff -> dead-letter/attention after limit
```

Every job needs an identity, payload schema/version, status, attempt count, timeout, retry policy,
idempotency key, and observable failure. A retry must be safe after a worker crashes between the
external effect and acknowledgement.

Celery, Dramatiq, or ARQ are possible implementations, but choose only after defining the required
semantics and fit with the existing async model. The queue library does not create idempotency.

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
