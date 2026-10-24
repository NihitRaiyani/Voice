# API, identity and job contracts

## Current API versus Level 1 target

Current external paths include `/health`, `/answer`, `/ws`, `/api/call`, `/api/call/{request_uuid}` and the versioned `/api/v1/...` resource API. The call command and v1 resources are bearer-protected; malformed/non-ASCII bearer input returns 401. Resource SQL/native connection-capacity outages map to stable 503 unavailable envelopes. Section 28 now requires JWT-backed Admin/Counsellor/Viewer permissions; static-token machine compatibility is an explicit disabled-by-default option.

L1 now implements `/api/v1` resources for calls/leads/appointments/analytics/safety with stable success/error envelopes, pagination/filter/sort and OpenAPI. Preserve compatibility routes until an explicit tested client migration. Use domain error codes such as `APPOINTMENT_SLOT_UNAVAILABLE`; provider SDK payloads stay internal.

The existing call service validates number/window/denylist/spend and public reachability before taking hourly allowance and contacting Twilio. Store lead metadata before dialing to avoid fast-pickup races. A pre-dial store failure blocks; a cosmetic status failure after accepted dialing must not turn the accepted call into HTTP 500.

## Level 8 identities

Human/client identity, Twilio signature validation and worker credentials are distinct mechanisms. Short-lived JWT access, SQL session checks, refresh rotation/reuse detection, logout revocation, issuer/audience validation and deny-by-default roles are implemented. [The permission matrix](levels/level-08.md#section-28-authentication-and-role-permissions) specifies the Admin/Counsellor/Viewer baseline. [Setup](runbook.md#section-28-human-authentication-setup) requires an explicit secret, migration and Admin bootstrap; no default account is supplied. Institute policy approval and complete recording/admin audit remain acceptance work.

Callback authenticity already exists. Answer idempotency already atomically commits receipt/call/event; it does not reject all old signed requests by timestamp. Retention/replay policy must match Twilio's actual callback capabilities, and new event kinds need their own stable identity.

## Job contract

Recording transport remains Redis/local spool with one consumer. It performs permitted file handling, commits call/job intents in PostgreSQL and only then acks. Dramatiq/Redis sends job IDs; targeted row claims and effect/settlement transactions protect redelivery. See [worker framework](19-background-task-framework.md).

Keep payload version, identity, timeout, attempts, available time, lease and reason visible. `compute_statistics`, `summarize_call` and `update_lead` run today. Section 26 preserves deterministic default summaries and adds explicit opt-in capped Claude summaries on a lower-priority queue. `send_followup` can execute only with persisted consent and an injected idempotent sender; none is configured by default. Scheduling does not authorize contact or paid AI.

Sections 26–28 extend supervised jobs, advisory webhook leases, human security and shared auth rate limits. Broader L8 publication/dead-letter retention, authorized replay, access auditing and PII/deletion remain pending. A queue framework alone does not complete those controls.

See [security](07-security.md), [webhook walkthrough](18-webhook-idempotency.md), [runbook](runbook.md) and [L8](levels/level-08.md).
