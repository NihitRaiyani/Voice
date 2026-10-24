# Security, privacy and production gates

## Existing trust boundaries

Validate `X-Twilio-Signature` against the exact configured external HTTP/WebSocket URL and verify start-event Account SID before constructing media. Human/client bearer authentication is distinct from carrier signature validation and worker access. Malformed/non-ASCII bearer credentials fail authentication with 401 before any resource/provider action. The current service has no frontend. Section 28 adds SQL-backed JWT sessions and role checks; the old static bearer is an explicit compatibility option, disabled by default. See [the implemented access contract](levels/level-08.md#section-28-authentication-and-role-permissions).

Keep secrets in environment-backed secret settings, hide SQL parameters and redact errors/logs. Full phone numbers, transcripts and authorization tokens do not belong in ordinary logs, metrics or fixtures. `var/roma`, Redis dumps and credentials remain ignored and access-controlled.

Stream metadata carries the lead token via TwiML `<Parameter>` and `start.customParameters`, not the Stream URL. **The current outbound answer callback URL does contain `?lead=...`.** Treat it as sensitive authorization data, suppress/redact proxy/access-log URLs and evaluate that exposure at L8; do not claim tokens never appear in any URL.

## Dial safeguards and limits

The current API validates Indian mobiles, calling window (09:00–21:00 IST), configured denylist, Claude spend, public reachability and hourly cap (default 20), with bearer auth first. An empty denylist blocks nothing; no real DND/DLT feed is implied. `ANTHROPIC_BUDGET_INR` defaults to ₹200 as a configured ceiling, not remaining provider credit; Sarvam readiness/credits are not covered by it.

Owner-approved contact/recording policy is required before real lead/production traffic. These code defaults do not establish legal clearance. Pending recording wording/audio causes capture discard rather than retained storage.

## Level 8 access and remaining security work

Section 28 implements user identity, issuer/audience-validated JWT expiry, rotating refresh-family revocation and deny-by-default roles. Operational secret/account setup and institute approval remain explicit. Recording access auditing, complete privacy/deletion and callback freshness are still later work. Keep callback signing separate and define replay behavior using actual provider capabilities; Twilio signatures alone do not impose a timestamp freshness window.

Current `/answer` already has atomic durable receipt/call/event acceptance. Extend identity/lease/completion semantics only to required callbacks. A processing claim cannot mark an effect completed before its transaction commits. Signature verification and idempotency solve distinct problems.

Use shared rate limits where deployment requires them. Encrypt recoverable sensitive fields; use keyed phone hashing for identity matching rather than enumerable plain hashes. `PII_HASH_KEY` supports outbound caller linkage today, not a claim of complete encrypted storage.

## Recordings and retention

Approved disclosure wording, matching audio and the institute's consent/retention policy precede retention. Source retention default is 90 days; local pruning exists, but complete raw/spool/database/backup deletion and audit remain future controls. Use authorized expiring playback access and audit reads/deletes; no public recording links.

CI uses synthetic/redacted fixtures and fake paid providers. L12 adds security scans and final-local-profile enforcement. L13 injects secrets at runtime, excludes PII/model weights from images and verifies backup/rollback. See [decisions](decisions.md), [recordings](09-recording-storage.md) and [API tutorial](15-api-security-and-jobs.md).


## Section 12 prompt and safety boundary

The [safety contract](04-guardrails.md#untrusted-inputs-and-limits) defines bounded caller/slot characters, length and enum validation, instruction-override canaries and the final deterministic output gate. Rejected utterances never become live model history. Structured events omit raw text and preserve call/turn correlation; API serialization exposes only approved audit metadata. Database write failures produce a safe line and an explicit structured failure signal. This is not complete semantic injection prevention, universal PII detection or complete semantic/production security acceptance. Section 28 separately adds user authorization.
