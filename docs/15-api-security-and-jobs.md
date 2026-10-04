# API, identity and job contracts

## Current API versus Level 1 target

Current external paths include `/health`, `/answer`, `/ws`, `/api/call`, `/api/call/{request_uuid}` and the versioned `/api/v1/...` resource API. The call command and v1 resources are bearer-protected; this is not an Admin/Counsellor/Viewer identity system.

L1 now implements `/api/v1` resources for calls/leads/appointments/analytics/safety with stable success/error envelopes, pagination/filter/sort and OpenAPI. Preserve compatibility routes until an explicit tested client migration. Use domain error codes such as `APPOINTMENT_SLOT_UNAVAILABLE`; provider SDK payloads stay internal.

The existing call service validates number/window/denylist/spend and public reachability before taking hourly allowance and contacting Twilio. Store lead metadata before dialing to avoid fast-pickup races. A pre-dial store failure blocks; a cosmetic status failure after accepted dialing must not turn the accepted call into HTTP 500.

## Level 8 identities

Human/client identity, Twilio signature validation and worker credentials are distinct mechanisms. Add short-lived tokens/session expiry, refresh/revocation, issuer/audience checks and deny-by-default roles. Suggested roles are Admin, Counsellor and Viewer; institute approval determines final permissions. Recording access/admin changes must be audited.

Callback authenticity already exists. Answer idempotency already atomically commits receipt/call/event; it does not reject all old signed requests by timestamp. Retention/replay policy must match Twilio's actual callback capabilities, and new event kinds need their own stable identity.

## Job contract

Recording transport remains Redis/local spool with one consumer. It performs permitted file handling, commits call/job intents in PostgreSQL and only then acks. Dramatiq/Redis sends job IDs; targeted row claims and effect/settlement transactions protect redelivery. See [worker framework](19-background-task-framework.md).

Keep payload version, identity, timeout, attempts, available time, lease and reason visible. `compute_statistics`, `summarize_call` and `update_lead` run today; summaries are deterministic metadata. `send_followup` is scheduled but excluded from eligible execution. Scheduling does not authorize SMS/contact or paid AI.

L8 completes bounded publication/dead-letter retention, authorized replay, user security, shared rate limits and PII/access/deletion. A queue framework alone does not complete those controls.

See [security](07-security.md), [webhook walkthrough](18-webhook-idempotency.md), [runbook](runbook.md) and [L8](levels/level-08.md).
